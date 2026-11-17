"""Real-browser smoke test using synthetic content and the actual local backend.

Run explicitly after installing requirements-dev.txt and Playwright Chromium:
    python tests/browser_smoke.py

No Ollama service, model, cloud API, or real student data is used. This script is
intentionally outside unittest discovery. CI may upload tests/artifacts only on
failure; all content in those diagnostic files is synthetic.
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

import uvicorn
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright

from app.config import Settings
from app.main import create_app


PASSWORD = "synthetic-ui-password"
TYPED_STUDENT = "SYNTHETIC typed 001"
SCAN_STUDENT = "SYNTHETIC scan 002"
ARTIFACTS = ROOT / "tests" / "artifacts"


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


def exercise(page, base_url: str, directory: Path) -> None:
    page.goto(base_url, wait_until="domcontentloaded")
    expect(page.locator("#login-view")).to_be_visible()
    page.locator("#password").fill(PASSWORD)
    page.locator("#login-form button[type=submit]").click()
    expect(page.locator("#workspace")).to_be_visible()

    # Create through the editable JSON import, then explicitly approve the key.
    page.locator("#new-assessment").click()
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

    # Reload verifies that final approvals survive browser state loss.
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#export-csv")).to_be_enabled()
    expect(page.locator("#assessment-header")).to_contain_text("4 / 4 approved")


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
        app = create_app(settings)
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
                    exercise(page, base_url, directory)
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
    print("teacher score edits, final approval/export, image upload/cropping, persistence, and local assets.")
    print("This verifies software behavior; no OCR, model inference, or classroom accuracy was evaluated.")


if __name__ == "__main__":
    main()
