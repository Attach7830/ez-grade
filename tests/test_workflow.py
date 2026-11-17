"""Synthetic end-to-end workflow tests with an injected fake inference client.

No model is downloaded or called and these tests do not measure classroom
transcription or grading accuracy. The real persistence, routes, worker,
rasterizer, scoring code, and approval boundaries are exercised.
"""

from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.config import Settings
from app.grading import grade_rubric_result
from app.main import create_app
from app.ollama import ModelDriftError, OllamaError


PASSWORD = "synthetic-local-test-password"
DIGEST = "a" * 64


def objective_questions():
    return [
        {"id": "1", "type": "choice", "prompt": "Select the Spanish word for apple: A. casa B. manzana",
         "max_points": 1, "accepted_answers": ["B"]},
        {"id": "2", "type": "exact", "prompt": "Completa: Ella ___ en casa.",
         "max_points": 2, "accepted_answers": ["está"]},
    ]


def rubric_questions():
    return [{"id": "1", "type": "rubric", "prompt": "Translate: I eat an apple.", "max_points": 2,
             "rubric": [{"id": "verb", "description": "Correct first-person verb como", "max_points": 1},
                        {"id": "object", "description": "Correct object una manzana", "max_points": 1}]}]


def synthetic_scan(format="PNG"):
    output = io.BytesIO()
    image = Image.new("RGB", (600, 400), "white")
    ImageDraw.Draw(image).text((30, 30), "SYNTHETIC TEST ONLY\n1. B\n2. esta", fill="black")
    image.save(output, format=format)
    image.close()
    return output.getvalue()


