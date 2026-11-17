"""Real-browser smoke test using synthetic content and the actual local backend.

Run explicitly after installing requirements-dev.txt and Playwright Chromium:
    python tests/browser_smoke.py

No Ollama service, model, cloud API, or real student data is used. Key generation
uses an explicitly injected FakeEngine with a controlled synthetic response;
uploads, validation, storage, approvals, scoring, and exports use the real app.
This verifies the generation UI and its review boundary, not OCR/model quality.
The script is intentionally outside unittest discovery. CI may upload
tests/artifacts only on failure; all diagnostic content is synthetic.
"""

from __future__ import annotations

import csv
import io
import json
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import uvicorn
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright

from app.config import Settings
from app.main import create_app
from test_key_generation import FakeEngine, exact as synthetic_exact, page as synthetic_page


PASSWORD = "synthetic-ui-password"
TYPED_STUDENT = "SYNTHETIC typed 001"
SCAN_STUDENT = "SYNTHETIC scan 002"
ARTIFACTS = ROOT / "tests" / "artifacts"
GENERATED_TITLE = "SYNTHETIC generated key · controlled model response"


def synthetic_key() -> dict:
    return {
        "title": "SYNTHETIC browser smoke · accent policy",
        "course": "Spanish 1 Honors",
        "instructions": "Invented responses for software verification only.",
        "synthetic": True,
        "questions": [
            {
                "id": "1",
                "type": "choice",
                "prompt": "Selecciona la palabra para ‘book’: A. mesa B. libro.",
                "max_points": 1,
                "accepted_answers": ["B"],
            },
            {
                "id": "2",
                "type": "exact",
                "prompt": "Ella ____ en casa. (estar)",
                "max_points": 1,
                "accepted_answers": ["está"],
                "partial_answers": [
                    {"answer": "esta", "points": 0.5, "feedback": "Add the written accent."}
                ],
                "normalization": {"accent_policy": "strict"},
            },
        ],
    }


def make_scan(path: Path) -> None:
    """Printed synthetic fixture; this is not a handwriting/OCR accuracy test."""
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    draw.text((40, 30), "SYNTHETIC TEST PAPER — invented responses", fill="black", font=font)
    draw.text((100, 165), "1. B", fill="black", font=font)
    draw.text((100, 365), "2. esta", fill="black", font=font)
    image.save(path)


def approval_state(page, expected: str) -> None:
    expect(page.locator(".response-heading .badge")).to_have_text(expected)


def save_crop_and_manual_result(page, question: str, text: str, points: str, y: float) -> None:
    page.locator("#question-filter").select_option(question)
    expect(page.locator(".response-heading h2")).to_contain_text(f"/ Q{question}")
    page.locator(".student-row").filter(has_text=SCAN_STUDENT).click()
    expect(page.locator(".response-heading h2")).to_contain_text(SCAN_STUDENT)
    expect(page.locator("#crop-page-image")).to_be_visible()
    page.wait_for_function(
        "() => { const image=document.querySelector('#crop-page-image'); "
        "return image && image.complete && image.naturalWidth > 0; }"
    )
    stage = page.locator("#crop-stage")
    stage.scroll_into_view_if_needed()
    box = stage.bounding_box()
    assert box and box["width"] > 0 and box["height"] > 0, "Rendered crop canvas is missing"
    page.mouse.move(box["x"] + box["width"] * 0.06, box["y"] + box["height"] * y)
    page.mouse.down()
    page.mouse.move(
        box["x"] + box["width"] * 0.91,
        box["y"] + box["height"] * (y + 0.24),
        steps=8,
    )
    page.mouse.up()
    expect(page.locator("#crop-rect")).to_be_visible()
    page.locator("#save-region").click()
    expect(page.locator(".answer-crop")).to_be_visible()
    page.wait_for_function(
        "() => { const image=document.querySelector('.answer-crop'); "
        "return image && image.complete && image.naturalWidth > 0; }"
    )
    expect(page.locator("#score")).to_have_value("")
    page.locator("#transcription").fill(text)
    page.locator("#transcription-confirmed").check()
    page.locator("#save-transcription").click()
    expect(page.locator("#unsaved-indicator")).to_have_text("")
    expect(page.locator("#transcription")).to_have_value(text)
    expect(page.locator("#transcription-confirmed")).to_be_checked()
    page.locator("#score").fill(points)
    page.locator("#feedback").fill("SYNTHETIC manual review of the original region.")
    page.locator("#approve-response").click()
    approval_state(page, "Approved")


