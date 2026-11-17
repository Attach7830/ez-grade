"""Synthetic metric/holdout tests. These are not classroom accuracy evidence."""
from decimal import Decimal
import importlib.util
from pathlib import Path
import tempfile
import unittest

_path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_pilot.py"
_spec = importlib.util.spec_from_file_location("evaluate_pilot", _path)
pilot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pilot)


def reading(text, status="ok", flags=None):
    return {"kind": "transcription_suggested", "details": {"transcription": text, "status": status, "flags": flags or []}}


def scoring(points, reasons=None):
    return {"kind": "grade_suggested", "details": {"suggestion": {"suggested_points": points, "review_reasons": reasons or []}}}


def response(rid, *history, source="scan", original=None, submission=None):
    return {"id": rid, "submission_id": submission or "student-" + rid, "question_id": "1",
            "source_kind": source, "original_text": original, "history": list(history)}


def reference(rid, text, points, split="eval", kind="conjugation"):
    return {"response_id": rid, "split": split, "question_type": kind,
            "transcription": text, "points": Decimal(str(points)), "blank": text == ""}


def audit(*responses):
    return {"format_version": 1, "assessment": {"synthetic": True, "questions": [{"id": "1", "max_points": 2}]},
            "responses": list(responses), "jobs": [], "time_entries": []}


def evaluate(data, refs, **kwargs):
    return pilot.evaluate(data, refs, allow_synthetic=True, **kwargs)