class FakeInference:
    """Test-only dependency injection, never available as an application mode."""

    def __init__(self):
        self.digest = DIGEST
        self.version = "0.13.0"
        self.calls = []
        self.graded_questions = []
        self.transcription = {"text": "B", "status": "ok", "flags": []}
        self.transcription_error = None
        self.grading_error = None
        self.structured_result = None
        self.block_grading = False
        self.version_after_grade = None
        self.grading_started = threading.Event()
        self.grading_release = threading.Event()

    def inspect(self, model, require_vision=False):
        self.calls.append(("inspect", model, require_vision))
        return {"model": model, "digest": self.digest, "ollama_version": self.version,
                "capabilities": ["completion", "vision"]}

    def list_models(self):
        return {"ok": True, "version": self.version,
                "models": [{"name": "qwen3-vl:4b-instruct-q4_K_M", "digest": self.digest, "size": 3300000000}]}

    def transcribe(self, model, image_path, expected_digest=None, num_ctx=4096):
        self.calls.append(("transcribe", model, str(image_path)))
        if expected_digest != self.digest:
            raise ModelDriftError("Synthetic model digest changed.")
        if self.transcription_error:
            raise self.transcription_error
        return copy.deepcopy(self.transcription)

    def grade(self, model, question, text, expected_digest=None, num_ctx=4096):
        self.calls.append(("grade", model, text))
        self.graded_questions.append(question.model_dump())
        self.grading_started.set()
        if self.block_grading and not self.grading_release.wait(5):
            raise OllamaError("Synthetic test did not release the model request.")
        if expected_digest != self.digest:
            raise ModelDriftError("Synthetic model digest changed.")
        if self.grading_error:
            raise self.grading_error
        # Fixed synthetic behavior for exercising the review pipeline, not a
        # substitute grading implementation or evidence of model quality.
        raw = {"suggested_points": question.max_points,
               "criteria": [{"criterion_id": c.id, "points": c.max_points,
                             "evidence": text, "feedback": "Synthetic criterion feedback."} for c in question.rubric],
               "feedback": "Synthetic model suggestion.", "review_reasons": []}
        result = grade_rubric_result(question, text, raw)
        if self.version_after_grade:
            # Simulates replacement between this completed result and the
            # next response's preflight, not valid live-model behavior.
            self.version = self.version_after_grade
        return result

    def chat_structured(self, model, schema, system, user, images=None, expected_digest=None, num_ctx=4096):
        self.calls.append(("chat_structured", model, user))
        if expected_digest != self.digest:
            raise ModelDriftError("Synthetic model digest changed.")
        return copy.deepcopy(self.structured_result if self.structured_result is not None
                             else {"text": "El gato está aquí."})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.directory.name), PASSWORD, "synthetic-secret-" * 4)
        self.fake = FakeInference()
        self.app = create_app(self.settings, client=self.fake)
        self.client = TestClient(self.app, headers={"X-Grader-Request": "1"})
        self.client.__enter__()
        self.assertEqual(self.client.post("/api/login", json={"password": PASSWORD}).status_code, 200)

    def tearDown(self):
        self.fake.grading_release.set()
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def checked(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text)
        return response.json() if response.headers.get("content-type", "").startswith("application/json") else response

    def assessment(self, questions=None, approve=True, title="SYNTHETIC workflow assessment"):
        payload = {"title": title, "course": "Spanish 2 Honors", "instructions": "SYNTHETIC TEST ONLY",
                   "questions": questions or objective_questions(), "synthetic": True}
        assessment = self.checked(self.client.post("/api/assessments", json=payload))
        if approve:
            assessment = self.checked(self.client.post(f"/api/assessments/{assessment['id']}/approve"))
        return assessment

    def typed(self, aid, answers, student="SYNTHETIC-A"):
        return self.checked(self.client.post(f"/api/assessments/{aid}/submissions/typed",
                                            json={"student_id": student, "answers": answers}))

    def responses(self, aid):
        return self.checked(self.client.get(f"/api/assessments/{aid}/responses"))

    def response(self, rid):
        return self.checked(self.client.get(f"/api/responses/{rid}"))

    def wait_job(self, jid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.checked(self.client.get(f"/api/jobs/{jid}"))
            if result["status"] not in {"queued", "running"}:
                return result
            time.sleep(0.005)
        self.fail("Synthetic worker did not finish within five seconds.")

    def job(self, aid, action="grade", response_ids=None, wait=True):
        payload = {"action": action}
        if response_ids is not None:
            payload["response_ids"] = response_ids
        result = self.checked(self.client.post(f"/api/assessments/{aid}/jobs", json=payload))
        return self.wait_job(result["id"]) if wait else result

    def approve(self, rid):
        current = self.response(rid)
        return self.checked(self.client.post(f"/api/responses/{rid}/approve", json={"revision": current["revision"]}))

    def scan(self, aid, student="SYNTHETIC-SCAN", format="PNG"):
        content = synthetic_scan(format)
        extension = "pdf" if format == "PDF" else "png"
        mime = "application/pdf" if extension == "pdf" else "image/png"
        result = self.checked(self.client.post(f"/api/assessments/{aid}/submissions/scan",
                              data={"student_id": student}, files={"file": ("synthetic." + extension, content, mime)}))
        return result, content

    def crop(self, response, **overrides):
        payload = {"revision": response["revision"], "page": 0, "x": 0.01, "y": 0.01, "width": 0.8, "height": 0.8}
        payload.update(overrides)
        return self.checked(self.client.put(f"/api/responses/{response['id']}/region", json=payload))

    def test_authentication_and_request_boundaries(self):
        self.checked(self.client.post("/api/logout"))
        self.assertEqual(self.client.get("/api/assessments").status_code, 401)
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.post("/api/login", json={"password": PASSWORD},
                         headers={"X-Grader-Request": ""}).status_code, 403)
        self.assertEqual(self.client.post("/api/login", json={"password": PASSWORD},
                         headers={"Origin": "http://unrelated.example"}).status_code, 403)
        response = self.client.post("/api/login", json={"password": PASSWORD})
        self.checked(response)
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertIn("samesite=strict", response.headers["set-cookie"].lower())
        settings = self.checked(self.client.get("/api/settings"))
        self.assertNotIn("password", settings)
        self.assertNotIn("session_secret", settings)

    def test_key_must_be_approved_then_is_frozen(self):
        a = self.assessment(approve=False)
        self.typed(a["id"], {"1": "B", "2": "está"})
        self.assertEqual(self.client.post(f"/api/assessments/{a['id']}/jobs", json={"action": "grade"}).status_code, 409)
        answer = self.responses(a["id"])[0]
        self.assertEqual(self.client.patch(f"/api/responses/{answer['id']}", json={
            "revision": answer["revision"], "points": 1}).status_code, 409)
        self.assertEqual(self.fake.calls, [])
        approved = self.checked(self.client.post(f"/api/assessments/{a['id']}/approve"))
        self.assertEqual(approved["status"], "approved")
        payload = {"title": "Changed", "course": "Spanish 2 CP", "questions": objective_questions()}
        self.assertEqual(self.client.put(f"/api/assessments/{a['id']}", json=payload).status_code, 409)

    def test_objective_batch_never_calls_ollama_or_approves_scores(self):
        a = self.assessment()
        self.typed(a["id"], {"1": "B", "2": "está"}, "SYNTHETIC-A")
        self.typed(a["id"], {"1": "C", "2": "esta"}, "SYNTHETIC-B")
        job = self.job(a["id"])
        self.assertEqual((job["status"], job["done"], job["errors"]), ("completed", 4, 0))
        self.assertEqual(self.fake.calls, [])
        indexed = {(r["student_id"], r["question_id"]): r for r in self.responses(a["id"])}
        self.assertEqual(indexed[("SYNTHETIC-A", "1")]["points"], 1)
        self.assertEqual(indexed[("SYNTHETIC-A", "2")]["points"], 2)
        self.assertEqual(indexed[("SYNTHETIC-B", "2")]["points"], 0)
        self.assertEqual(indexed[("SYNTHETIC-B", "2")]["transcription"], "esta")
        self.assertTrue(all(r["state"] != "approved" for r in indexed.values()))
        self.assertEqual(self.client.get(f"/api/assessments/{a['id']}/export.csv").status_code, 409)

    def test_missing_question_is_pending_and_explicit_blank_is_distinct(self):
        a = self.assessment()
        self.typed(a["id"], {"1": ""})
        first, missing = self.responses(a["id"])
        self.assertEqual(first["transcription_status"], "blank")
        self.assertEqual(missing["transcription_status"], "pending")
        self.assertIsNone(missing["transcription"])
        self.job(a["id"])
        self.assertEqual(self.response(first["id"])["points"], 0)
        self.assertIsNone(self.response(missing["id"])["points"])
        job = self.job(a["id"], response_ids=[missing["id"]])
        self.assertEqual(job["errors"], 1)
        failed = self.response(missing["id"])
        self.assertIsNone(failed["points"])
        self.assertEqual(failed["state"], "needs_review")
        self.assertEqual(self.client.post(f"/api/responses/{failed['id']}/approve", json={"revision": failed["revision"]}).status_code, 409)

    def test_transcription_edit_invalidates_score_and_keeps_original_suggestions(self):
        a = self.assessment()
        self.typed(a["id"], {"1": "B", "2": "esta"})
        self.job(a["id"])
        answer = self.responses(a["id"])[1]
        self.assertEqual(answer["suggested_points"], 0)
        edit = self.checked(self.client.patch(f"/api/responses/{answer['id']}", json={
            "revision": answer["revision"], "transcription": "está", "transcription_confirmed": True}))
        self.assertIsNone(edit["points"])
        self.assertIsNone(edit["suggestion"])
        self.assertEqual(edit["original_text"], "esta")
        original = next(h for h in edit["history"] if h["kind"] == "grade_suggested")
        self.assertEqual(original["details"]["suggestion"]["suggested_points"], 0)
        self.job(a["id"], response_ids=[answer["id"]])
        current = self.response(answer["id"])
        edited = self.checked(self.client.patch(f"/api/responses/{answer['id']}", json={
            "revision": current["revision"], "points": 1.5, "review_note": "Synthetic teacher override."}))
        self.assertEqual(edited["suggestion"]["suggested_points"], 2)
        self.assertEqual(edited["points"], 1.5)
        approved = self.approve(answer["id"])
        self.assertEqual(approved["state"], "approved")
        self.assertEqual(self.client.patch(f"/api/responses/{answer['id']}", json={"revision": approved["revision"], "points": 1}).status_code, 409)
        reopened = self.checked(self.client.post(f"/api/responses/{answer['id']}/reopen", json={"revision": approved["revision"]}))
        self.assertIsNone(reopened["approved_at"])
        self.assertEqual(reopened["state"], "needs_review")

    def test_invalid_scores_and_combined_transcription_score_edits_are_rejected(self):
        a = self.assessment()
        self.typed(a["id"], {"1": "B", "2": "está"})
        answer = self.responses(a["id"])[0]
        for points in [-1, 1.5, "nan", "1", True, False, None]:
            with self.subTest(points=points):
                response = self.client.patch(f"/api/responses/{answer['id']}", json={"revision": answer["revision"], "points": points})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.patch(f"/api/responses/{answer['id']}", json={
            "revision": answer["revision"], "transcription": "C", "points": 0}).status_code, 422)
        self.assertIsNone(self.response(answer["id"])["points"])

    def test_final_csv_requires_every_approval_and_computes_totals(self):
        a = self.assessment()
        self.typed(a["id"], {"1": "B", "2": "está"}, "=SYNTHETIC-FORMULA")
        self.job(a["id"])
        answers = self.responses(a["id"])
        self.approve(answers[0]["id"])
        self.assertEqual(self.client.get(f"/api/assessments/{a['id']}/export.csv").status_code, 409)
        self.approve(answers[1]["id"])
        response = self.client.get(f"/api/assessments/{a['id']}/export.csv")
        self.assertEqual(response.status_code, 200)
        row = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))[0]
        self.assertEqual(row["student_id"], "'=SYNTHETIC-FORMULA")
        self.assertEqual(float(row["total"]), 3)
        self.assertEqual(float(row["max_points"]), 3)
        self.assertEqual(row["percentage"], "100.00")
        self.assertEqual(row["assessment_version"], a["version"])

    def test_image_and_pdf_upload_preserve_original_bytes_and_crop(self):
        a = self.assessment()
        for format in ["PNG", "PDF"]:
            with self.subTest(format=format):
                sub, original = self.scan(a["id"], "SYNTHETIC-" + format, format)
                self.assertEqual(len(sub["pages"]), 1)
                self.assertEqual(self.client.get(sub["original_url"]).content, original)
                self.assertEqual(self.client.get(sub["pages"][0]["url"]).status_code, 200)
                answer = next(r for r in self.responses(a["id"]) if r["submission_id"] == sub["id"])
                self.assertIsNone(answer["transcription"])
                self.assertEqual(self.client.post(f"/api/assessments/{a['id']}/jobs",
                                 json={"action": "transcribe", "response_ids": [answer["id"]]}).status_code, 409)
                mapped = self.crop(answer)
                self.assertEqual(self.client.get(mapped["crop_url"]).status_code, 200)
                self.assertEqual(mapped["region"]["page"], 0)
                self.assertIsNone(mapped["points"])
                self.assertEqual(self.client.put(f"/api/responses/{answer['id']}/region", json={
                    "revision": mapped["revision"], "page": 0, "x": 0.9, "y": 0.1, "width": 0.8, "height": 0.2}).status_code, 422)

    def test_ambiguous_ocr_needs_teacher_confirmation_before_grading(self):
        a = self.assessment(questions=objective_questions()[:1])
        self.scan(a["id"])
        answer = self.crop(self.responses(a["id"])[0])
        self.fake.transcription = {"text": "B", "status": "ambiguous", "flags": ["overwritten"]}
        self.job(a["id"], "transcribe", [answer["id"]])
        read = self.response(answer["id"])
        self.assertEqual(read["state"], "needs_review")
        self.assertFalse(read["transcription_confirmed"])
        self.assertEqual(self.job(a["id"], response_ids=[answer["id"]])["errors"], 1)
        read = self.response(answer["id"])
        confirmed = self.checked(self.client.patch(f"/api/responses/{answer['id']}", json={
            "revision": read["revision"], "transcription_confirmed": True}))
        self.assertEqual(confirmed["transcription_status"], "manual")
        self.job(a["id"], response_ids=[answer["id"]])
        scored = self.response(answer["id"])
        self.assertEqual(scored["points"], 1)
        self.assertIn("overwritten", scored["suggestion"]["review_reasons"])
        self.assertEqual(self.approve(answer["id"])["state"], "approved")

    def test_failed_transcription_never_becomes_blank_zero_or_approved(self):
        for mode in ["request_error", "extraction_failed"]:
            with self.subTest(mode=mode):
                a = self.assessment(questions=objective_questions()[:1])
                self.scan(a["id"])
                answer = self.crop(self.responses(a["id"])[0])
                self.fake.transcription_error = OllamaError("Synthetic request timeout.") if mode == "request_error" else None
                self.fake.transcription = {"text": "", "status": "failed", "flags": ["bad_crop"]}
                job = self.job(a["id"], "transcribe", [answer["id"]])
                self.assertEqual(job["errors"], 1)
                failed = self.response(answer["id"])
                self.assertIsNone(failed["transcription"])
                self.assertEqual(failed["transcription_status"], "failed")
                self.assertIsNone(failed["points"])
                self.assertIsNone(failed["suggestion"])
                self.assertEqual(self.client.post(f"/api/responses/{answer['id']}/approve", json={"revision": failed["revision"]}).status_code, 409)
                self.assertEqual(self.client.patch(f"/api/responses/{answer['id']}", json={"revision": failed["revision"], "points": 0}).status_code, 409)

    def test_ocr_blank_requires_explicit_confirmation(self):
        a = self.assessment(questions=objective_questions()[:1])
        self.scan(a["id"])
        answer = self.crop(self.responses(a["id"])[0])
        self.fake.transcription = {"text": "", "status": "blank", "flags": []}
        self.job(a["id"], "transcribe", [answer["id"]])
        self.assertEqual(self.job(a["id"], response_ids=[answer["id"]])["errors"], 1)
        read = self.response(answer["id"])
        self.assertIsNone(read["points"])
        self.checked(self.client.patch(f"/api/responses/{answer['id']}", json={
            "revision": read["revision"], "transcription_confirmed": True}))
        self.job(a["id"], response_ids=[answer["id"]])
        scored = self.response(answer["id"])
        self.assertEqual(scored["points"], 0)
        self.assertNotEqual(scored["state"], "approved")

    def test_failed_grading_never_becomes_zero_and_teacher_can_recover(self):
        a = self.assessment(questions=rubric_questions())
        self.typed(a["id"], {"1": "Yo como una manzana."})
        self.fake.grading_error = OllamaError("Synthetic model request failed.")
        self.assertEqual(self.job(a["id"])["errors"], 1)
        answer = self.responses(a["id"])[0]
        self.assertIsNone(answer["points"])
        self.assertIsNone(answer["suggestion"])
        self.assertTrue(answer["error"])
        self.assertEqual(self.client.post(f"/api/responses/{answer['id']}/approve", json={"revision": answer["revision"]}).status_code, 409)
        manual = self.checked(self.client.patch(f"/api/responses/{answer['id']}", json={
            "revision": answer["revision"], "points": 2, "feedback": "Teacher graded this answer directly."}))
        self.assertIsNone(manual["error"])
        self.assertEqual(self.approve(answer["id"])["state"], "approved")

    def test_rubric_batch_records_versions_and_preserves_draft_status(self):
        a = self.assessment(questions=rubric_questions())
        self.typed(a["id"], {"1": "Yo como una manzana."})
        job = self.job(a["id"])
        self.assertEqual(job["model_digest"], DIGEST)
        self.assertEqual(job["ollama_version"], self.fake.version)
        self.assertEqual(job["rubric_version"], a["version"])
        self.assertTrue(job["prompt_version"])
        self.assertEqual(set(job["prompt_hashes"]), {"transcription", "grading", "key_extraction"})
        answer = self.responses(a["id"])[0]
        self.assertEqual(answer["suggestion"]["method"], "model")
        self.assertEqual(len(answer["suggestion"]["criteria"]), 2)
        self.assertNotEqual(answer["state"], "approved")
        audit = self.checked(self.client.get(f"/api/assessments/{a['id']}/audit.json"))
        self.assertTrue(audit["assessment"]["synthetic"])
        self.assertEqual(audit["responses"][0]["question_type"], "rubric")

    def test_rubric_grading_receives_global_and_question_policy_without_changing_student_text(self):
        instructions = "SYNTHETIC policy: preserve accents in evidence; award partial credit only under the supplied criteria."
        question_notes = "For this question, accept an omitted subject pronoun."
        questions = rubric_questions()
        questions[0]["notes"] = question_notes
        a = self.checked(self.client.post("/api/assessments", json={
            "title": "SYNTHETIC instruction forwarding", "course": "Spanish 2 Honors",
            "instructions": instructions, "questions": questions, "synthetic": True}))
        self.checked(self.client.post(f"/api/assessments/{a['id']}/approve"))
        literal = "Yo  como una manzana.\nesta"
        self.typed(a["id"], {"1": literal})
        self.assertEqual(self.job(a["id"])["errors"], 0)
        self.assertEqual(len(self.fake.graded_questions), 1)
        supplied = self.fake.graded_questions[0]
        self.assertIn(instructions, supplied["notes"])
        self.assertIn(question_notes, supplied["notes"])
        self.assertEqual(supplied["rubric"], a["questions"][0]["rubric"])
        self.assertEqual(next(call[2] for call in self.fake.calls if call[0] == "grade"), literal)
        response = self.responses(a["id"])[0]
        self.assertEqual(response["original_text"], literal)
        self.assertEqual(response["transcription"], literal)
        stored = self.checked(self.client.get(f"/api/assessments/{a['id']}"))
        self.assertEqual(stored["questions"][0]["notes"], question_notes)
        self.assertEqual(stored["version"], a["version"])

    def test_stale_revision_cannot_overwrite_teacher_edit(self):
        a = self.assessment()
        self.typed(a["id"], {"1": "B", "2": "está"})
        answer = self.responses(a["id"])[0]
        self.checked(self.client.patch(f"/api/responses/{answer['id']}", json={"revision": answer["revision"], "points": 1}))
        stale = self.client.patch(f"/api/responses/{answer['id']}", json={"revision": answer["revision"], "points": 0})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.response(answer["id"])["points"], 1)

    def test_inflight_model_result_cannot_overwrite_new_teacher_transcription(self):
        a = self.assessment(questions=rubric_questions())
        self.typed(a["id"], {"1": "Yo como una manzana."})
        answer = self.responses(a["id"])[0]
        self.fake.block_grading = True
        job = self.job(a["id"], wait=False)
        self.assertTrue(self.fake.grading_started.wait(2))
        try:
            self.checked(self.client.patch(f"/api/responses/{answer['id']}", json={
                "revision": answer["revision"], "transcription": "Yo como dos manzanas.", "transcription_confirmed": True}))
        finally:
            self.fake.grading_release.set()
        finished = self.wait_job(job["id"])
        self.assertEqual(finished["errors"], 1)
        current = self.response(answer["id"])
        self.assertEqual(current["transcription"], "Yo como dos manzanas.")
        self.assertIsNone(current["points"])
        self.assertIsNone(current["suggestion"])
        self.assertIn("stale_result_discarded", [h["kind"] for h in current["history"]])
        stale = next(h for h in current["history"] if h["kind"] == "stale_result_discarded")
        self.assertEqual(stale["details"]["result"]["transcription"], "Yo como una manzana.")
        self.assertEqual(stale["details"]["result"]["suggestion"]["suggested_points"], 2)

    def test_model_digest_drift_blocks_later_batch_without_changing_old_draft(self):
        a = self.assessment(questions=rubric_questions())
        self.typed(a["id"], {"1": "Yo como una manzana."}, "SYNTHETIC-A")
        self.job(a["id"])
        old = self.responses(a["id"])[0]
        self.fake.digest = "b" * 64
        self.typed(a["id"], {"1": "Yo como una manzana."}, "SYNTHETIC-B")
        self.assertEqual(self.job(a["id"])["errors"], 1)
        answers = {r["student_id"]: r for r in self.responses(a["id"])}
        self.assertEqual(answers["SYNTHETIC-A"]["points"], old["points"])
        self.assertIsNone(answers["SYNTHETIC-B"]["points"])
        self.assertIn("digest changed", answers["SYNTHETIC-B"]["error"])

    def test_runtime_drift_is_checked_between_answers_in_one_batch(self):
        a = self.assessment(questions=rubric_questions())
        self.typed(a["id"], {"1": "Yo como una manzana."}, "SYNTHETIC-A")
        self.typed(a["id"], {"1": "Yo como una manzana."}, "SYNTHETIC-B")
        self.fake.version_after_grade = "0.14.0"
        job = self.job(a["id"])
        self.assertEqual(job["done"], 2)
        self.assertEqual(job["errors"], 1)
        self.assertEqual(len([call for call in self.fake.calls if call[0] == "grade"]), 1)
        answers = self.responses(a["id"])
        self.assertEqual(sum(r["points"] is not None for r in answers), 1)
        failed = next(r for r in answers if r["points"] is None)
        self.assertIn("runtime changed", failed["error"])
        self.assertNotEqual(failed["state"], "approved")

    def test_prompt_version_and_hash_changes_block_new_jobs(self):
        a = self.assessment(questions=objective_questions()[:1])
        self.typed(a["id"], {"1": "B"})
        for name in ["PROMPT_VERSION", "PROMPT_HASH"]:
            with self.subTest(name=name), patch("app.main." + name, "synthetic-new-version"):
                blocked = self.client.post(f"/api/assessments/{a['id']}/jobs", json={"action": "grade"})
                self.assertEqual(blocked.status_code, 409)
        self.assertIsNone(self.responses(a["id"])[0]["points"])

    def test_configured_context_is_default_and_api_rejects_unsupported_context(self):
        custom = Settings(Path(self.directory.name) / "custom-config", PASSWORD, "synthetic-secret-" * 4,
                          num_ctx=8192, model="local-synthetic:latest")
        with TestClient(create_app(custom, client=self.fake), headers={"X-Grader-Request": "1"}) as client:
            self.checked(client.post("/api/login", json={"password": PASSWORD}))
            settings = self.checked(client.get("/api/settings"))
            self.assertEqual(settings["num_ctx"], 8192)
            self.assertEqual(settings["model"], "local-synthetic:latest")
            payload = {"title": "SYNTHETIC context test", "questions": objective_questions()}
            a = self.checked(client.post("/api/assessments", json=payload))
            self.assertEqual(a["num_ctx"], 8192)
            self.assertEqual(a["model"], "local-synthetic:latest")
            for num_ctx in [1024, 16384, True, 4096.5]:
                with self.subTest(num_ctx=num_ctx):
                    invalid = client.post("/api/assessments", json=dict(payload, num_ctx=num_ctx))
                    self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_json_key_upload_retains_source_and_requires_approval(self):
        payload = {"title": "SYNTHETIC imported key", "course": "Spanish 2 CP", "questions": objective_questions(), "synthetic": True}
        original = json.dumps(payload, ensure_ascii=False).encode()
        extracted = self.checked(self.client.post("/api/key/extract", files={"file": ("synthetic-key.json", original, "application/json")}))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.checked(self.client.get("/api/assessments")), [])
        self.assertEqual(self.client.get(f"/api/key/sources/{extracted['source_id']}").content, original)
        assessment = self.checked(self.client.post("/api/assessments", json=extracted["draft"]))
        self.assertEqual(assessment["status"], "draft")
        self.assertEqual(assessment["key_source"], extracted["source_id"])
        self.assertTrue(extracted["warnings"])

    def test_invalid_scan_and_invalid_key_do_not_create_zero_scores(self):
        a = self.assessment()
        bad_scan = self.client.post(f"/api/assessments/{a['id']}/submissions/scan", data={"student_id": "SYNTHETIC-BROKEN"},
                                    files={"file": ("broken.pdf", b"synthetic invalid pdf", "application/pdf")})
        self.assertEqual(bad_scan.status_code, 422)
        self.assertEqual(self.responses(a["id"]), [])
        bad_key = self.client.post("/api/key/extract", files={"file": ("bad.json", b'{"title":"missing questions"}', "application/json")})
        self.assertEqual(bad_key.status_code, 422)
        self.assertEqual(len(self.checked(self.client.get("/api/assessments"))), 1)

    def test_csv_preserves_accents_missing_columns_and_rolls_back_bad_batch(self):
        a = self.assessment()
        csv_content = "student_id,q2\r\nSYNTHETIC-A,está\r\nSYNTHETIC-B,esta\r\n".encode()
        imported = self.checked(self.client.post(f"/api/assessments/{a['id']}/submissions/csv",
                               files={"file": ("synthetic.csv", csv_content, "text/csv")}))
        self.assertEqual(imported["imported"], 2)
        answers = self.responses(a["id"])
        self.assertTrue(all(r["transcription"] is None for r in answers if r["question_id"] == "1"))
        self.assertEqual([r["original_text"] for r in answers if r["question_id"] == "2"], ["está", "esta"])
        bad_content = b"student_id,q1,q2\nSYNTHETIC-C,B,esta\nSYNTHETIC-D,B\n"
        invalid = self.client.post(f"/api/assessments/{a['id']}/submissions/csv",
                                  files={"file": ("bad.csv", bad_content, "text/csv")})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(len(self.checked(self.client.get(f"/api/assessments/{a['id']}/submissions"))), 2)

    def test_storage_survives_restart_and_timer_is_audited(self):
        a = self.assessment(questions=objective_questions()[:1])
        self.typed(a["id"], {"1": "B"})
        self.job(a["id"])
        answer = self.responses(a["id"])[0]
        self.approve(answer["id"])
        self.checked(self.client.post(f"/api/assessments/{a['id']}/time",
                                     json={"phase": "scanning", "seconds": 75, "note": "Synthetic timing entry."}))
        self.client.__exit__(None, None, None)
        self.app = create_app(self.settings, client=self.fake)
        self.client = TestClient(self.app, headers={"X-Grader-Request": "1"})
        self.client.__enter__()
        self.checked(self.client.post("/api/login", json={"password": PASSWORD}))
        restored = self.response(answer["id"])
        self.assertEqual(restored["state"], "approved")
        self.assertEqual(restored["points"], 1)
        self.assertEqual(restored["original_text"], "B")
        self.assertTrue(restored["history"])
        timer = self.checked(self.client.get(f"/api/assessments/{a['id']}/time"))
        self.assertEqual(timer["totals"]["scanning"], 75)
        audit = self.checked(self.client.get(f"/api/assessments/{a['id']}/audit.json"))
        self.assertEqual(audit["time_entries"][0]["note"], "Synthetic timing entry.")


if __name__ == "__main__":
    unittest.main()