def download_rows(page, directory: Path, filename: str) -> list[dict]:
    expect(page.locator("#export-csv")).to_be_enabled()
    with page.expect_download() as download_info:
        page.locator("#export-csv").click()
    destination = directory / filename
    download_info.value.save_as(destination)
    return list(csv.DictReader(io.StringIO(destination.read_text(encoding="utf-8-sig"))))


def exercise_scoring_adjustment(page, directory: Path) -> None:
    """Exercise real preview/apply endpoints; original answer scores stay intact."""
    page.locator("#adjust-scoring-header").click()
    expect(page.locator("#scoring-editor")).to_be_visible()
    page.locator("#add-scoring-section").click()
    page.locator("[data-section-label]").fill("SYNTHETIC proportional section")
    page.locator('[data-section-question="1"]').check()
    page.locator('[data-section-question="2"]').check()
    page.locator("[data-section-target]").fill("1")
    page.locator("#scoring-note").fill("SYNTHETIC test: scale a 2-point section to 1 point.")
    expect(page.locator("#apply-scoring")).to_be_disabled()
    page.locator("#preview-scoring").click()
    expect(page.locator("#scoring-preview")).to_contain_text("2 raw → 1 adjusted points")
    typed_row = page.locator(".scoring-table tbody tr").filter(has_text=TYPED_STUDENT)
    scan_row = page.locator(".scoring-table tbody tr").filter(has_text=SCAN_STUDENT)
    expect(typed_row.locator("td").nth(2)).to_have_text("1 / 1")
    expect(scan_row.locator("td").nth(2)).to_have_text("0.75 / 1")
    expect(page.locator("#apply-scoring")).to_be_disabled()
    page.locator("#scoring-confirm").check()
    expect(page.locator("#apply-scoring")).to_be_enabled()

    # Editing a reviewed preview must invalidate its confirmation and token.
    page.locator("[data-section-target]").fill("0.5")
    expect(page.locator("#scoring-confirm")).not_to_be_checked()
    expect(page.locator("#apply-scoring")).to_be_disabled()
    page.locator("[data-section-target]").fill("1")
    page.locator("#preview-scoring").click()
    expect(page.locator("#scoring-confirm")).to_be_enabled()
    page.locator("#scoring-confirm").check()
    page.locator("#apply-scoring").click()
    expect(page.locator("#scoring-dialog")).not_to_be_visible()
    expect(page.locator("#assessment-header")).to_contain_text("1 adjusted points · 2 original points")
    expect(page.locator("#assessment-header")).to_contain_text("4 / 4 approved")

    rows = download_rows(page, directory, "adjusted-final.csv")
    by_student = {row["student_id"]: row for row in rows}
    assert float(by_student[TYPED_STUDENT]["total"]) == 1.0
    assert float(by_student[SCAN_STUDENT]["total"]) == 0.75
    assert float(by_student[TYPED_STUDENT]["raw_total"]) == 2.0
    assert float(by_student[SCAN_STUDENT]["raw_total"]) == 1.5
    assert float(by_student[SCAN_STUDENT]["q2"]) == 0.5
    assert float(by_student[SCAN_STUDENT]["adjusted_q2"]) == 0.25
    assert all(float(row["max_points"]) == 1.0 for row in rows)
    assert all(float(row["raw_max_points"]) == 2.0 for row in rows)

    with page.expect_download() as audit_info:
        page.locator("#export-audit").click()
    audit_path = directory / "synthetic-adjustment-audit.json"
    audit_info.value.save_as(audit_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    originals = {(row["student_id"], row["question_id"]): row for row in audit["responses"]}
    assert originals[(SCAN_STUDENT, "2")]["points"] == 0.5
    assert originals[(TYPED_STUDENT, "2")]["points"] == 1.0
    assert all(row["state"] == "approved" for row in originals.values())
    assert originals[(TYPED_STUDENT, "2")]["original_text"] == "esta"

    # Restore the original maxima through the same review boundary.
    page.locator("#adjust-scoring-header").click()
    expect(page.locator("[data-scoring-section]")).to_have_count(1)
    page.locator("#clear-scoring-sections").click()
    expect(page.locator("[data-scoring-section]")).to_have_count(0)
    expect(page.locator("#apply-scoring")).to_be_disabled()
    page.locator("#preview-scoring").click()
    expect(page.locator("#scoring-preview")).to_contain_text("restores the original point values")
    page.locator("#scoring-confirm").check()
    page.locator("#apply-scoring").click()
    expect(page.locator("#scoring-dialog")).not_to_be_visible()
    expect(page.locator("#assessment-header")).to_contain_text("4 / 4 approved")
    restored = {row["student_id"]: row for row in download_rows(page, directory, "restored-final.csv")}
    assert float(restored[TYPED_STUDENT]["total"]) == 2.0
    assert float(restored[SCAN_STUDENT]["total"]) == 1.5
    assert all(float(row["max_points"]) == 2.0 for row in restored.values())


def exercise_controlled_generation(page, directory: Path, engine: FakeEngine) -> None:
    """Actual generation UI/API with explicitly controlled model output only."""
    source = directory / "SYNTHETIC-unkeyed-assessment.png"
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)
    draw.text((35, 30), "SYNTHETIC UNKEYED ASSESSMENT — no student data", fill="black", font=font)
    draw.text((35, 150), "1. Completa: El libro ___ aqui. (estar)", fill="black", font=font)
    draw.text((35, 290), "2. Completa: [SYNTHETIC unreadable verb]", fill="black", font=font)
    image.save(source)
    image.close()

    page.locator("#new-assessment").click()
    page.locator('#assessment-form select[name="course"]').select_option("Spanish 2 CP")
    instructions = "SYNTHETIC teacher policy: preserve Spanish accents."
    page.locator('#assessment-form textarea[name="instructions"]').fill(instructions)
    page.get_by_text("Generate a draft key from an assessment", exact=True).click()
    page.locator("#assessment-key-upload").set_input_files(source)
    page.locator("#generation-default-points").fill("3")
    page.locator("#generate-key").click()
    expect(page.locator("#extraction-notice")).to_contain_text("Model-generated answer key")
    expect(page.locator("#editor-key-source")).to_contain_text("Default points assumed")
    expect(page.locator("#editor-key-source")).to_contain_text("Answer unresolved")
    expect(page.locator("#question-editor .editor-question")).to_have_count(2)
    first = page.locator('[data-q-index="0"]')
    second = page.locator('[data-q-index="1"]')
    expect(first.locator('[data-field="accepted_answers"]')).to_have_value("está")
    expect(first.locator('[data-field="max_points"]')).to_have_value("3")
    expect(second.locator('[data-field="accepted_answers"]')).to_have_value("")
    expect(second).to_contain_text("Complete the missing answer or rubric before saving")
    expect(page.locator('#assessment-form textarea[name="instructions"]')).to_have_value(instructions)
    expect(page.locator('#assessment-form select[name="course"]')).to_have_value("Spanish 2 CP")
    expect(page.locator("#editor-key-source .key-source-pages img")).to_be_visible()
    page.wait_for_function(
        "() => { const image=document.querySelector('#editor-key-source .key-source-pages img'); "
        "return image && image.complete && image.naturalWidth > 0; }"
    )

    # Incomplete generated items cannot be saved until the teacher completes them.
    page.locator("#assessment-form button[type=submit]").click()
    expect(page.locator("#editor-dialog")).to_be_visible()
    expect(page.locator("#editor-error")).to_contain_text("Complete the missing answers or rubric for Q2")
    second.locator('[data-field="accepted_answers"]').fill("está")
    page.locator("#assessment-form button[type=submit]").click()
    expect(page.locator("#editor-dialog")).not_to_be_visible()
    expect(page.locator("#assessment-header h1")).to_have_text(GENERATED_TITLE)
    expect(page.locator("#key-gate")).to_be_visible()
    expect(page.locator("#batch-grade")).to_be_disabled()
    expect(page.locator("#key-source-details")).to_contain_text("Generated key · source and required review")
    expect(page.locator("#key-source-details .key-source-pages img")).to_be_visible()

    # Original source, generation warnings, and draft approval status persist.
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#assessment-header h1")).to_have_text(GENERATED_TITLE)
    expect(page.locator("#key-gate")).to_be_visible()
    expect(page.locator("#batch-grade")).to_be_disabled()
    page.locator('[data-tab="key"]').click()
    expect(page.locator("#key-source-details")).to_contain_text("Default points assumed")
    expect(page.locator("#key-source-details .key-source-pages img")).to_be_visible()
    page.locator("#approve-key").click()
    expect(page.locator("#confirm-key-approval")).to_be_disabled()
    page.locator("#key-checked").check()
    page.locator("#confirm-key-approval").click()
    expect(page.locator("#approval-dialog")).not_to_be_visible()
    expect(page.locator("#key-gate")).not_to_be_visible()
    chats = [payload for kind, payload in engine.calls if kind == "chat"]
    assert len(chats) == 1, "Only the controlled key-generation request should use the fake engine"
    assert len(chats[0]["images"]) == 1 and chats[0]["num_ctx"] == 4096
    assert json.loads(chats[0]["user"])["teacher_instructions"] == instructions


