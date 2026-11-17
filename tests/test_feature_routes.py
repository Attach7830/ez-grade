"""Synthetic API regression tests for generated keys and audited score scaling.

These tests use real routes, image/PDF processing, SQLite, and approval gates.
Inference is a test dependency. They do not measure OCR or grading accuracy.
"""

import copy
import csv
from hashlib import sha256
import io
import json
import sqlite3
import unittest
from unittest.mock import patch

from PIL import Image

import app.main as main_module
from app.db import dump, now, parse
from app.key_generation import KEY_GENERATION_PROMPT_HASH, KEY_GENERATION_PROMPT_VERSION
from app.ollama import OllamaError
import test_key_generation as key_examples
import test_workflow as workflow


class FeatureRouteTests(unittest.TestCase):
    # Reuse only setup/helpers. Importing or inheriting the WorkflowTests class
    # would cause its entire test suite to be collected a second time.
    setUp = workflow.WorkflowTests.setUp
    tearDown = workflow.WorkflowTests.tearDown
    checked = workflow.WorkflowTests.checked
    assessment = workflow.WorkflowTests.assessment
    typed = workflow.WorkflowTests.typed
    responses = workflow.WorkflowTests.responses
    response = workflow.WorkflowTests.response
    approve = workflow.WorkflowTests.approve

    def generate(self, output=None, *, pdf=False, **form):
        self.fake.structured_result = output if output is not None else key_examples.page(key_examples.exact(), key_examples.choice())
        original = workflow.synthetic_scan("PDF" if pdf else "PNG")
        options = {"course": "Spanish 2 Honors", "instructions": "SYNTHETIC teacher guidance.",
                   "default_points": "3", "num_ctx": "4096"}
        options.update(form)
        name, mime = ("synthetic-assessment.pdf", "application/pdf") if pdf else ("synthetic-assessment.png", "image/png")
        response = self.client.post("/api/key/generate", data=options, files={"file": (name, original, mime)})
        return response, original

    def scoring(self, aid):
        return self.checked(self.client.get(f"/api/assessments/{aid}/scoring"))

    def graded(self, scores=(8, 8), approve_answers=True):
        questions = workflow.objective_questions()
        for question in questions:
            question["max_points"] = 10
        assessment = self.assessment(questions=questions)
        self.typed(assessment["id"], {"1": "B", "2": "está"})
        for answer, points in zip(self.responses(assessment["id"]), scores):
            if points is not None:
                self.checked(self.client.patch(f"/api/responses/{answer['id']}", json={
                    "revision": answer["revision"], "points": points, "feedback": "Synthetic teacher score."}))
                if approve_answers:
                    self.approve(answer["id"])
        return assessment

    @staticmethod
    def section(target=10):
        return [{"id": "s1", "label": "SYNTHETIC vocabulary", "question_ids": ["1", "2"], "target_points": target}]

    def preview(self, aid, sections=None, note="SYNTHETIC teacher scaling decision."):
        body = {"revision": self.scoring(aid)["revision"],
                "sections": self.section() if sections is None else sections, "note": note}
        result = self.checked(self.client.post(f"/api/assessments/{aid}/scoring/preview", json=body))
        return body, result

    def apply(self, aid, body, preview):
        return self.checked(self.client.put(f"/api/assessments/{aid}/scoring", json={
            **body, "confirmed": True, "preview_hash": preview["preview_hash"]}))

    def csv_row(self, aid):
        response = self.client.get(f"/api/assessments/{aid}/export.csv")
        self.assertEqual(response.status_code, 200, response.text)
        return list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))[0]

    def test_generated_pdf_key_is_only_a_draft_and_source_is_preserved_and_protected(self):
        response, original = self.generate(pdf=True)
        result = self.checked(response)
        self.assertEqual(self.checked(self.client.get("/api/assessments")), [])
        self.assertEqual(result["draft"]["course"], "Spanish 2 Honors")
        self.assertEqual(result["draft"]["instructions"], "SYNTHETIC teacher guidance.")
        self.assertEqual(result["draft"]["questions"][1]["max_points"], 3)
        source_id = result["source_id"]
        self.assertEqual(result["draft"]["key_source"], source_id)
        details_url = f"/api/key/sources/{source_id}/details"
        details = self.checked(self.client.get(details_url))
        self.assertEqual(details["method"], "model_generation")
        self.assertEqual(details["status"], "draft")
        self.assertTrue(details["warnings"])
        self.assertEqual(details["question_reviews"][1]["points_source"], "teacher_default")
        self.assertEqual(self.client.get(details["original_url"]).content, original)
        self.assertEqual(len(details["pages"]), 1)
        image_response = self.client.get(details["pages"][0]["url"])
        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(details["provenance"]["input_image_sha256"], sha256(image_response.content).hexdigest())
        self.assertNotIn("source_sha256", details["provenance"])
        with self.app.state.store.read() as con:
            row = con.execute("SELECT metadata FROM key_sources WHERE id=?", (source_id,)).fetchone()
            self.assertEqual(parse(row["metadata"])["source_sha256"], sha256(original).hexdigest())
        saved = self.checked(self.client.post("/api/assessments", json=result["draft"]))
        self.assertEqual(saved["status"], "draft")
        self.assertIsNone(saved["approved_at"])
        self.assertEqual(saved["counts"]["total"], 0)
        self.checked(self.client.post("/api/logout"))
        for url in [details_url, details["original_url"], details["pages"][0]["url"]]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 401)

    def test_incomplete_generated_entry_cannot_be_saved_until_teacher_completes_it(self):
        output = key_examples.page(key_examples.exact(), key_examples.exact("2", answers=[], status="incomplete",
                                  prompt="Completa: [unreadable]", review=["SYNTHETIC unreadable question."]))
        response, _ = self.generate(output)
        result = self.checked(response)
        self.assertEqual(result["question_reviews"][1]["status"], "incomplete")
        self.assertEqual(result["draft"]["questions"][1]["accepted_answers"], [])
        self.assertEqual(result["draft"]["questions"][1]["notes"], "")
        self.assertEqual(self.client.post("/api/assessments", json=result["draft"]).status_code, 422)
        self.assertEqual(self.checked(self.client.get("/api/assessments")), [])
        corrected = copy.deepcopy(result["draft"])
        corrected["questions"][1].update(prompt="Completa: Ella ___ profesora.", accepted_answers=["es"])
        saved = self.checked(self.client.post("/api/assessments", json=corrected))
        self.assertEqual(saved["status"], "draft")
        self.assertEqual(self.client.post(f"/api/assessments/{saved['id']}/jobs", json={"action": "grade"}).status_code, 409)
        original_observations = self.checked(self.client.get(f"/api/key/sources/{result['source_id']}/details"))
        self.assertEqual(original_observations["question_reviews"][1]["status"], "incomplete")

    def test_generation_failure_preserves_failure_provenance_without_creating_assessment(self):
        with patch.object(self.fake, "chat_structured", side_effect=OllamaError("SYNTHETIC model failure.")):
            response, _ = self.generate()
        self.assertEqual(response.status_code, 502)
        self.assertIn("No assessment or approved key was created", response.text)
        self.assertEqual(self.checked(self.client.get("/api/assessments")), [])
        with self.app.state.store.read() as con:
            row = con.execute("SELECT * FROM key_sources").fetchone()
            self.assertEqual(parse(row["metadata"])["status"], "failed")
            source_id = row["id"]
        details = self.checked(self.client.get(f"/api/key/sources/{source_id}/details"))
        self.assertEqual(details["provenance"]["prompt_hash"], KEY_GENERATION_PROMPT_HASH)
        self.assertEqual(details["provenance"]["prompt_version"], KEY_GENERATION_PROMPT_VERSION)

    def test_multipage_or_wholly_unresolved_generation_does_not_create_assessment(self):
        images = [Image.new("RGB", (160, 100), color) for color in ["white", "lightgray"]]
        content = io.BytesIO()
        images[0].save(content, format="PDF", save_all=True, append_images=images[1:])
        for image in images:
            image.close()
        response = self.client.post("/api/key/generate", data={"course": "Spanish 1 Honors"},
                                    files={"file": ("synthetic-two-pages.pdf", content.getvalue(), "application/pdf")})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.fake.calls, [])
        response, _ = self.generate(key_examples.page(key_examples.exact(status="incomplete", answers=[])))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.checked(self.client.get("/api/assessments")), [])

    def test_generation_api_rejects_invalid_default_points_before_inference(self):
        for value in ["0", "-1", "nan", "inf", "1001", "true"]:
            with self.subTest(value=value):
                response, _ = self.generate(default_points=value)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.fake.calls, [])

    def test_scaling_requires_approved_key_and_explicit_preview_confirmation(self):
        draft = self.assessment(approve=False)
        body = {"revision": 0, "sections": self.section()}
        self.assertEqual(self.client.post(f"/api/assessments/{draft['id']}/scoring/preview", json=body).status_code, 409)
        assessment = self.graded()
        aid = assessment["id"]
        body, preview = self.preview(aid)
        self.assertEqual(preview["students"][0]["raw_total"], 16)
        self.assertEqual(preview["students"][0]["total"], 8)
        self.assertEqual(preview["students"][0]["max_points"], 10)
        self.assertEqual(self.scoring(aid)["revision"], 0)
        for extra in [{}, {"confirmed": False}, {"confirmed": "true"}, {"confirmed": True}]:
            with self.subTest(extra=extra):
                payload = {**body, **extra}
                if extra != {"confirmed": True}:
                    payload["preview_hash"] = preview["preview_hash"]
                self.assertEqual(self.client.put(f"/api/assessments/{aid}/scoring", json=payload).status_code, 422)
        self.assertEqual(self.scoring(aid)["revision"], 0)

    def test_scaling_16_of_20_to_8_of_10_preserves_raw_scores_history_and_prompt(self):
        a = self.graded()
        aid = a["id"]
        before = self.responses(aid)
        body, preview = self.preview(aid)
        state = self.apply(aid, body, preview)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["policy"]["raw_max_points"], 20)
        self.assertEqual(state["policy"]["max_points"], 10)
        self.assertEqual(self.responses(aid), before)
        after = self.checked(self.client.get(f"/api/assessments/{aid}"))
        for field in ["version", "prompt_version", "prompt_hash", "questions", "model", "num_ctx"]:
            self.assertEqual(after[field], a[field])
        self.assertEqual(after["raw_max_points"], 20)
        self.assertEqual(after["adjusted_max_points"], 10)
        row = self.csv_row(aid)
        self.assertEqual((float(row["q1"]), float(row["q2"])), (8, 8))
        self.assertEqual((float(row["adjusted_q1"]), float(row["adjusted_q2"])), (4, 4))
        self.assertEqual((float(row["raw_total"]), float(row["raw_max_points"])), (16, 20))
        self.assertEqual((float(row["total"]), float(row["max_points"])), (8, 10))
        self.assertEqual(row["percentage"], "80.00")
        self.assertEqual(row["scoring_revision"], "1")
        audit = self.checked(self.client.get(f"/api/assessments/{aid}/audit.json"))
        self.assertEqual(audit["scoring"]["student_totals"][0]["total"], 8)
        self.assertEqual(audit["scoring"]["student_totals"][0]["raw_total"], 16)
        history = audit["scoring"]["history"]
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["details"]["teacher_confirmed"])
        self.assertEqual(history[0]["details"]["students_at_confirmation"][0]["total"], 8)
        self.assertEqual(audit["responses"][0]["points"], 8)

    def test_preview_is_invalidated_by_edit_approval_or_new_submission(self):
        for change in ["score", "approval", "submission"]:
            with self.subTest(change=change):
                a = self.graded(approve_answers=False)
                aid = a["id"]
                body, preview = self.preview(aid)
                first = self.responses(aid)[0]
                if change == "score":
                    self.checked(self.client.patch(f"/api/responses/{first['id']}", json={"revision": first["revision"], "points": 7}))
                elif change == "approval":
                    self.approve(first["id"])
                else:
                    self.typed(aid, {"1": "B", "2": "está"}, "SYNTHETIC-NEW")
                result = self.client.put(f"/api/assessments/{aid}/scoring", json={
                    **body, "confirmed": True, "preview_hash": preview["preview_hash"]})
                self.assertEqual(result.status_code, 409)
                self.assertEqual(self.scoring(aid)["revision"], 0)

    def test_preview_hash_rejects_changed_policy_note_and_stale_revision(self):
        a = self.graded()
        aid = a["id"]
        body, preview = self.preview(aid)
        for changes in [{"sections": self.section(12)}, {"note": "Changed after preview."},
                        {"preview_hash": "0" * 64}]:
            with self.subTest(changes=changes):
                result = self.client.put(f"/api/assessments/{aid}/scoring", json={
                    **body, "confirmed": True, "preview_hash": preview["preview_hash"], **changes})
                self.assertEqual(result.status_code, 409)
        self.apply(aid, body, preview)
        self.assertEqual(self.client.put(f"/api/assessments/{aid}/scoring", json={
            **body, "confirmed": True, "preview_hash": preview["preview_hash"]}).status_code, 409)
        self.assertEqual(self.scoring(aid)["revision"], 1)

    def test_invalid_sections_cannot_change_policy(self):
        a = self.graded()
        aid = a["id"]
        cases = [
            [{"id": "one", "label": "One", "question_ids": ["404"], "target_points": 5}],
            [{"id": "one", "label": "One", "question_ids": ["1"], "target_points": 5},
             {"id": "two", "label": "Two", "question_ids": ["1", "2"], "target_points": 5}],
            self.section(0), self.section(-1), self.section(True),
        ]
        for sections in cases:
            with self.subTest(sections=sections):
                response = self.client.post(f"/api/assessments/{aid}/scoring/preview", json={"revision": 0, "sections": sections})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.scoring(aid)["revision"], 0)

    def test_empty_sections_restore_original_totals_with_a_second_audit_entry(self):
        a = self.graded()
        aid = a["id"]
        before = self.responses(aid)
        body, preview = self.preview(aid)
        self.apply(aid, body, preview)
        body, restored_preview = self.preview(aid, sections=[])
        self.assertEqual(restored_preview["students"][0]["previous_total"], 8)
        self.assertEqual(restored_preview["students"][0]["total"], 16)
        state = self.apply(aid, body, restored_preview)
        self.assertEqual(state["revision"], 2)
        self.assertEqual(state["sections"], [])
        self.assertEqual(state["policy"]["max_points"], 20)
        self.assertEqual(len(state["history"]), 2)
        self.assertEqual(self.responses(aid), before)
        self.assertEqual(float(self.csv_row(aid)["total"]), 16)

    def test_unresolved_score_stays_missing_in_preview_audit_and_export_gate(self):
        a = self.graded(scores=(8, None))
        aid = a["id"]
        body, preview = self.preview(aid)
        student = preview["students"][0]
        self.assertFalse(student["complete"])
        self.assertFalse(student["all_approved"])
        self.assertIsNone(student["total"])
        self.assertIsNone(student["raw_total"])
        self.apply(aid, body, preview)
        self.assertIsNone(self.responses(aid)[1]["points"])
        self.assertEqual(self.client.get(f"/api/assessments/{aid}/export.csv").status_code, 409)
        audit = self.checked(self.client.get(f"/api/assessments/{aid}/audit.json"))
        total = audit["scoring"]["student_totals"][0]
        self.assertFalse(total["complete"])
        self.assertIsNone(total["total"])
        self.assertEqual(total["missing_question_ids"], ["2"])

    def test_scoring_csv_and_audit_each_use_one_read_snapshot(self):
        for route in ["scoring", "export.csv", "audit.json"]:
            with self.subTest(route=route):
                a = self.graded()
                aid = a["id"]
                original = main_module.scoring_state
                changed = False

                def change_policy_after_assessment_read(con, assessment, include_history=False):
                    nonlocal changed
                    if not changed:
                        changed = True
                        # A separate committed writer stands in for another
                        # request that changes policy during a long export.
                        with self.app.state.store.transaction() as writer:
                            timestamp = now()
                            writer.execute("INSERT INTO scoring_policies VALUES(?,?,?,?,?)",
                                           (aid, 1, dump(self.section()), "SYNTHETIC concurrent policy", timestamp))
                            writer.execute("INSERT INTO scoring_history(assessment_id,revision,details,created_at) VALUES(?,?,?,?)",
                                           (aid, 1, dump({"teacher_confirmed": True}), timestamp))
                    return original(con, assessment, include_history)

                with patch("app.main.scoring_state", side_effect=change_policy_after_assessment_read):
                    response = self.client.get(f"/api/assessments/{aid}/{route}")
                self.assertEqual(response.status_code, 200, response.text)
                if route == "scoring":
                    self.assertEqual(response.json()["revision"], 0)
                    self.assertEqual(response.json()["history"], [])
                elif route == "export.csv":
                    row = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))[0]
                    self.assertEqual((float(row["total"]), float(row["max_points"])), (16, 20))
                    self.assertEqual(row["scoring_revision"], "0")
                else:
                    self.assertEqual(response.json()["scoring"]["revision"], 0)
                    self.assertEqual(response.json()["scoring"]["student_totals"][0]["total"], 16)
                self.assertEqual(self.scoring(aid)["revision"], 1)
                self.assertEqual(self.scoring(aid)["policy"]["max_points"], 10)

    def test_v1_migration_retains_grades_and_new_policy_persists_after_restart(self):
        a = self.graded()
        aid = a["id"]
        before = self.responses(aid)
        self.client.__exit__(None, None, None)
        # Reproduce the actual v1 shape: existing data and no v2 policy tables.
        with sqlite3.connect(self.settings.data_dir / "grader.sqlite3") as con:
            con.execute("DROP TABLE scoring_history")
            con.execute("DROP TABLE scoring_policies")
            con.execute("PRAGMA user_version=1")
        self.app = workflow.create_app(self.settings, client=self.fake)
        self.client = workflow.TestClient(self.app, headers={"X-Grader-Request": "1"})
        self.client.__enter__()
        self.checked(self.client.post("/api/login", json={"password": workflow.PASSWORD}))
        self.assertEqual(self.responses(aid), before)
        restored = self.checked(self.client.get(f"/api/assessments/{aid}"))
        self.assertEqual(restored["version"], a["version"])
        self.assertEqual(restored["prompt_hash"], a["prompt_hash"])
        self.assertEqual(self.scoring(aid)["sections"], [])
        with sqlite3.connect(self.settings.data_dir / "grader.sqlite3") as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 2)
        body, preview = self.preview(aid)
        self.apply(aid, body, preview)
        self.client.__exit__(None, None, None)
        self.app = workflow.create_app(self.settings, client=self.fake)
        self.client = workflow.TestClient(self.app, headers={"X-Grader-Request": "1"})
        self.client.__enter__()
        self.checked(self.client.post("/api/login", json={"password": workflow.PASSWORD}))
        self.assertEqual(self.responses(aid), before)
        state = self.scoring(aid)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["policy"]["max_points"], 10)
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(float(self.csv_row(aid)["total"]), 8)


if __name__ == "__main__":
    unittest.main()
