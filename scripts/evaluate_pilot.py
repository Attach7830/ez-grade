#!/usr/bin/env python3
"""Compare immutable first drafts with teacher references, using eval rows only.

Never calls a model. Synthetic reports are software checks, not classroom accuracy.
Teacher edits and current scores are not used as the original predictions.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import unicodedata


REQUIRED_COLUMNS = {"response_id", "split", "question_type", "transcription", "points", "blank"}


def digest_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def distance(left, right) -> int:
    """Levenshtein edit count; O(min(n,m)) memory, for short responses."""
    if len(left) > len(right):
        left, right = right, left
    row = list(range(len(left) + 1))
    for i, right_item in enumerate(right, 1):
        next_row = [i]
        for j, left_item in enumerate(left, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (left_item != right_item)))
        row = next_row
    return row[-1]


def number(value, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field}: expected a finite number, not a boolean")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field}: expected a finite number") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{field}: expected a finite nonnegative number")
    return parsed


def read_truth(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not REQUIRED_COLUMNS.issubset(reader.fieldnames or []):
            raise ValueError("Truth CSV must include: " + ",".join(sorted(REQUIRED_COLUMNS)))
        rows, seen = [], set()
        for line, item in enumerate(reader, 2):
            if not any(value for value in item.values() if isinstance(value, str)):
                continue
            if None in item:
                raise ValueError(f"Truth line {line}: extra cells; quote responses containing commas")
            response_id = (item["response_id"] or "").strip()
            if not response_id or response_id in seen:
                raise ValueError(f"Truth line {line}: missing or duplicate response_id")
            seen.add(response_id)
            split = (item["split"] or "").strip()
            if split not in {"tuning", "eval"}:
                raise ValueError(f"Truth line {line}: split must be tuning or eval")
            question_type = (item["question_type"] or "").strip()
            if not question_type:
                raise ValueError(f"Truth line {line}: supply a question_type such as conjugation")
            blank_raw = (item["blank"] or "").strip().lower()
            if blank_raw not in {"true", "false"}:
                raise ValueError(f"Truth line {line}: blank must be true or false")
            transcript = item["transcription"]
            if transcript is None:
                raise ValueError(f"Truth line {line}: missing transcription cell")
            blank = blank_raw == "true"
            if blank != (transcript == ""):
                raise ValueError(f"Truth line {line}: true blanks need an empty transcription; nonblanks need literal text")
            if len(transcript) > 10000:
                raise ValueError(f"Truth line {line}: transcription exceeds the short-response limit")
            rows.append({"response_id": response_id, "split": split, "question_type": question_type,
                         "transcription": transcript, "points": number(item["points"], f"Truth line {line} points"), "blank": blank})
    if not any(row["split"] == "eval" for row in rows):
        raise ValueError("No evaluation rows; at least one split=eval row is required")
    return rows


def first_event(response: dict, kinds: set[str]) -> dict | None:
    # Audit history is ordered oldest first. Do not select the latest retry.
    for event in response.get("history", []):
        if event.get("kind") in kinds:
            return event
    return None


def initial_reading(response: dict) -> tuple[str | None, str, list[str]]:
    if response.get("source_kind") == "typed":
        original = response.get("original_text")
        return (original if isinstance(original, str) else None, "typed", [])
    event = first_event(response, {"transcription_suggested", "transcription_failed"})
    if event is None:
        return None, "not_attempted", []
    details = event.get("details", {})
    if event["kind"] == "transcription_failed":
        return None, "failed", ["transcription_failed"]
    flags = list(details.get("flags") or [])
    status = details.get("status", "unknown")
    if status in {"ambiguous", "failed", "blank"}:
        flags.append("transcription_" + status)
    transcript = details.get("transcription")
    if not isinstance(transcript, str) or status == "failed":
        return None, "failed", flags + ["invalid_initial_transcription"]
    if len(transcript) > 10000:
        raise ValueError("An initial transcription exceeds the short-response limit")
    return transcript, status, flags


def initial_grade(response: dict) -> tuple[Decimal | None, list[str], dict | None]:
    event = first_event(response, {"grade_suggested", "grade_failed"})
    if event is None:
        return None, [], None
    if event["kind"] == "grade_failed":
        return None, ["grade_failed"], event
    suggestion = event.get("details", {}).get("suggestion")
    if not isinstance(suggestion, dict) or suggestion.get("suggested_points") is None:
        return None, ["invalid_initial_grade"], event
    try:
        points = number(suggestion["suggested_points"], "Initial suggested points")
    except ValueError:
        return None, ["invalid_initial_grade"], event
    return points, list(suggestion.get("review_reasons") or []), event


def summarize(rows: list[dict]) -> dict:
    scored = [row for row in rows if row["suggested_points"] is not None]
    scans = [row for row in rows if row["source_kind"] == "scan"]
    read_scans = [row for row in scans if row["initial_transcription_available"]]
    char_den = sum(row["reference_characters"] for row in read_scans)
    word_den = sum(row["reference_words"] for row in read_scans)
    char_edits = sum(row["character_edits"] for row in read_scans)
    word_edits = sum(row["word_edits"] for row in read_scans)
    return {
        "responses": len(rows),
        "scan_responses": len(scans),
        "typed_responses": len(rows) - len(scans),
        "scan_transcription_outputs": len(read_scans),
        "scan_missing_or_failed_transcriptions": len(scans) - len(read_scans),
        "scan_transcription_mismatches": sum(row["transcription_error"] for row in read_scans),
        "scan_character_edits": char_edits,
        "scan_reference_characters": char_den,
        "scan_cer": char_edits / char_den if char_den else None,
        "scan_word_edits": word_edits,
        "scan_reference_words": word_den,
        "scan_wer": word_edits / word_den if word_den else None,
        "true_blank_scans_with_invented_text": sum(row["true_blank"] and row["transcription_error"] for row in read_scans),
        "nonblank_scans_read_as_blank": sum(not row["true_blank"] and row["initial_transcription_blank"] for row in read_scans),
        "initial_grade_outputs": len(scored),
        "missing_or_failed_initial_grades": len(rows) - len(scored),
        "exact_grade_matches": sum(row["points_delta"] == 0 for row in scored),
        "grade_agreement_given_output": sum(row["points_delta"] == 0 for row in scored) / len(scored) if scored else None,
        "responses_over_awarded": sum(row["points_delta"] > 0 for row in scored),
        "responses_under_awarded": sum(row["points_delta"] < 0 for row in scored),
        "points_incorrectly_awarded": float(sum((Decimal(str(row["points_delta"])) for row in scored if row["points_delta"] > 0), Decimal(0))),
        "points_incorrectly_withheld": float(sum((-Decimal(str(row["points_delta"])) for row in scored if row["points_delta"] < 0), Decimal(0))),
        "unflagged_transcription_errors": sum(row["unflagged_transcription_error"] for row in rows),
        "unflagged_grade_errors": sum(row["unflagged_grade_error"] for row in rows),
        "responses_with_any_unflagged_error": sum(row["unflagged_transcription_error"] or row["unflagged_grade_error"] for row in rows),
    }


def evaluate(audit: dict, truth: list[dict], *, allow_synthetic: bool = False, prior_tuning: dict | None = None) -> dict:
    if audit.get("format_version") != 1:
        raise ValueError("Expected audit format_version=1")
    assessment = audit.get("assessment", {})
    synthetic = bool(assessment.get("synthetic", False))
    if synthetic and not allow_synthetic:
        raise ValueError("Synthetic assessment: pass --allow-synthetic for a SOFTWARE TEST report only")
    responses = {str(item["id"]): item for item in audit.get("responses", [])}
    if len(responses) != len(audit.get("responses", [])):
        raise ValueError("Audit contains duplicate response IDs")
    questions = {str(item["id"]): item for item in assessment.get("questions", [])}
    if len({row['response_id'] for row in truth}) != len(truth):
        raise ValueError("Duplicate truth response IDs")
    for job_field, assessment_field in (("model_digest", "model_digest"), ("prompt_version", "prompt_version"), ("rubric_version", "version")):
        versions = {job[job_field] for job in audit.get("jobs", []) if job.get(job_field)}
        if assessment.get(assessment_field):
            versions.add(assessment[assessment_field])
        if len(versions) > 1:
            raise ValueError(f"Audit mixes {job_field} values; evaluate a consistent batch")
    splits = {}
    for row in truth:
        response = responses.get(row["response_id"])
        if response is None:
            raise ValueError(f"Truth response_id {row['response_id']} is absent from this audit")
        submission_id = str(response.get("submission_id", ""))
        if not submission_id:
            raise ValueError("Audit response is missing its submission_id")
        if submission_id in splits and splits[submission_id] != row["split"]:
            raise ValueError("The same student submission cannot appear in tuning and evaluation splits")
        splits[submission_id] = row["split"]
        maximum = questions.get(str(response.get("question_id")), {}).get("max_points")
        if maximum is None:
            raise ValueError("Audit question has no maximum points")
        if row["points"] > number(maximum, "Question maximum"):
            raise ValueError("Teacher reference exceeds the question maximum")
    manifest = {
        "tuning_response_id_hashes": sorted(digest_id(row["response_id"]) for row in truth if row["split"] == "tuning"),
        "evaluation_response_id_hashes": sorted(digest_id(row["response_id"]) for row in truth if row["split"] == "eval"),
        "tuning_submission_id_hashes": sorted(digest_id(sid) for sid, split in splits.items() if split == "tuning"),
        "evaluation_submission_id_hashes": sorted(digest_id(sid) for sid, split in splits.items() if split == "eval"),
    }
    if prior_tuning:
        previous = prior_tuning.get("split_manifest", prior_tuning)
        for kind in ("response", "submission"):
            seen = set(previous.get(f"tuning_{kind}_id_hashes", []))
            if seen.intersection(manifest[f"evaluation_{kind}_id_hashes"]):
                raise ValueError("Evaluation overlaps the prior tuning manifest")
    compared = []
    for reference in truth:
        if reference["split"] != "eval":
            continue
        response = responses[reference["response_id"]]
        if response.get("source_kind") not in {"scan", "typed"}:
            raise ValueError("Audit response must identify scan or typed source_kind")
        transcript, reading_status, reading_flags = initial_reading(response)
        grade, grade_flags, grade_event = initial_grade(response)
        maximum = number(questions[str(response["question_id"])]["max_points"], "Question maximum")
        if grade is not None and grade > maximum:
            grade, grade_flags = None, grade_flags + ["out_of_range_initial_grade"]
        expected = unicodedata.normalize("NFC", reference["transcription"])
        actual = unicodedata.normalize("NFC", transcript) if transcript is not None else None
        scan = response.get("source_kind") == "scan"
        text_error = actual is not None and actual != expected
        delta = grade - reference["points"] if grade is not None else None
        flags = reading_flags + grade_flags
        compared.append({
            "response_id": reference["response_id"], "question_id": str(response["question_id"]),
            "question_type": reference["question_type"], "source_kind": response.get("source_kind"),
            "initial_transcription_status": reading_status, "initial_transcription_available": actual is not None,
            "initial_transcription_blank": actual == "", "true_blank": reference["blank"],
            "transcription_error": bool(text_error),
            "character_edits": distance(expected, actual) if actual is not None else None,
            "reference_characters": len(expected),
            "word_edits": distance(expected.split(), actual.split()) if actual is not None else None,
            "reference_words": len(expected.split()),
            "suggested_points": float(grade) if grade is not None else None,
            "teacher_points": float(reference["points"]), "points_delta": float(delta) if delta is not None else None,
            "initial_review_reasons": flags,
            "unflagged_transcription_error": bool(scan and text_error and not reading_flags),
            "unflagged_grade_error": bool(delta is not None and delta != 0 and not flags),
            "grade_event_id": grade_event.get("id") if grade_event else None,
        })
    timers = {"scanning": 0.0, "review": 0.0, "baseline": 0.0}
    for entry in audit.get("time_entries", []):
        phase = entry.get("phase")
        if phase in timers:
            timers[phase] += float(number(entry.get("seconds"), "Timer seconds"))
    total_seconds = timers["scanning"] + timers["review"]
    return {
        "report_version": 1,
        "data_label": "SYNTHETIC SOFTWARE TEST — not classroom accuracy" if synthetic else "PILOT — teacher reference, evaluation split only",
        "assessment_id": assessment.get("id"), "assessment_title": assessment.get("title"),
        "versions": {key: assessment.get(key) for key in ("version", "model", "model_digest", "num_ctx", "prompt_version")},
        "jobs": [{key: job.get(key) for key in ("id", "action", "status", "model_digest", "prompt_version", "rubric_version")} for job in audit.get("jobs", [])],
        "tuning_rows_excluded": sum(row["split"] == "tuning" for row in truth),
        "audit_responses_without_truth": len(responses) - len(truth),
        "overall": summarize(compared),
        "by_question_type": {kind: summarize([row for row in compared if row["question_type"] == kind]) for kind in sorted({row["question_type"] for row in compared})},
        "teacher_time_seconds": {**timers, "scanning_plus_review": total_seconds,
            "saving_vs_recorded_baseline": timers["baseline"] - total_seconds if timers["baseline"] else None},
        "time_scope": "All recorded assessment time, including tuning if done in the same assessment; compare equivalent workloads only.",
        "metric_notes": [
            "First recorded transcription and grading attempts are used; failures remain missing outputs, never zeroes.",
            "OCR CER/WER apply only to scans with an initial transcription. Missing/failed outputs are counted separately.",
            "NFC Unicode composition is normalized. Accents, spelling, case, and verb endings are preserved.",
            "CER is character edits/reference characters; WER is word edits/reference words. Rates can exceed 1. Blank-only denominators yield null.",
            "Unflagged means no initial machine review reason; teacher approval is still required for every answer.",
            "A first grade may follow a teacher transcription correction; this evaluates that assisted workflow, not autonomous grading.",
            "ID hashes detect repeated IDs, not the same paper reimported under new IDs. Keep physical tuning/evaluation documents separate.",
            "No population accuracy or probability estimate is implied by this small pilot.",
        ],
        "split_manifest": manifest, "responses": compared,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New JSON report path; existing files are never overwritten")
    parser.add_argument("--tuning-manifest", type=Path, help="Earlier report or split_manifest JSON for overlap rejection")
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args()
    try:
        audit = json.loads(args.audit.read_text(encoding="utf-8"))
        prior = json.loads(args.tuning_manifest.read_text(encoding="utf-8")) if args.tuning_manifest else None
        report = evaluate(audit, read_truth(args.truth), allow_synthetic=args.allow_synthetic, prior_tuning=prior)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"Evaluation stopped: {exc}\n")
    print(report["data_label"])
    print(f"Evaluated {report['overall']['responses']} responses; excluded {report['tuning_rows_excluded']} tuning rows.")
    print(f"Report: {args.out}")
    print("Question type | outputs / cases | over-awarded | under-awarded | unflagged grade errors")
    for kind, metrics in report["by_question_type"].items():
        print(f"{kind} | {metrics['initial_grade_outputs']} / {metrics['responses']} | {metrics['points_incorrectly_awarded']:g} | {metrics['points_incorrectly_withheld']:g} | {metrics['unflagged_grade_errors']}")


if __name__ == "__main__":
    main()