def exercise(page, base_url: str, directory: Path, engine: FakeEngine) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    expect(page.locator("#login-view")).to_be_visible()
    page.locator("#password").fill(PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    expect(page.locator("#workspace")).to_be_visible()

    # Create through the editable JSON import, then explicitly approve the key.
    page.locator("#new-assessment").click()
    # The assessment-generation form is distinct from existing-key extraction.
    # This checks the UI only; no model output or generation accuracy is claimed.
    page.get_by_text("Generate a draft key from an assessment", exact=True).click()
    expect(page.locator("#assessment-key-upload")).to_be_visible()
    expect(page.locator("#generation-default-points")).to_have_value("1")
    expect(page.locator("#generate-key-panel")).to_contain_text("up to 10 short questions")
    page.get_by_text("Generate a draft key from an assessment", exact=True).click()
    page.get_by_text("Import an answer key", exact=True).click()
    page.locator("#key-json").fill(json.dumps(synthetic_key(), ensure_ascii=False))
    page.locator("#apply-json").click()
    page.locator("#assessment-form button[type=submit]").click()
    expect(page.locator("#editor-dialog")).not_to_be_visible()
    expect(page.locator("#assessment-header h1")).to_have_text(synthetic_key()["title"])
    expect(page.locator("#batch-grade")).to_be_disabled()
    expect(page.locator("#export-csv")).to_be_disabled()
    page.locator("#approve-key").click()
    expect(page.locator("#confirm-key-approval")).to_be_disabled()
    page.locator("#key-checked").check()
    page.locator("#confirm-key-approval").click()
    expect(page.locator("#approval-dialog")).not_to_be_visible()
    expect(page.locator("#key-gate")).not_to_be_visible()

    # Import literal typed text, including a deliberately missing accent.
    page.locator('[data-tab="intake"]').click()
    page.locator('#typed-form input[name="student_id"]').fill(TYPED_STUDENT)
    page.locator('[data-answer="1"]').fill("B")
    page.locator('[data-answer="2"]').fill("esta")
    page.locator("#typed-form button[type=submit]").click()
    expect(page.locator(".response-heading h2")).to_contain_text(TYPED_STUDENT)
    page.locator("#batch-grade").click()
    expect(page.locator("#job-status")).to_contain_text("completed", timeout=20000)
    expect(page.locator("#score")).to_have_value("1")
    page.locator("#question-filter").select_option("2")
    expect(page.locator(".response-heading h2")).to_contain_text("/ Q2")
    expect(page.locator("#score")).to_have_value("0.5")
    expect(page.locator(".original-text")).to_have_text("esta")
    expect(page.locator("#transcription")).to_have_value("esta")
    expect(page.locator(".suggestion-card")).to_contain_text("0.5")

    # Teacher edits are preserved; changing transcription invalidates that score.
    page.locator("#score").fill("0.75")
    page.locator("#feedback").fill("SYNTHETIC teacher adjustment before checking transcription.")
    page.locator("#save-score").click()
    expect(page.locator("#unsaved-indicator")).to_have_text("")
    expect(page.locator("#score")).to_have_value("0.75")
    page.locator("#transcription").fill("está")
    page.locator("#transcription").press("Control+Enter")
    expect(page.locator("#score")).to_have_value("")
    expect(page.locator("#transcription")).to_have_value("está")
    expect(page.locator(".original-text")).to_have_text("esta")
    approval_state(page, "Pending")
    expect(page.locator("#export-csv")).to_be_disabled()
    expect(page.locator(".suggestion-card")).to_have_count(0)
    page.locator("#grade-current").click()
    expect(page.locator("#score")).to_have_value("1", timeout=20000)
    page.locator("#approve-response").click()
    approval_state(page, "Approved")
    page.locator("#question-filter").select_option("1")
    expect(page.locator(".response-heading h2")).to_contain_text("/ Q1")
    expect(page.locator("#score")).to_have_value("1")
    page.locator("#score").press("Control+Enter")
    approval_state(page, "Approved")
    rows = download_rows(page, directory, "typed-final.csv")
    assert len(rows) == 1 and rows[0]["student_id"] == TYPED_STUDENT
    assert float(rows[0]["total"]) == 2.0

    # Upload a synthetic image, map each question with real pointer gestures,
    # and resolve it manually. No handwriting model or fake AI is involved.
    scan = directory / "synthetic-browser-scan.png"
    make_scan(scan)
    page.locator('[data-tab="intake"]').click()
    page.locator('#scan-form input[name="student_id"]').fill(SCAN_STUDENT)
    page.locator('#scan-form input[type="file"]').set_input_files(scan)
    page.locator("#scan-form button[type=submit]").click()
    expect(page.locator(".response-heading h2")).to_contain_text(SCAN_STUDENT)
    expect(page.locator("#export-csv")).to_be_disabled()
    save_crop_and_manual_result(page, "1", "B", "1", 0.16)
    save_crop_and_manual_result(page, "2", "esta", "0.5", 0.51)
    rows = download_rows(page, directory, "all-final.csv")
    assert len(rows) == 2
    by_student = {row["student_id"]: row for row in rows}
    assert float(by_student[TYPED_STUDENT]["total"]) == 2.0
    assert float(by_student[SCAN_STUDENT]["total"]) == 1.5
    assert all(float(row["max_points"]) == 2.0 for row in rows)

    exercise_scoring_adjustment(page, directory)

    # Reload verifies that final approvals survive browser state loss.
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#export-csv")).to_be_enabled()
    expect(page.locator("#assessment-header")).to_contain_text("4 / 4 approved")

    exercise_controlled_generation(page, directory, engine)


def main() -> None:
    if hasattr(signal, "SIGALRM"):
        def deadline(_signum, _frame):
            raise TimeoutError("Browser smoke exceeded its 160-second deadline")
        signal.signal(signal.SIGALRM, deadline)
        signal.alarm(160)

    failures: dict[str, list] = {"javascript": [], "console": [], "http": [], "network": [], "external": []}
    page = None
    with tempfile.TemporaryDirectory(prefix="cuaderno-browser-smoke-") as folder:
        directory = Path(folder)
        settings = Settings(
            data_dir=directory / "data",
            password=PASSWORD,
            session_secret="synthetic-browser-smoke-session-secret-0123456789",
        )
        # The only model-shaped output in this script is explicitly controlled.
        # All earlier grading uses deterministic rules or teacher-entered scores.
        engine = FakeEngine(synthetic_page(
            synthetic_exact(prompt="Completa: El libro ___ aquí. (estar)", points=None),
            synthetic_exact("2", status="incomplete", points=None, answers=[],
                            prompt="Completa: [SYNTHETIC unreadable verb]",
                            review=["SYNTHETIC controlled response: this verb could not be read."]),
            title=GENERATED_TITLE,
        ))
        app = create_app(settings, client=engine)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            started = time.monotonic()
            while not server.started:
                if not thread.is_alive() or time.monotonic() - started > 10:
                    raise RuntimeError("Synthetic local test server did not start")
                time.sleep(0.05)
            with urllib.request.urlopen(base_url + "/api/health", timeout=3) as response:
                assert response.status == 200

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(10000)
                page.set_default_navigation_timeout(15000)
                expect.set_options(timeout=12000)
                # The application does not ship a favicon. Avoid unrelated browser noise.
                page.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
                page.on("pageerror", lambda error: failures["javascript"].append(str(error)))
                page.on("console", lambda message: failures["console"].append(message.text) if message.type == "error" else None)
                page.on("response", lambda response: failures["http"].append({"url": response.url, "status": response.status}) if response.status >= 400 else None)
                page.on("requestfailed", lambda request: failures["network"].append({"url": request.url, "error": request.failure}))
                page.on("request", lambda request: failures["external"].append(request.url) if urlsplit(request.url).hostname not in (None, "127.0.0.1") else None)
                try:
                    exercise(page, base_url, directory, engine)
                    assert not any(failures.values()), json.dumps(failures, ensure_ascii=False, indent=2)
                except BaseException:
                    ARTIFACTS.mkdir(parents=True, exist_ok=True)
                    with suppress(Exception):
                        page.screenshot(path=str(ARTIFACTS / "browser-failure.png"), full_page=True)
                    with suppress(Exception):
                        (ARTIFACTS / "browser-failure.html").write_text(page.content(), encoding="utf-8")
                    (ARTIFACTS / "browser-errors.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
                    raise
                finally:
                    context.close()
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)

    print("PASS: synthetic browser workflow, key approval, strict-accent partial credit, transcription invalidation,")
    print("teacher score edits, final approval/export, image upload/cropping, section scaling/restoration,")
    print("raw-score audit preservation, persistence, local assets, and generated-key review/source persistence.")
    print("Key generation used an injected FakeEngine with controlled output; all other workflow behavior used the real backend.")
    print("No OCR, real model inference, or classroom grading accuracy was evaluated.")


if __name__ == "__main__":
    main()
