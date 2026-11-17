"""Local proposals for upright, printed, numbered worksheet answer regions.

Tesseract is used only to locate printed numbers and prompt words. This module
does not transcribe answers, identify blanks, score work, or change any files in
the application. Every rectangle is an unapproved proposal. Rules and generous
padding help retain handwriting, but teachers must check for clipped strokes,
crossed-out work, neighbouring print, and answers written outside the spaces.

The deliberately limited first format supports numeric question IDs (also qN
and unambiguous section prefixes such as B_21), inline rules, and spaces below
short prompts, in one or two columns. Rotation, tables, repeated numbers, and
complex layouts require manual selection. No language model or network is used.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import csv
import math
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import time
import unicodedata
import warnings as image_warnings

from PIL import Image, UnidentifiedImageError


DETECTOR_VERSION = "printed-regions-1.0.0"
MAX_PAGES = 10
MAX_QUESTIONS = 100
MAX_PIXELS = 25_000_000
MAX_TOTAL_PIXELS = 100_000_000
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_OCR_EDGE = 2200
MAX_TSV_BYTES = 4 * 1024 * 1024
MAX_WORDS = 20_000
PAGE_TIMEOUT_SECONDS = 20
TOTAL_TIMEOUT_SECONDS = 75


class RegionDetectionError(RuntimeError):
    """A safe, actionable detector failure; never an empty student response."""


@dataclass(frozen=True)
class _Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    line: tuple[int, int, int]

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h


@dataclass(frozen=True)
class _Rule:
    x: int
    right: int
    y: int
    bottom: int


@dataclass
class _Anchor:
    number: int
    word: _Word
    strong: bool
    question: dict | None = None
    matched_words: list[_Word] | None = None
    flags: list[str] | None = None


def _tokens(text: str) -> list[str]:
    # This normalization applies ONLY to printed prompt matching, never answers.
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                    if not unicodedata.combining(c))
    return re.findall(r"[a-z]{2,}", plain)


def _numeric_alias(identifier: str) -> int | None:
    match = re.fullmatch(r"(?:[A-Za-z]{1,8}[-_]?)?([0-9]{1,4})", identifier)
    return int(match[1]) if match else None


def _validate_inputs(pages, questions):
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise RegionDetectionError(f"Use between 1 and {MAX_PAGES} page images for region detection.")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise RegionDetectionError(f"Region detection supports 1–{MAX_QUESTIONS} questions.")
    clean_questions, seen, text_size = [], set(), 0
    for item in questions:
        if not isinstance(item, dict):
            raise RegionDetectionError("Questions need an ID, type, and printed prompt.")
        identifier, kind, prompt = (item.get(k) for k in ("id", "type", "prompt"))
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier)
                or identifier in seen or kind not in ("exact", "choice", "rubric")
                or not isinstance(prompt, str) or not 1 <= len(prompt) <= 20_000):
            raise RegionDetectionError("Questions need unique IDs, supported types, and bounded printed prompts.")
        seen.add(identifier)
        text_size += len(prompt)
        # Discard any extra fields before inspecting content; keys are not used.
        clean_questions.append({"id": identifier, "type": kind, "prompt": prompt})
    if text_size > 200_000:
        raise RegionDetectionError("Question prompts are too large for region detection; select regions manually.")
    clean_pages, seen, pixels = [], set(), 0
    for item in pages:
        if not isinstance(item, dict):
            raise RegionDetectionError("Page images need their index and dimensions.")
        index, width, height = (item.get(k) for k in ("index", "width", "height"))
        if (type(index) is not int or index < 0 or index > 999 or index in seen
                or type(width) is not int or type(height) is not int
                or min(width, height) < 40 or max(width, height) > 20_000
                or width * height > MAX_PIXELS):
            raise RegionDetectionError("Page indices and dimensions are invalid or exceed the 25-megapixel limit.")
        path = item.get("path")
        if not isinstance(path, (str, Path)) or not str(path) or "\0" in str(path):
            raise RegionDetectionError("A local page image is required.")
        seen.add(index)
        pixels += width * height
        clean_pages.append({"index": index, "width": width, "height": height, "path": Path(path)})
    if pixels > MAX_TOTAL_PIXELS:
        raise RegionDetectionError("The selected pages exceed the 100-megapixel batch limit.")
    return clean_pages, clean_questions


def _run_process(command: list[str], timeout: float, limit: int) -> bytes:
    # Files bound Python memory; the input raster and runtime are bounded too.
    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"
    env["OMP_NUM_THREADS"] = "1"
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    timeout=timeout, check=False, env=env)
        except subprocess.TimeoutExpired as exc:
            raise RegionDetectionError("Local printed-number detection timed out; select regions manually.") from exc
        except OSError as exc:
            raise RegionDetectionError("Tesseract could not start. Install local Tesseract with English language data.") from exc
        if result.returncode:
            raise RegionDetectionError("Local OCR failed. Check Tesseract and its English language data, or select regions manually.")
        if out.tell() > limit:
            raise RegionDetectionError("The OCR layout is too large to inspect safely; use a simpler page or manual selection.")
        out.seek(0)
        return out.read(limit + 1)


def _engine_version(binary: str) -> str:
    output = _run_process([binary, "--version"], 5, 16_384).decode("utf-8", "replace")
    first = output.splitlines()[0] if output else ""
    if not re.fullmatch(r"tesseract [0-9][A-Za-z0-9.+_-]{0,70}", first.strip()):
        raise RegionDetectionError("The local OCR engine did not report a valid Tesseract version.")
    return first.strip()


def _run_tesseract(binary: str, image_path: Path, timeout: float) -> str:
    output = _run_process([binary, str(image_path), "stdout", "-l", "eng", "--psm", "3",
                           "-c", "tessedit_do_invert=0", "tsv"], timeout, MAX_TSV_BYTES)
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegionDetectionError("Local OCR returned unreadable layout data; select regions manually.") from exc


def _parse_tsv(text: str, width: int, height: int) -> list[_Word]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TSV_BYTES:
        raise RegionDetectionError("Local OCR layout exceeds the supported size.")
    required = {"level", "block_num", "par_num", "line_num", "left", "top", "width", "height", "text"}
    reader = csv.DictReader(StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
    if not required.issubset(set(reader.fieldnames or [])):
        raise RegionDetectionError("Local OCR returned an invalid layout table.")
    words = []
    try:
        for row_number, row in enumerate(reader):
            if row_number > MAX_WORDS * 6:
                raise RegionDetectionError("Local OCR returned too many layout elements.")
            if row["level"] != "5" or not (row.get("text") or "").strip():
                continue
            x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
                raise ValueError("out-of-page word")
            label = row["text"]
            if len(label) > 2048:
                raise ValueError("oversized word")
            line = tuple(int(row[k]) for k in ("block_num", "par_num", "line_num"))
            words.append(_Word(label, x, y, w, h, line))
            if len(words) > MAX_WORDS:
                raise RegionDetectionError("Local OCR returned too many words for this page.")
    except (ValueError, TypeError, KeyError, csv.Error) as exc:
        raise RegionDetectionError("Local OCR returned invalid word coordinates; select regions manually.") from exc
    return sorted(words, key=lambda word: (word.y, word.x))


def _load_page(page: dict) -> Image.Image:
    try:
        if not page["path"].is_file() or page["path"].stat().st_size > MAX_IMAGE_BYTES:
            raise RegionDetectionError("The local page image is missing or exceeds 25 MB.")
        with image_warnings.catch_warnings():
            image_warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(page["path"]) as original:
                if (original.format not in ("PNG", "JPEG", "WEBP")
                        or getattr(original, "n_frames", 1) != 1):
                    raise RegionDetectionError("Use a single PNG, JPEG, or WebP page image.")
                if original.size != (page["width"], page["height"]):
                    raise RegionDetectionError("Page image dimensions changed; reload the submission before detection.")
                image = original.convert("L")
                image.thumbnail((MAX_OCR_EDGE, MAX_OCR_EDGE), Image.Resampling.LANCZOS)
                return image
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise RegionDetectionError("The local page image could not be read safely; rescan it or select regions manually.") from exc


def _horizontal_rules(image: Image.Image, font_height: float) -> list[_Rule]:
    """Find long, thin horizontal ink runs, including small gaps in underscores."""
    width, height = image.size
    data = image.point(lambda value: 0 if value < 205 else 255).tobytes()
    minimum = max(24, round(width * 0.025), round(font_height * 1.8))
    join_gap = max(2, round(width / 900))
    segments = []
    for y in range(height):
        row = data[y * width:(y + 1) * width]
        start, end = None, None
        for run in re.finditer(b"\x00{4,}", row):
            if start is None:
                start, end = run.start(), run.end()
            elif run.start() - end <= join_gap:
                end = run.end()
            else:
                if end - start >= minimum:
                    segments.append((start, end, y))
                start, end = run.start(), run.end()
        if start is not None and end - start >= minimum:
            segments.append((start, end, y))
    active, finished = [], []
    for x, right, y in segments:
        still = []
        for group in active:
            if y - group[3] > 2:
                finished.append(group)
            else:
                still.append(group)
        active = still
        group = next((g for g in active if min(right, g[1]) - max(x, g[0])
                      >= 0.75 * min(right - x, g[1] - g[0])), None)
        if group is None:
            active.append([x, right, y, y + 1])
        else:
            group[0], group[1], group[3] = min(x, group[0]), max(right, group[1]), y + 1
    finished.extend(active)
    return [_Rule(*group) for group in finished
            if group[3] - group[2] <= max(6, font_height * 0.55)]


def _same_row(left: _Word, right: _Word) -> bool:
    overlap = min(left.bottom, right.bottom) - max(left.y, right.y)
    return overlap >= min(left.h, right.h) * 0.45


def _anchors(words: list[_Word]) -> list[_Anchor]:
    found = []
    number_count = 0
    for word in words:
        strong = re.fullmatch(r"(?:[A-Za-z][._-]?)?(?:\(([0-9]{1,4})\)|([0-9]{1,4})[.)])", word.text)
        weak = re.fullmatch(r"[0-9]{1,4}", word.text)
        if not strong and not weak:
            continue
        number_count += 1
        if number_count > 400:
            raise RegionDetectionError("Too many possible printed numbers were found; select regions manually.")
        number = int(next(g for g in strong.groups() if g is not None)) if strong else int(word.text)
        previous = [other for other in words if other.right <= word.x and _same_row(word, other)]
        if previous and word.x - max(other.right for other in previous) < max(10, word.h * 1.2):
            continue  # A number inside a sentence is not a question anchor.
        found.append(_Anchor(number, word, bool(strong)))
    return found


def _match_prompt(anchor: _Anchor, question: dict, words: list[_Word], anchors: list[_Anchor]):
    word = anchor.word
    other_columns = [item.word.x for item in anchors
                     if item.word.x > word.right + word.h * 3 and _same_row(word, item.word)]
    right = min(other_columns, default=10**9)
    expected = Counter(_tokens(question["prompt"])[:30])
    first_row = [item for item in words if item.x >= word.right and item.right <= right
                 and _same_row(word, item)]
    observed = Counter(token for item in first_row for token in _tokens(item.text))
    shared = expected & observed
    overlap = sum(shared.values())
    if not overlap:
        return None
    flags = []
    if overlap < min(2, sum(expected.values())):
        common_words = {"la", "el", "los", "las", "un", "una", "de", "del", "en", "al", "que",
                        "yo", "tu", "se", "su", "es", "the", "to", "and", "of"}
        if not anchor.strong or not any(token not in common_words for token in shared):
            return None
        flags.append("printed_prompt_match_limited")
    if not anchor.strong:
        flags.append("number_punctuation_unclear")
    matched = [item for item in first_row if set(_tokens(item.text)) & expected.keys()]
    # Follow up to three closely spaced printed continuation rows. Never consume
    # arbitrary later OCR text as a student response or classify it as blank.
    bottom = max([word.bottom] + [item.bottom for item in matched])
    for _ in range(3):
        nearby = [item for item in words if item.x >= word.x and item.right <= right
                  and bottom + 1 <= item.y <= bottom + word.h * 1.0]
        if not nearby:
            break
        top = min(item.y for item in nearby)
        row = [item for item in nearby if abs(item.y - top) <= word.h * 0.4]
        row_matches = [item for item in row if set(_tokens(item.text)) & expected.keys()]
        if sum(len(_tokens(item.text)) for item in row_matches) < 2:
            break
        matched.extend(row_matches)
        bottom = max(item.bottom for item in row_matches)
        if "multiline_printed_prompt" not in flags:
            flags.append("multiline_printed_prompt")
    return matched, flags


def _column_groups(anchors: list[_Anchor], width: int, font: float) -> list[list[_Anchor]]:
    groups = []
    tolerance = max(font * 2, width * 0.04)
    for anchor in sorted(anchors, key=lambda item: item.word.x):
        if not groups or anchor.word.x - statistics.median(a.word.x for a in groups[-1]) > tolerance:
            groups.append([anchor])
        else:
            groups[-1].append(anchor)
    return groups


def _proposal(anchor: _Anchor, group: list[_Anchor], column_left: float, column_right: float,
              all_anchors: list[_Anchor], words: list[_Word], rules: list[_Rule],
              image: Image.Image, page_index: int, multicolumn: bool) -> dict | None:
    width, height = image.size
    word = anchor.word
    font = max(10, statistics.median([word.h] + [item.h for item in anchor.matched_words]))
    prompt_bottom = max([word.bottom] + [item.bottom for item in anchor.matched_words])
    if any(item.right > column_right for item in anchor.matched_words):
        return None
    following = [item.word.y for item in all_anchors if item.word.y > word.y + font
                 and abs(item.word.x - word.x) <= max(font * 2, width * 0.04)
                 and (item.strong or item.question is not None)]
    section_headers = [item.y for item in words if item.y > prompt_bottom + font
                       and abs(item.x - word.x) <= font * 1.5
                       and re.fullmatch(r"[A-Za-z]\.", item.text)]
    bottom = min(following + section_headers + [height * 0.96]) - font * 0.55
    if bottom <= prompt_bottom + font * 0.7:
        return None
    relevant = [line for line in rules if line.x >= column_left - font
                and line.right <= column_right + font * 0.3
                and line.y >= word.y + font * 0.35 and line.bottom <= bottom]
    inline = []
    for line in relevant:
        if line.y > prompt_bottom + font or line.x <= word.right:
            continue
        printed_overlap = sum(max(0, min(item.right, line.right) - max(item.x, line.x))
                              for item in anchor.matched_words
                              if item.y < line.bottom and item.bottom + font >= line.y)
        if printed_overlap < (line.right - line.x) * 0.18:
            inline.append(line)
    below = [line for line in relevant if line.y > prompt_bottom + font]
    flags = ["teacher_review_required"] + (anchor.flags or [])
    if multicolumn:
        flags.append("check_column_boundary")
    if inline:
        # Several blanks need one union rectangle and may include prompt text.
        x = min(line.x for line in inline) - font * 0.6
        right = max(line.right for line in inline) + font * 0.6
        top = min(line.y for line in inline) - font * 2.4
        end = max(line.bottom for line in inline) + font * 1.15
        if len(inline) == 1:
            # Use the whole available gap while keeping adjacent printed words
            # out where possible. Edge ink below remains an explicit warning to
            # enlarge the rectangle if handwriting overran the printed space.
            before = [item.right for item in anchor.matched_words if item.right <= inline[0].x
                      and item.y < end and item.bottom > top]
            after = [item.x for item in anchor.matched_words if item.x >= inline[0].right
                     and item.y < end and item.bottom > top]
            x = max(x, max(before, default=0) + 2)
            right = min(right, min(after, default=width) - 2)
        if len(inline) > 1:
            flags.extend(["multiple_answer_spaces", "contains_printed_prompt"])
        reason = "Printed question number and an inline answer rule; padded to retain accents, edits, and crossed-out strokes."
    elif below:
        x = min(line.x for line in below) - font * 0.65
        right = max(line.right for line in below) + font * 0.65
        top = prompt_bottom + font * 0.2
        end = max(line.bottom for line in below) + font * 1.1
        reason = "Printed question number with ruled response space below the prompt; includes the space above each rule."
    elif anchor.question["type"] == "choice":
        # Circles, checks, or letters may be anywhere in the printed options.
        x, right = word.x - font * 0.7, column_right
        top, end = word.y - font * 0.6, bottom
        flags.extend(["contains_printed_prompt", "choice_marks_require_review", "answer_extent_uncertain"])
        reason = "Question block only: no separate answer rule was found. Printed options and student marks may both be included."
    else:
        # A filled/overwritten inline rule can disappear in the image. Guessing
        # the space below it would omit the answer, so leave these cases manual.
        if anchor.question["type"] == "exact" or re.search(r"_{2,}|\.{3,}|…", anchor.question["prompt"]):
            return None
        x, right = word.right + font * 0.2, column_right
        top, end = prompt_bottom + font * 0.3, bottom
        if end - top < font * 1.2:
            return None
        flags.append("answer_extent_uncertain")
        reason = "Open response space below the matched printed prompt, ending before the next detected question or section."
    top = max(top, word.y - font * 1.1)
    x, right = max(0, column_left, x), min(width, column_right, right)
    top, end = max(0, top), min(height, bottom, end)
    if right - x < 8 or end - top < 8:
        return None
    if any(item.x < right and item.right > x and item.y < end and item.bottom > top
           for item in anchor.matched_words):
        if "contains_printed_prompt" not in flags:
            flags.append("adjacent_printed_text")
    # Touching ink is a review signal, not evidence of a blank or a content type.
    box = (math.floor(x), math.floor(top), math.ceil(right), math.ceil(end))
    with image.crop(box) as crop:
        if crop.width >= 8 and crop.height >= 8:
            strips = [(0, 0, crop.width, 2), (0, crop.height - 2, crop.width, crop.height),
                      (0, 0, 2, crop.height), (crop.width - 2, 0, crop.width, crop.height)]
            for strip in strips:
                with crop.crop(strip) as edge:
                    if sum(value < 160 for value in edge.tobytes()) > 3:
                        flags.append("ink_near_crop_edge")
                        break
    return {"question_id": anchor.question["id"], "page": page_index,
            "x": x / width, "y": top / height,
            "width": (right - x) / width, "height": (end - top) / height,
            "reason": reason, "flags": flags}


def detect_regions(pages: list[dict], questions: list[dict]) -> dict:
    """Return unapproved normalized rectangles for known printed questions.

    Inputs are local rasterized pages with index/path/width/height and questions
    with id/type/prompt. Extra question fields are discarded. A page failure is
    reported without inventing rectangles; failure on every page raises a safe
    RegionDetectionError. Missing/ambiguous IDs remain explicitly missing. No
    return value expresses a score, blank-answer status, or confidence probability.
    """
    pages, questions = _validate_inputs(pages, questions)
    binary = shutil.which("tesseract")
    if not binary:
        raise RegionDetectionError("Install local Tesseract with English language data to suggest regions, or select them manually.")
    engine_version = _engine_version(binary)
    warnings = ["All regions are proposals. Check question numbers, full handwriting and crossed-out work before applying; detection does not identify blank answers."]
    aliases = defaultdict(list)
    for question in questions:
        number = _numeric_alias(question["id"])
        if number is None:
            warnings.append(f"Question {question['id']}: its ID is not a supported printed numeric ID; select manually.")
        else:
            aliases[number].append(question)
    usable = {}
    for number, matches in aliases.items():
        if len(matches) == 1:
            usable[number] = matches[0]
        else:
            warnings.append(f"Printed number {number} maps to several question IDs; select those regions manually.")
    found = defaultdict(list)
    occurrences = Counter()
    successful_pages = 0
    failures = []
    started = time.monotonic()
    for page in pages:
        image = None
        try:
            remaining = TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise RegionDetectionError("The detection batch reached its time limit; select remaining pages manually.")
            image = _load_page(page)
            with tempfile.TemporaryDirectory(prefix="ez-grade-regions-") as directory:
                ocr_path = Path(directory) / "page.png"
                image.save(ocr_path)
                words = _parse_tsv(_run_tesseract(binary, ocr_path, min(PAGE_TIMEOUT_SECONDS, remaining)), *image.size)
            successful_pages += 1
            anchors = _anchors(words)
            selected = []
            for anchor in anchors:
                if anchor.number not in usable:
                    continue
                result = _match_prompt(anchor, usable[anchor.number], words, anchors)
                if result is None:
                    if anchor.strong:
                        warnings.append(f"Page {page['index'] + 1}, number {anchor.number}: printed prompt could not be matched; inspect manually.")
                    continue
                anchor.question = usable[anchor.number]
                anchor.matched_words, anchor.flags = result
                occurrences[anchor.question["id"]] += 1
                selected.append(anchor)
            if not selected:
                warnings.append(f"Page {page['index'] + 1}: no unambiguous known question numbers matched their printed prompts.")
                continue
            font = statistics.median(anchor.word.h for anchor in selected)
            columns = _column_groups(selected, image.width, font)
            if len(columns) > 2:
                warnings.append(f"Page {page['index'] + 1}: more than two possible columns or inconsistent indentation; select regions manually.")
                continue
            rules = _horizontal_rules(image, font)
            for column_index, group in enumerate(columns):
                left = max(0, min(anchor.word.x for anchor in group) - font)
                right = (min(anchor.word.x for anchor in columns[column_index + 1]) - font
                         if column_index + 1 < len(columns) else image.width * 0.97)
                for anchor in group:
                    proposal = _proposal(anchor, group, left, right, anchors, words, rules,
                                         image, page["index"], len(columns) > 1)
                    if proposal:
                        found[anchor.question["id"]].append(proposal)
                    else:
                        warnings.append(f"Page {page['index'] + 1}, question {anchor.question['id']}: answer space could not be separated safely; select manually.")
        except RegionDetectionError as exc:
            warnings.append(f"Page {page['index'] + 1}: {exc}")
            failures.append(str(exc))
        finally:
            if image is not None:
                image.close()
    if not successful_pages:
        detail = (failures[0] if failures else "Check page images and local Tesseract.")[:500]
        raise RegionDetectionError(f"Local region detection failed on every page. {detail}")
    proposals = []
    for question in questions:
        candidates = found.get(question["id"], [])
        if occurrences[question["id"]] > 1:
            warnings.append(f"Question {question['id']}: its printed number and prompt appeared more than once; no region was selected.")
        elif candidates:
            proposals.append(candidates[0])
    overlapping = set()
    for i, first in enumerate(proposals):
        for second in proposals[i + 1:]:
            if first["page"] != second["page"]:
                continue
            overlap_width = min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"])
            overlap_height = min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"])
            if overlap_width > 0 and overlap_height > 0:
                area = overlap_width * overlap_height
                if area > 0.02 * min(first["width"] * first["height"], second["width"] * second["height"]):
                    overlapping.update((first["question_id"], second["question_id"]))
    if overlapping:
        warnings.append("Some proposed answer spaces overlapped; those questions require manual selection.")
        proposals = [item for item in proposals if item["question_id"] not in overlapping]
    proposed = {item["question_id"] for item in proposals}
    return {"detector_version": DETECTOR_VERSION, "engine_version": engine_version,
            "proposals": proposals,
            "missing_question_ids": [question["id"] for question in questions if question["id"] not in proposed],
            "warnings": list(dict.fromkeys(warnings))}
