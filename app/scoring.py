"""Pure, teacher-controlled section scaling applied after raw grading.

Raw points are Decimal values. Exact rational weights are retained throughout;
only optional display strings are rounded with Decimal/ROUND_HALF_UP. Neither the question key nor raw scores
are modified. Missing raw points remain missing.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
import math
from typing import Any

from pydantic import Field, field_validator, model_validator

from .models import Identifier, PositivePoints, StrictModel


class ScalingSection(StrictModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=200)
    question_ids: list[Identifier] = Field(min_length=1, max_length=100)
    target_points: PositivePoints

    @field_validator("label")
    @classmethod
    def meaningful_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A scoring section needs a label.")
        return value

    @field_validator("question_ids")
    @classmethod
    def unique_questions(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("A question may appear only once in a scoring section.")
        return values


class ScoringInput(StrictModel):
    revision: int = Field(ge=0, strict=True)
    sections: list[ScalingSection] = Field(default_factory=list, max_length=100)
    note: str = Field(default="", max_length=1000)
    preview_hash: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def disjoint_sections(self) -> "ScoringInput":
        ids: set[str] = set()
        assigned: set[str] = set()
        for section in self.sections:
            if section.id in ids:
                raise ValueError("Scoring section IDs must be unique.")
            ids.add(section.id)
            if assigned.intersection(section.question_ids):
                raise ValueError("Scoring sections must not overlap.")
            assigned.update(section.question_ids)
        return self


def _number(value: Any, label: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label} must be a finite number.")
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}.")
    _finite_float(result, label)
    return result


def _finite_float(value: Decimal | Fraction, label: str) -> float:
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} is outside the supported finite numeric range.") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{label} is outside the supported finite numeric range.")
    if value != 0 and converted == 0:
        raise ValueError(f"{label} is too small to represent as a numeric value.")
    return converted


def _text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _sum(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    # Enough precision for the exact finite decimal sum, including widely
    # separated exponents. Do not inherit a caller's small Decimal context.
    highest = max(value.adjusted() for value in values)
    lowest = min(value.as_tuple().exponent for value in values)
    with localcontext() as context:
        context.prec = max(28, highest - lowest + len(str(len(values))) + 4)
        return sum(values, Decimal(0))


def _exact(value: Fraction) -> dict[str, str]:
    # Decimal(int) avoids Python's decimal-string limit for a large exact ratio.
    return {"numerator": format(Decimal(value.numerator), "f"),
            "denominator": format(Decimal(value.denominator), "f")}


def _display(value: Fraction) -> str:
    numerator = Decimal(value.numerator)
    denominator = Decimal(value.denominator)
    # Sufficient precision to distinguish a ratio from a half-cent boundary,
    # even when a finite input produces a long repeating expansion.
    with localcontext() as context:
        context.prec = max(80, numerator.adjusted() + denominator.adjusted() + 12)
        rounded = (numerator / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, ".2f")


def _prepare(questions: Sequence[Any], sections: Sequence[Any]) -> tuple[list[tuple[str, Decimal]], list[ScalingSection]]:
    if not isinstance(questions, Sequence) or isinstance(questions, (str, bytes)) or not 1 <= len(questions) <= 100:
        raise ValueError("Provide between 1 and 100 assessment questions.")
    records: list[tuple[str, Decimal]] = []
    seen: set[str] = set()
    for question in questions:
        record = question.model_dump() if hasattr(question, "model_dump") else question
        if not isinstance(record, Mapping):
            raise ValueError("Each assessment question must provide its ID and maximum points.")
        qid = record.get("id")
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ValueError("Assessment question IDs must be nonempty and unique.")
        seen.add(qid)
        records.append((qid, _number(record.get("max_points"), f"Question {qid} maximum", positive=True)))
    policy = ScoringInput.model_validate({"revision": 0, "sections": list(sections)})
    for section in policy.sections:
        unknown = set(section.question_ids).difference(seen)
        if unknown:
            raise ValueError("Scoring section contains unknown question IDs: " + ", ".join(sorted(unknown)))
    return records, policy.sections


def _compile(records: list[tuple[str, Decimal]], sections: list[ScalingSection]) -> tuple[dict, dict[str, Fraction]]:
    raw_maxima = dict(records)
    assignment = {qid: section.id for section in sections for qid in section.question_ids}
    factors: dict[str, Fraction] = {}
    section_rows = []
    for section in sections:
        raw_max = _sum([raw_maxima[qid] for qid in section.question_ids])
        target = _number(section.target_points, "Section target", positive=True)
        factor = Fraction(target) / Fraction(raw_max)
        factor_number = _finite_float(factor, "Section scale factor")
        factors.update({qid: factor for qid in section.question_ids})
        section_rows.append({
            "id": section.id, "label": section.label, "question_ids": list(section.question_ids),
            "target_points": _finite_float(target, "Section target"),
            "raw_max_points": _finite_float(raw_max, "Section raw maximum"),
            "max_points": _finite_float(target, "Section maximum"),
            "raw_max_points_exact": _text(raw_max), "max_points_exact": _text(target),
            "raw_max_points_display": _display(Fraction(raw_max)),
            "max_points_display": _display(Fraction(target)),
            "factor": factor_number, "factor_exact": _exact(factor),
        })
    question_rows = []
    for qid, raw_max in records:
        factor = factors.setdefault(qid, Fraction(1))
        effective = Fraction(raw_max) * factor
        question_rows.append({
            "id": qid, "section_id": assignment.get(qid),
            "raw_max_points": _finite_float(raw_max, "Question raw maximum"),
            "max_points": _finite_float(effective, "Question effective maximum"),
            "raw_max_points_exact": _text(raw_max), "max_points_exact": _exact(effective),
            "factor": _finite_float(factor, "Question scale factor"), "factor_exact": _exact(factor),
        })
    raw_max = _sum(list(raw_maxima.values()))
    adjusted_max = _sum([_number(section.target_points, "Section target", positive=True) for section in sections]
                        + [maximum for qid, maximum in records if qid not in assignment])
    return {
        "raw_max_points": _finite_float(raw_max, "Assessment raw maximum"),
        "max_points": _finite_float(adjusted_max, "Assessment adjusted maximum"),
        "raw_max_points_exact": _text(raw_max), "max_points_exact": _text(adjusted_max),
        "raw_max_points_display": _display(Fraction(raw_max)),
        "max_points_display": _display(Fraction(adjusted_max)),
        "sections": section_rows, "questions": question_rows,
    }, factors


def compile_policy(questions: Sequence[Any], sections: Sequence[Any]) -> dict:
    """Validate disjoint sections and return JSON-ready maxima and exact factors.

