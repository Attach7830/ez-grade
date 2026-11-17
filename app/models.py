"""Validated assessment and model-result contracts.

Stored student text is never normalized here. Normalization is a comparison-only
operation in :mod:`app.grading`.
"""

from __future__ import annotations

import re
import math
import unicodedata
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_MODEL = "qwen3-vl:4b-instruct-q4_K_M"
PointValue = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
PositivePoints = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Normalization(StrictModel):
    trim: bool = True
    collapse_whitespace: bool = True
    case_sensitive: bool = False
    ignore_terminal_punctuation: bool = False
    accent_policy: Literal["strict", "ignore_acute"] = "strict"


def comparison_text(text: str, rules: Normalization) -> str:
    """Return a comparison key; never use this as a student transcription.

    NFC equates Unicode encodings of the same visible character. The opt-in
    acute policy only maps accented Spanish vowels. It preserves ñ and ü.
    """
    value = unicodedata.normalize("NFC", text)
    if rules.trim:
        value = value.strip()
    if rules.collapse_whitespace:
        value = re.sub(r"\s+", " ", value)
    if not rules.case_sensitive:
        value = value.casefold()
    if rules.ignore_terminal_punctuation:
        value = re.sub(r"[.!?;,:\u2026]+$", "", value)
    if rules.accent_policy == "ignore_acute":
        value = value.translate(str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU"))
    return value


class PartialAnswer(StrictModel):
    answer: str = Field(min_length=1, max_length=5000)
    points: PointValue
    feedback: str = Field(default="Partial credit under the approved key.", max_length=2000)


class Criterion(StrictModel):
    id: Identifier
    description: str = Field(min_length=1, max_length=5000)
    max_points: PositivePoints

    @field_validator("description")
    @classmethod
    def nonempty_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A rubric criterion needs a description.")
        return value


class Question(StrictModel):
    id: Identifier
    type: Literal["choice", "exact", "rubric"]
    prompt: str = Field(min_length=1, max_length=20000)
    max_points: PositivePoints
    accepted_answers: list[str] = Field(default_factory=list, max_length=200)
    partial_answers: list[PartialAnswer] = Field(default_factory=list, max_length=200)
    normalization: Normalization = Field(default_factory=Normalization)
    rubric: list[Criterion] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=12000)

    @field_validator("prompt")
    @classmethod
    def nonempty_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A question needs a prompt.")
        return value

    @model_validator(mode="after")
    def coherent_scoring(self) -> "Question":
        if self.type == "rubric":
            if not self.rubric:
                raise ValueError("Interpretive questions require at least one rubric criterion.")
            ids = [criterion.id for criterion in self.rubric]
            if len(ids) != len(set(ids)):
                raise ValueError("Rubric criterion IDs must be unique within a question.")
            total = sum((Decimal(str(item.max_points)) for item in self.rubric), Decimal(0))
            if total != Decimal(str(self.max_points)):
                raise ValueError("Rubric criterion maxima must sum exactly to question points.")
            if self.accepted_answers or self.partial_answers:
                raise ValueError("Rubric questions use criteria; put examples and alternatives in notes.")
            return self
        if self.rubric:
            raise ValueError("Objective questions use accepted and partial answers, not rubric criteria.")
        if not self.accepted_answers:
            raise ValueError("Objective questions require at least one accepted answer.")
        seen: set[str] = set()
        for answer in self.accepted_answers:
            if len(answer) > 5000:
                raise ValueError("An accepted answer must be at most 5000 characters.")
            key = comparison_text(answer, self.normalization)
            if not key.strip():
                raise ValueError("Blank answers cannot be accepted answer variants.")
            if key in seen:
                raise ValueError("Answer variants collide under this question's normalization rules.")
            seen.add(key)
        for partial in self.partial_answers:
            if Decimal(str(partial.points)) >= Decimal(str(self.max_points)):
                raise ValueError("A partial answer must award fewer points than the question maximum.")
            key = comparison_text(partial.answer, self.normalization)
            if not key.strip():
                raise ValueError("Blank answers cannot be partial-credit variants.")
            if key in seen:
                raise ValueError("Answer variants collide under this question's normalization rules.")
            seen.add(key)
        return self


class AssessmentInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    course: str = Field(default="", max_length=200)
    instructions: str = Field(default="", max_length=20000)
    questions: list[Question] = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    num_ctx: int | None = Field(default=None, ge=2048, le=8192, strict=True)
    synthetic: bool = False
    key_source: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def assessment_valid(self) -> "AssessmentInput":
        if not self.title.strip():
            raise ValueError("An assessment needs a title.")
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Question IDs must be unique within an assessment.")
        total = sum((Decimal(str(question.max_points)) for question in self.questions), Decimal(0))
        if not math.isfinite(float(total)):
            raise ValueError("The assessment maximum must fit a finite numeric total.")
        if self.model is not None and not self.model.strip():
            raise ValueError("Model cannot be an empty string.")
        return self


class CriterionResult(StrictModel):
    criterion_id: Identifier
    points: PointValue
    evidence: str = Field(max_length=12000)
    feedback: str = Field(max_length=2000)


class GradeResult(StrictModel):
    suggested_points: PointValue
    criteria: list[CriterionResult] = Field(min_length=1, max_length=30)
    feedback: str = Field(max_length=2000)
    review_reasons: list[str] = Field(max_length=30)

    @field_validator("review_reasons")
    @classmethod
    def bounded_reasons(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 2000 for item in values):
            raise ValueError("Review reasons must be nonempty and at most 2000 characters each.")
        return values


class TranscriptionResult(StrictModel):
    text: str = Field(max_length=12000)
    status: Literal["ok", "blank", "ambiguous", "failed"]
    flags: list[str] = Field(max_length=30)

    @model_validator(mode="after")
    def status_matches_text(self) -> "TranscriptionResult":
        if self.status == "blank" and self.text != "":
            raise ValueError("A blank extraction must contain exactly an empty string.")
        if self.status == "ok" and not self.text.strip():
            raise ValueError("An empty extraction cannot have ok status.")
        if self.status in {"ambiguous", "failed"} and not self.flags:
            raise ValueError("Ambiguous and failed extraction require an explanation in flags.")
        if any(not item.strip() or len(item) > 2000 for item in self.flags):
            raise ValueError("Transcription flags must be nonempty and at most 2000 characters each.")
        return self
