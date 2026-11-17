# EZ Grade — local Spanish grading assistant

A single-teacher browser application for drafting and reviewing grades for Spanish 1 Honors, Spanish 2 CP, and Spanish 2 Honors. It uses your **existing local Ollama service**, SQLite, and files on your server. No cloud inference fallback, external account, hosted OCR, analytics, remote fonts, or cloud storage is required.

Version **0.3.0** handles one assessment format and a small batch, including proposed answer keys, suggested question regions on scanned quizzes, review of one student's whole quiz, and proportional section scoring. Every final grade requires teacher approval. It is a pilot tool; synthetic software tests do not establish classroom grading accuracy.

## Start here

1. Follow [installation and configuration](docs/INSTALL.md) to clone the source onto `ianchat`, configure the existing Ollama service, and start it with Docker Compose. GitHub's Code → Download ZIP is an alternative if Git is unavailable on the server.
2. Open `http://ianchat:8766` from your Windows laptop and sign in with the locally generated teacher password.
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

Create an assessment by entering questions, importing a structured key, extracting an existing answer key, or generating a proposed key from an assessment page. Each question has a point limit, accepted answers or rubric criteria, and its own spelling/normalization policy. Every extracted or generated key requires your inspection and explicit approval. Once approved, the raw question values, key, rubric, model, and context settings are frozen for that assessment. Make a new assessment to change them; section weighting has its own reviewed policy described below.

**Generate a draft key from an assessment** accepts one image or a single-page PDF containing up to 10 short questions without a supplied key. The local model proposes answers and, where needed, rubric criteria. Printed item points are retained; otherwise the editable teacher default starts at **1 point per question**. The interface identifies assumed points, proposed rubrics, and incomplete items. Complete every missing answer or rubric before saving, then check the saved key against the source before approving. An unreadable page or failed request does not create an approved key. The original page, initial warnings, and generation provenance remain available after saving.

**Extract existing answer key** expects explicit answers already present in the source. Extraction is limited to one raster/PDF page or 6,000 characters of key text. Each model request also has a conservative budget for the combined prompt and content, so a request below that text limit can still be rejected at the initial 4096-token context. This estimate does not guarantee exact model tokenization. Use manual entry or structured JSON for a longer key. These key-setup limits do not restrict the separate student workflow to one question per submission; choose answer regions on the student's pages as needed.

Import typed answers, a typed-response CSV, or one PDF/image per student. For a clearly printed numbered quiz, local Tesseract can propose question numbers and answer regions. Inspect the boxes on the original page, adjust them where needed, and explicitly apply the selected mappings. Manual page/region selection remains available. Detection creates proposals only; it does not transcribe answers, assign scores, or replace an existing mapping or reviewed answer. Missing or ambiguous questions remain unresolved.

Transcription runs separately from mapping and grading, using the configured local Ollama model. Keep the image beside the literal transcription, correct any reading errors, and confirm the reading before approval. A machine-read blank is a proposed blank that you must confirm; failed extraction is a separate state.

Generate draft scores, then choose **By question** to compare students on one question or **One quiz** to work through every question for one student. One quiz keeps that student selected as you move between questions and shows their approval progress. **Approve & next** saves and approves a valid result before moving on; a changed transcription stops for a fresh score review. Score edits retain the original suggestion in history. Approve each answer explicitly; CSV export remains blocked until every answer is approved.

### Review detected regions

After key approval, choose **Detect regions** for an imported scan or **Detect / review answer regions** in answer review. The initial detector targets upright worksheets with clear, printed question numbers and a simple single-column layout. It uses local OCR text and image geometry to locate known questions; it is not a handwriting reader. Complex layouts, multiple columns, missing numbers, and faint or crossed-out work can produce missed or unsuitable boxes. Check each proposed question number, page, and boundary, including overwritten work and the full answer. Reject an unsuitable proposal and draw the region manually.

Enter the actual printed question wording in the key's prompt field: detection matches both the number and printed prompt words. Generic labels such as “Question 3” may remain unmatched. Unambiguous numeric aliases such as `q3` or `B_21` are supported; repeated printed numbers across sections can be ambiguous and should be mapped manually.

Exact-answer questions need a detectable answer line or rule. If it is absent or obscured by a filled response, the question stays missing for manual mapping. Open-space proposals are reserved for rubric questions without an inline blank marker. These restrictions prevent an uncertain location from being mistaken for an empty student answer.

Applying reviewed boxes is a separate action. Existing mappings, transcriptions, scores, and approvals are protected; the detector cannot silently replace them. Original proposals, warnings, applied adjustments, and detector version are retained for audit. A detector error leaves answers ungraded and available for manual mapping.

## Scoring

