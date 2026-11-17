"""Synthetic section-scaling tests; no model or classroom data."""
from copy import deepcopy
from decimal import Decimal, localcontext
import math
import unittest

from pydantic import ValidationError

from app.scoring import ScoringInput, ScalingSection, calculate_scores, compile_policy


def question(qid, maximum):
    return {"id": qid, "max_points": maximum}


def section(sid, qids, target):
    return {"id": sid, "label": sid, "question_ids": qids, "target_points": target}


class SectionScalingTests(unittest.TestCase):
    def test_twenty_point_section_becomes_ten(self):
        qs = [question("1", 10), question("2", 10)]
        result = calculate_scores(qs, [section("written", ["1", "2"], 10)], {"1": 9, "2": 7})
        self.assertEqual((result["raw_total"], result["raw_max_points"]), (16, 20))
        self.assertEqual((result["total"], result["max_points"]), (8, 10))
        self.assertEqual(result["total_display"], "8.00")
        self.assertEqual(result["sections"][0]["factor_exact"], {"numerator": "1", "denominator": "2"})
        self.assertEqual([q["points"] for q in result["questions"]], [4.5, 3.5])

    def test_mixed_sections_increase_decrease_and_unassigned(self):
        qs = [question("1", 10), question("2", 10), question("3", 5), question("4", 2)]
        sections = [section("writing", ["1", "2"], 10), section("speaking", ["3"], 10)]
        result = calculate_scores(qs, sections, {"1": 10, "2": 6, "3": 4, "4": 1})
        self.assertEqual(result["raw_total"], 21)
        self.assertEqual(result["total"], 17)
        self.assertEqual(result["max_points"], 22)
        self.assertEqual(result["questions"][-1]["factor"], 1)
        self.assertIsNone(result["questions"][-1]["section_id"])

    def test_thirds_round_total_once_not_each_question(self):
        qs = [question(str(i), 1) for i in range(1, 4)]
        result = calculate_scores(qs, [section("thirds", ["1", "2", "3"], 1)], {"1": 1, "2": 1, "3": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["total_exact"], {"numerator": "1", "denominator": "1"})
        self.assertEqual(result["sections"][0]["total"], 1)
        self.assertEqual([row["points_display"] for row in result["questions"]], ["0.33"] * 3)
        self.assertTrue(all(row["points_exact"] == {"numerator": "1", "denominator": "3"} for row in result["questions"]))

    def test_different_repeating_ratios_sum_exactly(self):
        qs = [question("1", 3), question("2", 6), question("3", 2)]
        sections = [section("a", ["1"], 1), section("b", ["2"], 1), section("c", ["3"], 1)]
        result = calculate_scores(qs, sections, {"1": 1, "2": 1, "3": 1})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["total_exact"], {"numerator": "1", "denominator": "1"})

    def test_half_up_and_raw_exact_value_preserved(self):
        result = calculate_scores([question("1", 2)], [], {"1": 1.005})
        self.assertEqual(result["raw_total_exact"], "1.005")
        self.assertEqual(result["raw_total"], 1.005)
        self.assertEqual(result["total"], 1.005)
        self.assertEqual(result["total_display"], "1.01")
        self.assertEqual(result["raw_total_display"], "1.01")

    def test_fine_precision_full_credit_never_exceeds_numeric_maximum(self):
        unscaled = calculate_scores([question("1", 0.125)], [], {"1": 0.125})
        self.assertEqual(unscaled["total"], 0.125)
        self.assertEqual(unscaled["total"], unscaled["max_points"])
        self.assertEqual(unscaled["total_display"], "0.13")
        scaled = calculate_scores([question("1", 20)], [section("a", ["1"], 0.005)], {"1": 20})
        self.assertEqual(scaled["total"], 0.005)
        self.assertEqual(scaled["total"], scaled["max_points"])
        self.assertEqual(scaled["sections"][0]["total"], 0.005)
        self.assertEqual(scaled["total_display"], "0.01")
        self.assertEqual(scaled["total_display"], scaled["max_points_display"])

    def test_fine_precision_partial_credit_stays_unrounded(self):
        result = calculate_scores([question("1", 20)], [section("a", ["1"], 0.005)], {"1": 16})
        self.assertEqual(result["total"], 0.004)
        self.assertLess(result["total"], result["max_points"])
        self.assertEqual(result["total_exact"], {"numerator": "1", "denominator": "250"})
        thirds = calculate_scores([question("1", 3)], [section("a", ["1"], 1)], {"1": 1})
        self.assertEqual(thirds["total"], 1 / 3)
        self.assertEqual(thirds["total_display"], "0.33")

    def test_tiny_legitimate_unscaled_scores_are_not_zeroed(self):
        result = calculate_scores([question("1", 1e-310)], [], {"1": 1e-310})
        self.assertEqual(result["raw_total"], 1e-310)
        self.assertEqual(result["total"], 1e-310)
        self.assertGreater(result["total"], 0)
        self.assertEqual(result["total_display"], "0.00")
        self.assertEqual(Decimal(result["raw_total_exact"]), Decimal("1e-310"))

    def test_partial_missing_and_failed_answers_are_not_zeroes(self):
        qs = [question("1", 10), question("2", 10), question("3", 2)]
        for points in ({"1": 8, "3": 1}, {"1": 8, "2": None, "3": 1}):
            result = calculate_scores(qs, [section("written", ["1", "2"], 10)], points)
            self.assertFalse(result["complete"])
            self.assertIsNone(result["total"])
            self.assertIsNone(result["raw_total"])
            self.assertIsNone(result["sections"][0]["total"])
            self.assertIsNone(result["questions"][1]["points"])
            self.assertEqual(result["missing_question_ids"], ["2"])
            self.assertEqual(result["questions"][0]["points"], 4)

    def test_completed_section_available_when_other_section_missing(self):
        result = calculate_scores([question("1", 2), question("2", 2)],
                                  [section("a", ["1"], 1), section("b", ["2"], 1)], {"1": 2})
        self.assertIsNone(result["total"])
        self.assertEqual(result["sections"][0]["total"], 1)
        self.assertIsNone(result["sections"][1]["total"])

    def test_zero_is_a_real_completed_score(self):
        result = calculate_scores([question("1", 2)], [], {"1": 0})
        self.assertTrue(result["complete"])
        self.assertEqual(result["total"], 0)

    def test_sections_must_be_disjoint_unique_and_known(self):
        qs = [question("1", 1), question("2", 1)]
        invalid = [
            [section("a", ["1"], 1), section("b", ["1"], 1)],
            [section("same", ["1"], 1), section("same", ["2"], 1)],
            [section("a", ["1", "1"], 1)],
            [section("a", ["missing"], 1)],
            [section("a", [], 1)],
        ]
        for sections in invalid:
            with self.subTest(sections=sections), self.assertRaises(ValueError):
                compile_policy(qs, sections)

    def test_target_points_and_raw_scores_are_finite_and_bounded(self):
        qs = [question("1", 2)]
        for target in (0, -1, math.inf, -math.inf, math.nan, True, "1"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                compile_policy(qs, [section("a", ["1"], target)])
        for score in (-1, 2.001, math.inf, math.nan, True, "1"):
            with self.subTest(score=score), self.assertRaises(ValueError):
                calculate_scores(qs, [], {"1": score})
        with self.assertRaises(ValueError):
            calculate_scores(qs, [], {"not-a-question": 1})

    def test_reject_duplicate_questions_and_nonpositive_question_maxima(self):
        for qs in ([question("1", 0)], [question("1", -1)], [question("1", math.inf)],
                   [question("1", 1), question("1", 2)], []):
            with self.subTest(qs=qs), self.assertRaises(ValueError):
                compile_policy(qs, [])

    def test_overflow_is_rejected_before_json_output(self):
        with self.assertRaisesRegex(ValueError, "finite numeric range"):
            compile_policy([question("1", 1e308), question("2", 1e308)], [])
        with self.assertRaisesRegex(ValueError, "finite numeric range"):
            compile_policy([question("1", 1), question("2", 1)], [section("a", ["1"], 1e308), section("b", ["2"], 1e308)])
        with self.assertRaisesRegex(ValueError, "finite numeric range"):
            compile_policy([question("1", 1e-308)], [section("a", ["1"], 1e308)])

    def test_inputs_are_immutable_and_caller_decimal_precision_does_not_leak(self):
        qs = [question("1", 0.1), question("2", 0.2)]
        sections = [section("a", ["1", "2"], 1)]
        points = {"1": 0.1, "2": 0.2}
        before = deepcopy((qs, sections, points))
        with localcontext() as context:
            context.prec = 2
            result = calculate_scores(qs, sections, points)
        self.assertEqual((qs, sections, points), before)
        self.assertEqual(result["raw_total_exact"], "0.3")
        self.assertEqual(result["total"], 1)

    def test_request_models_validate_revision_note_and_preview(self):
        payload = ScoringInput(revision=0, sections=[ScalingSection(**section("a", ["1"], 10))])
        self.assertEqual(payload.note, "")
        self.assertIsNone(payload.preview_hash)
        for changes in ({"revision": True}, {"revision": -1}, {"revision": "0"},
                        {"note": "x" * 1001}, {"preview_hash": "x" * 129}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ScoringInput.model_validate({"revision": 0, "sections": [], **changes})


if __name__ == "__main__":
    unittest.main()