class PilotMetricTests(unittest.TestCase):
    def test_initial_reading_and_score_survive_later_teacher_edits(self):
        item = response("a", reading("esta"), scoring(2), reading("está"), scoring(1))
        item.update(transcription="está", points=1, state="approved")
        result = evaluate(audit(item), [reference("a", "está", 1)])
        self.assertEqual(result["overall"]["scan_cer"], 0.25)
        self.assertEqual(result["overall"]["points_incorrectly_awarded"], 1)
        self.assertEqual(result["overall"]["unflagged_transcription_errors"], 1)
        self.assertEqual(result["overall"]["unflagged_grade_errors"], 1)

    def test_initial_failures_remain_missing_even_if_retry_succeeds(self):
        item = response("a", {"kind": "transcription_failed", "details": {"error": "timeout"}},
                        reading("tengo"), {"kind": "grade_failed", "details": {"error": "timeout"}}, scoring(2))
        result = evaluate(audit(item), [reference("a", "tengo", 2)])["overall"]
        self.assertEqual(result["scan_missing_or_failed_transcriptions"], 1)
        self.assertEqual(result["missing_or_failed_initial_grades"], 1)
        self.assertEqual(result["points_incorrectly_withheld"], 0)
        self.assertIsNone(result["scan_cer"])
        self.assertIsNone(result["grade_agreement_given_output"])

    def test_hallucinated_text_on_true_blank_has_no_fake_denominator(self):
        result = evaluate(audit(response("a", reading("hola"), scoring(0))), [reference("a", "", 0)])["overall"]
        self.assertEqual(result["true_blank_scans_with_invented_text"], 1)
        self.assertEqual(result["scan_character_edits"], 4)
        self.assertEqual(result["scan_reference_characters"], 0)
        self.assertIsNone(result["scan_cer"])
        self.assertEqual(result["unflagged_transcription_errors"], 1)

    def test_nonblank_read_as_blank_is_counted_and_flagged(self):
        result = evaluate(audit(response("a", reading("", status="blank"))), [reference("a", "perro", 2)])["overall"]
        self.assertEqual(result["nonblank_scans_read_as_blank"], 1)
        self.assertEqual(result["scan_cer"], 1)
        self.assertEqual(result["unflagged_transcription_errors"], 0)
        self.assertEqual(result["missing_or_failed_initial_grades"], 1)

    def test_unicode_composition_is_equivalent_but_accents_are_not_removed(self):
        result = evaluate(audit(response("a", reading("esta\u0301"), scoring(2)), response("b", reading("esta"), scoring(2))),
                          [reference("a", "está", 2), reference("b", "está", 2)])["overall"]
        self.assertEqual(result["scan_transcription_mismatches"], 1)
        self.assertEqual(result["scan_character_edits"], 1)
        self.assertEqual(result["scan_cer"], 1 / 8)

    def test_metrics_by_type_time_and_tuning_exclusion(self):
        data = audit(response("a", reading("esta"), scoring(2)),
                     response("b", scoring(0), source="typed", original="B"),
                     response("c", scoring(0), source="typed", original="B"))
        data["time_entries"] = [{"phase": "scanning", "seconds": 60}, {"phase": "review", "seconds": 120}, {"phase": "baseline", "seconds": 300}]
        result = evaluate(data, [reference("a", "está", 1), reference("b", "B", 2, kind="choice"), reference("c", "B", 2, split="tuning", kind="choice")])
        self.assertEqual(result["overall"]["responses"], 2)
        self.assertEqual(result["overall"]["scan_responses"], 1)
        self.assertEqual(result["overall"]["points_incorrectly_awarded"], 1)
        self.assertEqual(result["overall"]["points_incorrectly_withheld"], 2)
        self.assertEqual(result["by_question_type"]["choice"]["scan_responses"], 0)
        self.assertEqual(result["by_question_type"]["choice"]["responses"], 1)
        self.assertEqual(result["tuning_rows_excluded"], 1)
        self.assertEqual(result["teacher_time_seconds"]["scanning_plus_review"], 180)
        self.assertEqual(result["teacher_time_seconds"]["saving_vs_recorded_baseline"], 120)

    def test_out_of_range_grade_is_failed_output_not_clamped(self):
        result = evaluate(audit(response("a", reading("tengo"), scoring(99))), [reference("a", "tengo", 2)])["overall"]
        self.assertEqual(result["missing_or_failed_initial_grades"], 1)
        self.assertEqual(result["points_incorrectly_awarded"], 0)

    def test_same_submission_cannot_cross_split(self):
        data = audit(response("a", submission="same-paper"), response("b", submission="same-paper"))
        with self.assertRaisesRegex(ValueError, "same student submission"):
            evaluate(data, [reference("a", "tengo", 2, split="tuning"), reference("b", "tengo", 2)])

    def test_prior_tuning_manifest_rejects_reuse(self):
        with self.assertRaisesRegex(ValueError, "prior tuning"):
            evaluate(audit(response("a")), [reference("a", "tengo", 2)],
                     prior_tuning={"split_manifest": {"tuning_response_id_hashes": [pilot.digest_id("a")]}})

    def test_duplicate_truth_ids_and_mixed_versions_rejected(self):
        data = audit(response("a"))
        with self.assertRaisesRegex(ValueError, "Duplicate truth"):
            evaluate(data, [reference("a", "tengo", 2), reference("a", "tengo", 2)])
        data["jobs"] = [{"model_digest": "one"}, {"model_digest": "two"}]
        with self.assertRaisesRegex(ValueError, "mixes model_digest"):
            evaluate(data, [reference("a", "tengo", 2)])

    def test_synthetic_input_requires_explicit_acknowledgment(self):
        with self.assertRaisesRegex(ValueError, "Synthetic assessment"):
            pilot.evaluate(audit(response("a")), [reference("a", "tengo", 2)])
        result = evaluate(audit(response("a")), [reference("a", "tengo", 2)])
        self.assertIn("not classroom accuracy", result["data_label"])

    def test_truth_csv_preserves_accents_and_rejects_false_blank(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "truth.csv"
            path.write_text('response_id,split,question_type,transcription,points,blank\na,eval,translation,"Sí, tengo un perro.",2,false\n', encoding="utf-8")
            rows = pilot.read_truth(path)
            self.assertEqual(rows[0]["transcription"], "Sí, tengo un perro.")
            path.write_text('response_id,split,question_type,transcription,points,blank\na,eval,vocabulary,perro,2,true\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "true blanks"):
                pilot.read_truth(path)


if __name__ == "__main__":
    unittest.main()