| Question kind | Method |
| --- | --- |
| Multiple choice | Deterministic comparison with teacher-approved choices |
| Vocabulary, conjugation, short fixed answers | Deterministic comparison with accepted answers and explicitly scored alternatives |
| Translation or short explanations requiring interpretation | Local model suggests criterion-level points, exact supporting evidence, brief feedback, and review reasons |

Strict accents are the default. Optional per-question acute-accent normalization affects `áéíóú`; it does not merge `ñ` with `n` or `ü` with `u`. You can configure case, whitespace, and terminal punctuation separately. A specific partial-credit alternative is preferable to broad normalization when a missing accent should cost points. For example, accept `está` for full credit and list `esta` as an explicit lower-scoring alternative if that matches your policy.

The model does not decide totals. Code validates structured results and bounds, and calculates totals. Model text cannot approve a grade or change the answer key. Student-written instructions are treated as response content. No self-reported confidence percentage is displayed as an accuracy probability.

### Adjust section totals

After approving the key, open **Answer key → Adjust scoring**. Give a section a name, select its questions, and enter its new positive maximum. For example, a section originally worth 20 points can be worth 10: a raw score of **16/20 becomes 8/10**. Each selected question receives the same proportional factor. Sections cannot overlap, and questions outside a section retain their original weight. Both increases and decreases are supported.

Use **Preview totals**, inspect the current and proposed student totals, then confirm and **Apply reviewed adjustment**. A score edit, new submission, changed approval, or policy change makes an earlier preview stale and requires another preview. Missing or failed answers remain unresolved. The raw question scores, rubric, model suggestions, and answer approvals are preserved. **Restore original totals** clears the sections; preview and apply that change to return every question to its original weight.

Final CSV keeps `q{id}` as the raw score and adds `adjusted_q{id}` for its weighted contribution. `total` and `max_points` describe the adjusted result; `raw_total`, `raw_max_points`, `scoring_revision`, and `scoring_version` retain its context. Numeric totals are calculated without rounding individual contributions first. Displayed values may be rounded; the audit retains exact ratios and the complete policy history. Final export still requires every answer's approval.

## Review shortcuts

| Shortcut | Action |
| --- | --- |
| `J` / `K` | Next / previous student |
| `[` / `]` | Previous / next question |
| Left / right arrow | Previous / next question while outside text inputs |
| `Alt+Left` / `Alt+Right` | Previous / next question from an editor, with the unsaved-edit guard |
| `T` | Focus transcription |
| `S` | Focus points |
| `Ctrl+Enter` | Save; approve only when the transcription is unchanged and score validation succeeds |
| `Ctrl+Shift+Enter` | In One quiz, approve and move to the next question only after successful approval |

Navigation shortcuts do not interrupt normal typing in text fields. Pending, needs-review, and approved filters are available, including a view of every answer. A local audit export retains source references, key-generation provenance, first suggestions, later edits, approvals, model/prompt/rubric versions, section policies and their confirmed preview totals, batch jobs, and recorded teacher time.

If you change the transcription, `Ctrl+Enter` saves it and pauses for a fresh score; it does not approve the old score against new text. A score-only edit is saved and then approved after the save succeeds. A failed save prevents approval.

One quiz shows every question, including already approved and unflagged answers. It disables the state filter so questions do not disappear during review. Next-question navigation stays within the selected quiz; approval at its last question does not switch students automatically.

## Architecture

- **Server:** Python 3.12, FastAPI, one sequential worker for model jobs.
- **Storage:** SQLite plus original uploads, rendered pages, and answer crops under `data/`.
- **Interface:** local HTML, CSS, and JavaScript; no Node build step or hosted assets.
- **Inference:** Ollama on the Linux host. The initial candidate is `qwen3-vl:4b-instruct-q4_K_M`, with a 4096-token context and one answer per request.
- **Printed-question detection:** local Tesseract and image geometry on the CPU; English and Spanish language data are included in the container. This stage does not supply student transcriptions or grades.
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

**Upgrading to 0.3.0:** back up before pulling or building the new source. Startup advances existing databases to schema 3, adding persistent region detections; upgrades from schema 1 also add the section-policy/history tables introduced in 0.2.0. Existing data and raw scores are preserved. The transcription and grading prompts remain unchanged, so previously approved assessments remain usable. An older app cannot open schema 3. Rolling back requires the matching older source and pre-upgrade data backup, as described in the installation guide.

## Current limits

This version expects one file per student and teacher verification of every proposed or manually selected region. The first detection format is a clearly numbered printed worksheet, preferably single-column. Handwriting, crossed-out text, and overwritten answers require visual inspection; a model may miss these conditions. Ambiguity flags are aids to review, not proof that unflagged transcriptions are correct. Entire scanned stacks, full class management, LMS/AMC integrations, and simultaneous graders are outside this milestone.

All examples are synthetic beginner Spanish, without vosotros. Keep the first real pilot small and inspect every answer, including unflagged answers. Measure total scanning and review time against manual grading before deciding whether the workflow saves time.
