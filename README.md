# EZ Grade — local Spanish grading assistant

A single-teacher browser application for drafting and reviewing grades for Spanish 1 Honors, Spanish 2 CP, and Spanish 2 Honors. It uses your **existing local Ollama service**, SQLite, and files on your server. No cloud inference fallback, external account, hosted OCR, analytics, remote fonts, or cloud storage is required.

This version handles one assessment format and a small batch. Every final grade requires teacher approval. It is a pilot tool; synthetic software tests do not establish classroom grading accuracy.

## Start here

1. Follow [installation and configuration](docs/INSTALL.md) to clone the source onto `ianchat`, configure the existing Ollama service, and start it with Docker Compose. GitHub's Code → Download ZIP is an alternative if Git is unavailable on the server.
2. Open `http://ianchat:8765` from your Windows laptop and sign in with the locally generated teacher password.
3. Load the clearly labeled synthetic demonstration, review its answer key, and approve the key before generating draft scores.
4. Try your own previously graded examples using the [pilot protocol](docs/VALIDATION.md).

**Deployment status:** this source is built and tested in a separate development workspace. It has not been deployed to `ianchat`. No connection to your server, driver inspection, live GPU benchmark, or real-student accuracy evaluation has been performed here. [Verification notes](docs/VERIFICATION.md) distinguish tested software behavior from deployment checks still to run.

## Source on GitHub, application on your server

Source repository: [Attach7830/ez-grade](https://github.com/Attach7830/ez-grade). Clone it onto your AI server using the GitHub installation path in [INSTALL.md](docs/INSTALL.md).

GitHub stores the application source and synthetic examples. Submissions, images, grades, local configuration, and backups stay on your equipment and are excluded from Git tracking. The source repository is public, so HTTPS cloning needs no GitHub login. The grading application uses its own local password and runs without a GitHub connection.

From the Linux server:

```bash
mkdir -p ~/apps
cd ~/apps
git clone https://github.com/Attach7830/ez-grade.git
cd ez-grade
```

Continue with the service checks and Ollama configuration in [INSTALL.md](docs/INSTALL.md), then run `python3 scripts/init_config.py` and `docker compose up -d --build`. Finish an active assessment and back up before updating. The documented update command uses `git pull --ff-only` and stops if source changes would need to be merged.

## Automated checks

The GitHub workflow runs synthetic tests, a browser workflow, and a Docker Compose build/startup check. It does not deploy the application or receive student data. [Verification notes](docs/VERIFICATION.md) describe which checks were run during development.

## Workflow

Create an assessment by entering questions or importing a structured key. Each question has a point limit, accepted answers or rubric criteria, and its own spelling/normalization policy. An automatically extracted key remains a draft until you inspect and approve it. Once approved, the key, rubric, model, and context settings are frozen for that assessment. Make a new assessment to change them.

Automatic key extraction is limited to one raster/PDF page or 6,000 characters of key text. Each model request also has a conservative budget for the combined prompt and content, so a request below that text limit can still be rejected at the initial 4096-token context. This estimate does not guarantee exact model tokenization. Use manual entry or structured JSON for a longer key. This key-extraction limit does not restrict the separate student workflow to one question per submission; choose answer regions on the student's pages as needed.

Import typed answers, a typed-response CSV, or one PDF/image per student. For paper submissions, select the page and draw a region around each answer. Transcription runs separately from grading. Keep the image beside the literal transcription, correct any reading errors, and confirm the reading before approval. A machine-read blank is a proposed blank that you must confirm; failed extraction is a separate state.

Generate draft scores, review question by question across students, and save corrections. Transcription edits invalidate the current draft score so it cannot be approved against changed text. Score edits retain the original suggestion in history. Approve each answer explicitly; CSV export remains blocked until every answer is approved.

## Scoring

| Question kind | Method |
| --- | --- |
| Multiple choice | Deterministic comparison with teacher-approved choices |
| Vocabulary, conjugation, short fixed answers | Deterministic comparison with accepted answers and explicitly scored alternatives |
| Translation or short explanations requiring interpretation | Local model suggests criterion-level points, exact supporting evidence, brief feedback, and review reasons |

Strict accents are the default. Optional per-question acute-accent normalization affects `áéíóú`; it does not merge `ñ` with `n` or `ü` with `u`. You can configure case, whitespace, and terminal punctuation separately. A specific partial-credit alternative is preferable to broad normalization when a missing accent should cost points. For example, accept `está` for full credit and list `esta` as an explicit lower-scoring alternative if that matches your policy.

The model does not decide totals. Code validates structured results and bounds, and calculates totals. Model text cannot approve a grade or change the answer key. Student-written instructions are treated as response content. No self-reported confidence percentage is displayed as an accuracy probability.

## Review shortcuts

| Shortcut | Action |
| --- | --- |
| `J` / `K` | Next / previous student |
| `[` / `]` | Previous / next question |
| `T` | Focus transcription |
| `S` | Focus points |
| `Ctrl+Enter` | Save; approve only when the transcription is unchanged and score validation succeeds |

Navigation shortcuts do not interrupt normal typing in text fields. Pending, needs-review, and approved filters are available, including a view of every answer. A local audit export retains source references, first suggestions, later edits, approvals, model/prompt/rubric versions, batch jobs, and recorded teacher time.

If you change the transcription, `Ctrl+Enter` saves it and pauses for a fresh score; it does not approve the old score against new text. A score-only edit is saved and then approved after the save succeeds. A failed save prevents approval.

## Architecture

- **Server:** Python 3.12, FastAPI, one sequential worker for model jobs.
- **Storage:** SQLite plus original uploads, rendered pages, and answer crops under `data/`.
- **Interface:** local HTML, CSS, and JavaScript; no Node build step or hosted assets.
- **Inference:** Ollama on the Linux host. The initial candidate is `qwen3-vl:4b-instruct-q4_K_M`, with a 4096-token context and one answer per request.
- **Container:** one nonroot app container using Linux host networking; no duplicate Ollama service or container GPU driver setup.

[Foundation review](docs/REPOSITORY_REVIEW.md) explains why this focused implementation was chosen after inspecting ai-grader and the published SJSU Opengrader source. Opengrader already provides meaningful local capabilities and remains a useful reference for later milestones.

## Included maintenance tools

```bash
python3 scripts/init_config.py
bash scripts/backup.sh
bash scripts/restore.sh /path/to/backup.tar.gz /path/to/new-restore-directory
python3 scripts/evaluate_pilot.py --audit audit.json --truth teacher_truth.csv --out pilot-results.json
```

The setup script never overwrites an existing configuration. Backup briefly stops only this app, captures the complete data directory and private configuration, then restarts it if it was running. Restore validates archive paths and restores into a new directory; it never executes archive content or overwrites the active data. Detailed activation and update steps are in [INSTALL.md](docs/INSTALL.md).

## Current limits

This version expects one file per student and manual question mapping for scans. Handwriting, crossed-out text, and overwritten answers require visual inspection; a model may miss these conditions. Ambiguity flags are aids to review, not proof that unflagged transcriptions are correct. Entire scanned stacks, full class management, LMS/AMC integrations, simultaneous graders, and automatic answer-region detection are outside this milestone.

All examples are synthetic beginner Spanish, without vosotros. Keep the first real pilot small and inspect every answer, including unflagged answers. Measure total scanning and review time against manual grading before deciding whether the workflow saves time.
