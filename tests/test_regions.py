"""Synthetic layout tests; these do not measure classroom OCR accuracy."""

from __future__ import annotations

from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from app import regions


class SyntheticPage:
    """Draw only synthetic print/marks and optionally supply its exact OCR boxes."""

    def __init__(self, directory, name="page.png", width=1000, height=1200):
        self.path = Path(directory) / name
        self.image = Image.new("L", (width, height), 255)
        self.draw = ImageDraw.Draw(self.image)
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        self.font = ImageFont.truetype(str(font_path), 28) if font_path.exists() else ImageFont.load_default(size=28)
        self.words = []
        self.line = 0

    def row(self, words, y):
        self.line += 1
        for text, x in words:
            self.draw.text((x, y), text, font=self.font, fill=0, anchor="lt")
            box = self.draw.textbbox((x, y), text, font=self.font, anchor="lt")
            self.words.append((text, x, y, box[2] - x, box[3] - y, self.line))

    def rule(self, x, right, y):
        self.draw.line((x, y, right, y), fill=70, width=2)

    def save(self, index=0):
        self.image.save(self.path)
        return {"index": index, "path": str(self.path), "width": self.image.width, "height": self.image.height}

    def tsv(self):
        lines = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
        for n, (text, x, y, width, height, line) in enumerate(self.words):
            lines.append(f"5\t1\t1\t1\t{line}\t{n + 1}\t{x}\t{y}\t{width}\t{height}\t95\t{text}")
        return "\n".join(lines) + "\n"

    def close(self):
        self.image.close()


def question(identifier, prompt, kind="exact"):
    return {"id": identifier, "type": kind, "prompt": prompt}


def pixels(proposal, page):
    return (proposal["x"] * page["width"], proposal["y"] * page["height"],
            (proposal["x"] + proposal["width"]) * page["width"],
            (proposal["y"] + proposal["height"]) * page["height"])


class RegionDetectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("app.regions.shutil.which", return_value="/local/tesseract"))
        self.stack.enter_context(patch("app.regions._engine_version", return_value="tesseract 5.synthetic"))

    def scene(self, *args, **kwargs):
        scene = SyntheticPage(self.directory.name, *args, **kwargs)
        self.addCleanup(scene.close)
        return scene

    def detect(self, scene, questions, index=0):
        page = scene.save(index)
        with patch("app.regions._run_tesseract", return_value=scene.tsv()) as engine:
            result = regions.detect_regions([page], questions)
        self.assertLessEqual(engine.call_args.args[2], 20)
        self.assertEqual(result["detector_version"], regions.DETECTOR_VERSION)
        self.assertEqual(result["engine_version"], "tesseract 5.synthetic")
        for proposal in result["proposals"]:
            self.assertEqual(set(proposal), {"question_id", "page", "x", "y", "width", "height", "reason", "flags"})
            self.assertGreater(proposal["width"], 0)
            self.assertGreater(proposal["height"], 0)
            self.assertGreaterEqual(proposal["x"], 0)
            self.assertGreaterEqual(proposal["y"], 0)
            self.assertLessEqual(proposal["x"] + proposal["width"], 1)
            self.assertLessEqual(proposal["y"] + proposal["height"], 1)
            self.assertIn("teacher_review_required", proposal["flags"])
        return result, page

    def test_inline_and_ruled_spaces_exclude_prompt_and_keep_full_synthetic_marks(self):
        scene = self.scene()
        scene.row([("21.", 40), ("La", 105), ("tienda", 155), ("camisas.", 480)], 140)
        scene.rule(270, 450, 174)
        # Synthetic accent and crossed-out strokes inside the reserved answer gap.
        scene.draw.line([(280, 160), (330, 148), (360, 165), (440, 150)], fill=0, width=2)
        scene.draw.line((282, 147, 438, 165), fill=0, width=2)
        scene.draw.line((345, 134, 351, 129), fill=0, width=2)
        scene.row([("22.", 40), ("Describe", 105), ("tu", 240), ("casa.", 290)], 350)
        scene.rule(105, 900, 430)
        scene.rule(105, 900, 490)
        result, page = self.detect(scene, [question("21", "La tienda ____ camisas."),
                                          question("22", "Describe tu casa.", "rubric")], index=1)
        self.assertEqual([p["question_id"] for p in result["proposals"]], ["21", "22"])
        inline, ruled = result["proposals"]
        x, y, right, end = pixels(inline, page)
        self.assertLessEqual(x, 270)
        self.assertGreaterEqual(right, 450)
        self.assertLessEqual(y, 129)
        self.assertGreater(end, 174)
        self.assertLess(right, 480)
        self.assertGreater(x, 240)
        self.assertNotIn("contains_printed_prompt", inline["flags"])
        x, y, right, end = pixels(ruled, page)
        self.assertLessEqual(x, 105)
        self.assertGreaterEqual(right, 900)
        self.assertGreater(y, 370)
        self.assertLess(y, 430)
        self.assertGreater(end, 490)
        self.assertEqual(inline["page"], 1)
        self.assertEqual(result["missing_question_ids"], [])

    def test_only_known_ids_are_proposed_and_unknown_following_number_stops_open_space(self):
        scene = self.scene()
        scene.row([("1.", 40), ("Describe", 100), ("tu", 235), ("escuela.", 285)], 100)
        scene.row([("2.", 40), ("Otra", 100), ("pregunta.", 185)], 400)
        result, page = self.detect(scene, [question("q1", "Describe tu escuela.", "rubric"),
                                          question("q9", "Unseen question.")])
        self.assertEqual([p["question_id"] for p in result["proposals"]], ["q1"])
        self.assertLess(pixels(result["proposals"][0], page)[3], 400)
        self.assertIn("answer_extent_uncertain", result["proposals"][0]["flags"])
        self.assertEqual(result["missing_question_ids"], ["q9"])

    def test_parenthesized_and_section_prefixed_numbers_map_to_unambiguous_ids(self):
        scene = self.scene()
        scene.row([("(21)", 40), ("La", 120), ("tienda", 170)], 100)
        scene.rule(320, 600, 133)
        scene.row([("B.22.", 40), ("Tu", 130), ("casa", 190)], 300)
        scene.rule(320, 600, 333)
        result, _ = self.detect(scene, [question("B_21", "La tienda ____"), question("B22", "Tu casa ____")])
        self.assertEqual({p["question_id"] for p in result["proposals"]}, {"B_21", "B22"})

    def test_duplicate_numeric_aliases_and_unsupported_ids_remain_missing(self):
        scene = self.scene()
        scene.row([("1.", 40), ("La", 100), ("tienda", 150)], 100)
        scene.rule(300, 600, 134)
        result, _ = self.detect(scene, [question("B_1", "La tienda"), question("C_1", "La tienda"),
                                       question("vocabulary", "La tienda")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["missing_question_ids"], ["B_1", "C_1", "vocabulary"])
        self.assertTrue(any("several question IDs" in w for w in result["warnings"]))
        self.assertTrue(any("not a supported" in w for w in result["warnings"]))

    def test_repeated_number_and_prompt_are_not_arbitrarily_assigned(self):
        scene = self.scene()
        for y in (100, 350):
            scene.row([("1.", 40), ("La", 100), ("tienda", 150)], y)
            scene.rule(300, 600, y + 34)
        result, _ = self.detect(scene, [question("1", "La tienda ____")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["missing_question_ids"], ["1"])
        self.assertTrue(any("more than once" in w for w in result["warnings"]))

    def test_same_question_on_two_pages_is_ambiguous(self):
        scene = self.scene()
        scene.row([("1.", 40), ("La", 100), ("tienda", 150)], 100)
        scene.rule(300, 600, 134)
        first = scene.save(0)
        second = {**first, "index": 1}
        with patch("app.regions._run_tesseract", return_value=scene.tsv()):
            result = regions.detect_regions([first, second], [question("1", "La tienda ____")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["missing_question_ids"], ["1"])

    def test_different_prompt_or_numbers_inside_sentences_are_not_matches(self):
        scene = self.scene()
        scene.row([("1.", 40), ("La", 100), ("escuela", 150)], 100)
        scene.rule(300, 600, 134)
        scene.row([("Puntos", 40), ("2.", 150), ("La", 205), ("tienda", 255)], 300)
        scene.rule(400, 600, 334)
        result, _ = self.detect(scene, [question("1", "La tienda ____"), question("2", "La tienda ____")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["missing_question_ids"], ["1", "2"])

    def test_two_columns_have_disjoint_regions_with_boundary_review_flag(self):
        scene = self.scene()
        questions = []
        for identifier, x, y in (("1", 40, 100), ("2", 40, 350), ("3", 550, 100), ("4", 550, 350)):
            scene.row([(identifier + ".", x), ("Describe", x + 55), ("casa", x + 195)], y)
            scene.rule(x + 55, x + 390, y + 100)
            questions.append(question(identifier, "Describe casa.", "rubric"))
        result, page = self.detect(scene, questions)
        self.assertEqual({p["question_id"] for p in result["proposals"]}, {"1", "2", "3", "4"})
        for proposal in result["proposals"]:
            x, y, right, end = pixels(proposal, page)
            self.assertIn("check_column_boundary", proposal["flags"])
            if proposal["question_id"] in ("1", "2"):
                self.assertLess(right, 550)
            else:
                self.assertGreater(x, 550)

    def test_three_columns_require_manual_selection(self):
        scene = self.scene(width=1500)
        for number, x in ((1, 40), (2, 550), (3, 1050)):
            scene.row([(f"{number}.", x), ("Tu", x + 55), ("casa", x + 110)], 100)
            scene.rule(x + 55, x + 350, 200)
        result, _ = self.detect(scene, [question(str(n), "Tu casa.", "rubric") for n in range(1, 4)])
        self.assertEqual(result["proposals"], [])
        self.assertTrue(any("more than two" in w for w in result["warnings"]))

    def test_multiple_inline_blanks_explicitly_warn_about_included_print(self):
        scene = self.scene()
        scene.row([("1.", 40), ("Yo", 90), ("y", 395), ("ella", 430), ("temprano.", 780)], 120)
        scene.rule(160, 370, 154)
        scene.rule(530, 750, 154)
        result, page = self.detect(scene, [question("1", "Yo ____ y ella ____ temprano.")])
        proposal = result["proposals"][0]
        self.assertIn("multiple_answer_spaces", proposal["flags"])
        self.assertIn("contains_printed_prompt", proposal["flags"])
        self.assertLess(pixels(proposal, page)[0], 160)
        self.assertGreater(pixels(proposal, page)[2], 750)

    def test_choice_block_fallback_explicitly_contains_print_and_is_uncertain(self):
        scene = self.scene()
        scene.row([("1.", 40), ("Elige", 100), ("un", 190), ("color.", 240)], 100)
        scene.row([("A.", 100), ("rojo", 155)], 180)
        scene.row([("B.", 100), ("azul", 155)], 230)
        scene.row([("2.", 40), ("Otra", 100), ("pregunta.", 185)], 400)
        result, page = self.detect(scene, [question("1", "Elige un color.", "choice")])
        proposal = result["proposals"][0]
        self.assertIn("contains_printed_prompt", proposal["flags"])
        self.assertIn("choice_marks_require_review", proposal["flags"])
        self.assertIn("answer_extent_uncertain", proposal["flags"])
        self.assertLess(pixels(proposal, page)[1], 100)
        self.assertGreater(pixels(proposal, page)[3], 250)

    def test_empty_ocr_is_missing_layout_not_blank_answers_or_zero_scores(self):
        scene = self.scene()
        result, _ = self.detect(scene, [question("1", "La tienda ____")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["missing_question_ids"], ["1"])
        self.assertNotIn("score", json.dumps(result))
        self.assertNotIn("confidence", json.dumps(result))

    def test_obscured_inline_rule_does_not_become_an_empty_space_below_prompt(self):
        scene = self.scene()
        scene.row([("1.", 40), ("La", 100), ("tienda", 150), ("camisas.", 500)], 100)
        # Synthetic writing without a remaining visible rule in the answer gap.
        scene.draw.line([(300, 115), (330, 98), (355, 120), (390, 99), (450, 114)], fill=0, width=3)
        result, _ = self.detect(scene, [question("1", "La tienda ____ camisas.")])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["missing_question_ids"], ["1"])
        self.assertTrue(any("could not be separated safely" in w for w in result["warnings"]))

    def test_question_keys_are_ignored_and_inputs_are_not_mutated(self):
        scene = self.scene()
        scene.row([("1.", 40), ("La", 100), ("tienda", 150)], 100)
        scene.rule(300, 600, 134)
        item = {**question("1", "La tienda ____"), "accepted_answers": ["SECRET_KEY_DO_NOT_READ"],
                "rubric": {"untrusted": "ignored"}}
        original = json.dumps(item)
        result, _ = self.detect(scene, [item])
        self.assertEqual(json.dumps(item), original)
        self.assertNotIn("SECRET_KEY", json.dumps(result))
        self.assertEqual(len(result["proposals"]), 1)

    def test_partial_page_failure_reports_missing_ids_without_losing_successful_page(self):
        scene = self.scene()
        scene.row([("2.", 40), ("La", 100), ("tienda", 150)], 100)
        scene.rule(300, 600, 134)
        first, second = scene.save(0), scene.save(1)
        with patch("app.regions._run_tesseract", side_effect=[regions.RegionDetectionError("Local OCR timed out."), scene.tsv()]):
            result = regions.detect_regions([first, second], [question("1", "Tu casa ____"), question("2", "La tienda ____")])
        self.assertEqual([p["question_id"] for p in result["proposals"]], ["2"])
        self.assertEqual(result["missing_question_ids"], ["1"])
        self.assertTrue(any("Page 1" in w and "timed out" in w for w in result["warnings"]))

    def test_every_page_failure_raises_safe_actionable_error(self):
        scene = self.scene()
        with patch("app.regions._run_tesseract", side_effect=regions.RegionDetectionError("Local OCR timed out.")):
            with self.assertRaisesRegex(regions.RegionDetectionError, "every page.*timed out"):
                regions.detect_regions([scene.save()], [question("1", "La tienda ____")])

    def test_invalid_or_out_of_bounds_tsv_is_a_failure_not_a_region(self):
        scene = self.scene()
        scene.row([("1.", 40), ("La", 100), ("tienda", 150)], 100)
        malformed = scene.tsv().replace("\t40\t100\t", "\t999999\t100\t", 1)
        for tsv in ("not a layout", malformed):
            with self.subTest(tsv=tsv[:15]), patch("app.regions._run_tesseract", return_value=tsv):
                with self.assertRaises(regions.RegionDetectionError):
                    regions.detect_regions([scene.save()], [question("1", "La tienda ____")])

    def test_missing_engine_and_changed_image_dimensions_fail_without_ocr(self):
        scene = self.scene()
        page = scene.save()
        with patch("app.regions.shutil.which", return_value=None):
            with self.assertRaisesRegex(regions.RegionDetectionError, "Install local Tesseract"):
                regions.detect_regions([page], [question("1", "La tienda ____")])
        with patch("app.regions._run_tesseract") as engine:
            with self.assertRaisesRegex(regions.RegionDetectionError, "dimensions changed"):
                regions.detect_regions([{**page, "width": page["width"] + 1}], [question("1", "La tienda ____")])
            engine.assert_not_called()

    def test_input_bounds_and_duplicate_page_indices_are_checked_before_ocr(self):
        scene = self.scene()
        page = scene.save()
        invalid = [([], [question("1", "Casa")]), ([page] * 11, [question("1", "Casa")]),
                   ([page, page], [question("1", "Casa")]),
                   ([{**page, "width": 6000, "height": 6000}], [question("1", "Casa")]),
                   ([page], [question("1", "Casa")] * 2),
                   ([page], [question(str(n), "Casa") for n in range(101)]),
                   ([{**page, "index": True}], [question("1", "Casa")])]
        with patch("app.regions._run_tesseract") as engine:
            for pages, questions in invalid:
                with self.subTest(pages=len(pages), questions=len(questions)):
                    with self.assertRaises(regions.RegionDetectionError):
                        regions.detect_regions(pages, questions)
            engine.assert_not_called()


class RegionProcessTests(unittest.TestCase):
    def test_subprocess_timeout_and_exit_errors_are_safe_without_echoing_stderr(self):
        with patch("app.regions.subprocess.run", side_effect=subprocess.TimeoutExpired(["private/student/path"], 20)):
            with self.assertRaisesRegex(regions.RegionDetectionError, "timed out") as raised:
                regions._run_process(["tesseract"], 20, 1000)
            self.assertNotIn("private", str(raised.exception))

        def failed(*args, **kwargs):
            kwargs["stderr"].write(b"private student path and answer content")
            return SimpleNamespace(returncode=1)

        with patch("app.regions.subprocess.run", side_effect=failed):
            with self.assertRaisesRegex(regions.RegionDetectionError, "Local OCR failed") as raised:
                regions._run_process(["tesseract"], 20, 1000)
            self.assertNotIn("student", str(raised.exception))

    def test_output_size_single_thread_timeout_and_no_shell_are_enforced(self):
        def oversized(command, **kwargs):
            self.assertIsInstance(command, list)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["timeout"], 20)
            self.assertEqual(kwargs["env"]["OMP_THREAD_LIMIT"], "1")
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            kwargs["stdout"].write(b"x" * 101)
            return SimpleNamespace(returncode=0)

        with patch("app.regions.subprocess.run", side_effect=oversized):
            with self.assertRaisesRegex(regions.RegionDetectionError, "too large"):
                regions._run_process(["tesseract", "local.png"], 20, 100)


class RealSyntheticOCRTests(unittest.TestCase):
    def test_local_tesseract_detects_exact_ids_and_surrounds_inline_and_below_rules(self):
        binary = shutil.which("tesseract")
        if not binary:
            self.skipTest("Local Tesseract is not installed.")
        languages = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=5)
        if languages.returncode or "eng" not in languages.stdout.splitlines():
            self.skipTest("Tesseract English language data is not installed.")
        with tempfile.TemporaryDirectory() as directory:
            scene = SyntheticPage(directory)
            try:
                scene.row([("SYNTHETIC", 50), ("LAYOUT", 240), ("TEST", 380)], 30)
                scene.row([("21.", 40), ("La", 105), ("tienda", 155), ("camisas.", 490)], 150)
                scene.rule(270, 450, 183)
                scene.row([("22.", 40), ("Nosotros", 105), ("nueve.", 550)], 320)
                scene.rule(300, 510, 353)
                scene.row([("23.", 40), ("Describe", 105), ("tu", 245), ("casa.", 295)], 510)
                scene.rule(105, 900, 610)
                page = scene.save()
                before = sha256(scene.path.read_bytes()).hexdigest()
                result = regions.detect_regions([page], [question("21", "La tienda ____ camisas."),
                    question("22", "Nosotros ____ nueve."), question("23", "Describe tu casa.", "rubric")])
                self.assertEqual({p["question_id"] for p in result["proposals"]}, {"21", "22", "23"})
                self.assertEqual(result["missing_question_ids"], [])
                self.assertTrue(result["engine_version"].startswith("tesseract "))
                bounds = {p["question_id"]: pixels(p, page) for p in result["proposals"]}
                for identifier, left, right, y in (("21", 270, 450, 183), ("22", 300, 510, 353), ("23", 105, 900, 610)):
                    x1, y1, x2, y2 = bounds[identifier]
                    self.assertLessEqual(x1, left)
                    self.assertGreaterEqual(x2, right)
                    self.assertLess(y1, y)
                    self.assertGreater(y2, y)
                self.assertGreater(bounds["23"][1], 530)
                self.assertEqual(sha256(scene.path.read_bytes()).hexdigest(), before)
            finally:
                scene.close()


if __name__ == "__main__":
    unittest.main()