Questions may be Question models or mappings with id/max_points. No input is
modified. Unassigned questions receive factor 1. Numeric maxima are unrounded;
*_display fields use two-decimal HALF_UP formatting.
"""
    records, parsed_sections = _prepare(questions, sections)
    return _compile(records, parsed_sections)[0]


def calculate_scores(questions: Sequence[Any], sections: Sequence[Any], points: Mapping[str, float | None]) -> dict:
    """Scale bounded raw scores without converting missing answers into zeroes.

`raw_total` remains an unrounded raw sum; `total` is rounded once to two decimal
places. Per-question `points` are unrounded contributions. Exact rational fields
preserve repeating factors; display fields are safe to use directly in CSV.
An omitted question or explicit None leaves complete=False and both totals=None.
"""
    records, parsed_sections = _prepare(questions, sections)
    result, factors = _compile(records, parsed_sections)
    if not isinstance(points, Mapping):
        raise ValueError("Raw points must be a mapping from question IDs to scores or None.")
    raw_maxima = dict(records)
    if set(points).difference(raw_maxima):
        raise ValueError("Raw points contain unknown question IDs.")
    raw: dict[str, Decimal | None] = {}
    for qid, maximum in records:
        value = points.get(qid)
        if value is None:
            raw[qid] = None
            continue
        score = _number(value, f"Question {qid} raw score")
        if score > maximum:
            raise ValueError(f"Question {qid} raw score exceeds its approved maximum.")
        raw[qid] = score
    for row in result["questions"]:
        score = raw[row["id"]]
        weighted = Fraction(score) * factors[row["id"]] if score is not None else None
        row.update(raw_points=_finite_float(score, "Raw question score") if score is not None else None,
                   raw_points_exact=_text(score) if score is not None else None,
                   points=_finite_float(weighted, "Adjusted question score") if weighted is not None else None,
                   points_exact=_exact(weighted) if weighted is not None else None,
                   points_display=_display(weighted) if weighted is not None else None)
    for section in result["sections"]:
        values = [raw[qid] for qid in section["question_ids"]]
        complete = all(value is not None for value in values)
        raw_total = _sum(values) if complete else None
        exact_total = Fraction(raw_total) * factors[section["question_ids"][0]] if complete else None
        total = _finite_float(exact_total, "Adjusted section total") if complete else None
        displayed = _display(exact_total) if complete else None
        section.update(complete=complete,
                       raw_total=_finite_float(raw_total, "Raw section total") if complete else None,
                       raw_total_exact=_text(raw_total) if complete else None,
                       raw_total_display=_display(Fraction(raw_total)) if complete else None,
                       total=total, total_display=displayed,
                       total_exact=_exact(exact_total) if complete else None)
    missing = [qid for qid, value in raw.items() if value is None]
    complete = not missing
    raw_total = _sum(list(raw.values())) if complete else None
    exact_total = sum((Fraction(raw[qid]) * factors[qid] for qid, _ in records), Fraction(0)) if complete else None
    total = _finite_float(exact_total, "Adjusted assessment total") if complete else None
    displayed = _display(exact_total) if complete else None
    result.update(complete=complete, missing_question_ids=missing,
                  raw_total=_finite_float(raw_total, "Raw assessment total") if complete else None,
                  raw_total_exact=_text(raw_total) if complete else None,
                  raw_total_display=_display(Fraction(raw_total)) if complete else None,
                  total=total, total_display=displayed,
                  total_exact=_exact(exact_total) if complete else None)
    return result
