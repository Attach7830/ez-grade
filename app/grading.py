"""Comparison-only objective scoring and strict rubric-result validation."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .models import GradeResult, Question, comparison_text


_INSTRUCTION_PATTERNS = (
    r"\bignore\s+(?:all\s+)?(?:previous|prior|earlier|above|the rubric|the instructions)",
    r"\b(?:system|developer)\s+(?:prompt|message|instructions)\b",
    r"\b(?:award|give)\s+(?:me\s+)?(?:full|maximum|all)\s+(?:credit|points)\b",
    r"\bignora(?:r)?\s+(?:todas?\s+)?(?:las\s+)?(?:instrucciones|reglas|rúbrica)",
    r"\b(?:dame|otorga)\s+(?:todos?\s+)?(?:los\s+)?puntos\b",
    r"<\|(?:im_start|system|assistant|endoftext)\|>",
)


def content_review_reasons(text: str) -> list[str]:
    """Heuristic flag, not a security boundary or a measure of confidence."""
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _INSTRUCTION_PATTERNS):
        return ["Response contains instruction-like text; inspect the answer and proposed score."]
    return []


def grade_objective(question: Question, text: str) -> dict[str, Any]:
    if question.type not in {"choice", "exact"}:
        raise ValueError("Objective scoring requires a choice or exact-answer question.")
    if not isinstance(text, str):
        raise ValueError("A transcription is required; missing extraction is not a blank answer.")
    if not text.strip():
        raise ValueError("Confirm a true blank separately before assigning its score.")
    candidate = comparison_text(text, question.normalization)
    reasons = content_review_reasons(text)
    for accepted in question.accepted_answers:
        if candidate == comparison_text(accepted, question.normalization):
            return {
                "suggested_points": question.max_points,
                "criteria": [{"criterion_id": "answer", "points": question.max_points,
                              "evidence": text, "feedback": "Matches an approved answer."}],
                "feedback": "Correct under the approved answer key.",
                "review_reasons": reasons,
                "method": "deterministic",
            }
    for partial in question.partial_answers:
        if candidate == comparison_text(partial.answer, question.normalization):
            return {
                "suggested_points": partial.points,
                "criteria": [{"criterion_id": "answer", "points": partial.points,
                              "evidence": text, "feedback": partial.feedback}],
                "feedback": partial.feedback,
                "review_reasons": reasons + ["Matched a teacher-defined partial-credit answer."],
                "method": "deterministic",
            }
    return {
        "suggested_points": 0.0,
        "criteria": [{"criterion_id": "answer", "points": 0.0,
                      "evidence": text, "feedback": "No approved answer variant matched."}],
        "feedback": "Does not match an approved answer or partial-credit variant.",
        "review_reasons": reasons + ["Check for an acceptable alternative or transcription error."],
        "method": "deterministic",
    }


def grade_rubric_result(question: Question, text: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Reject invalid suggestions; never clamp, repair, or silently assign zero.

    Evidence checks detect nonexistent quotations. They cannot prove whether a
    real quotation justifies the proposed rubric interpretation.
    """
    if question.type != "rubric":
        raise ValueError("Model interpretation is only used for rubric questions.")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("A nonblank transcription is required for model interpretation.")
    parsed = GradeResult.model_validate(raw)
    criteria = {item.id: item for item in question.rubric}
    returned = [item.criterion_id for item in parsed.criteria]
    if len(returned) != len(set(returned)) or set(returned) != set(criteria):
        raise ValueError("Model must return each approved rubric criterion exactly once.")
    total = Decimal(0)
    for result in parsed.criteria:
        points = Decimal(str(result.points))
        if points > Decimal(str(criteria[result.criterion_id].max_points)):
            raise ValueError("Model points exceed a rubric criterion maximum.")
        if points > 0 and not result.evidence.strip():
            raise ValueError("Awarded points require evidence quoted from the student response.")
        if result.evidence and result.evidence not in text:
            raise ValueError("Model evidence is not a verbatim part of the student response.")
        total += points
    if total > Decimal(str(question.max_points)):
        raise ValueError("Model points exceed the question maximum.")
    if total != Decimal(str(parsed.suggested_points)):
        raise ValueError("Claimed score does not equal the sum of rubric criterion points.")
    result = parsed.model_dump()
    # The stored total is computed here, not trusted from generated text.
    result["suggested_points"] = float(total)
    result["review_reasons"] = list(dict.fromkeys(
        parsed.review_reasons + content_review_reasons(text)
    ))
    result["method"] = "model"
    return result
