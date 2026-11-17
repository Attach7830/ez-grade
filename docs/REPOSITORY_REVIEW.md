# Foundation review

Inspected on 2026-11-16 (America/Los_Angeles). This is a source-code review, not a classroom accuracy evaluation.

## Decision

Build a small independent application around the user's existing Ollama service. Borrow the workflow ideas of separate transcription, answer crops, rubric decisions, and teacher review. Do not copy source from either project in this milestone.

Opengrader is a credible existing local grading application and the stronger starting point if automated stack splitting, printed templates, multiple graders, or Canvas integration become priorities. The immediate need is narrower: explicit Spanish normalization and partial-credit rules, immutable assessment settings, drafts with traceable evidence, a strict approval gate for every result, and a maintainable Linux deployment.

## ai-grader

Repository: https://github.com/lerlerchan/ai-grader

Inspected commit: `aad56782bdbdb3eaadd756118cfc879d90b50af0` (`master`). GitHub connector reads and a local clone agreed. License: MIT, copyright 2026 LerLer Chan.

Useful implementation:

- `ai_grader/submission_loader.py`: loads PDFs, DOCX, text, and Markdown. PDF extraction uses a 50-character threshold to select extracted text versus rendered full-page images.
- `ai_grader/grader.py`: sends to a configurable Ollama host/model.
- `ai_grader/gui.py`: Flask browser app, progress streams, CSRF checks and result downloads.
- `ai_grader/exporter.py`: totals are computed in Python; parse/request failures use `-1` and are flagged instead of silently becoming zeroes.

Material gaps for this task:

- `grader.py:grade` grades images directly; there is no independently reviewable transcription stage.
- `_SYSTEM_PROMPT` directs the model to forgive spelling/garbled OCR and use generous partial credit. Those rules conflict with Spanish spelling, accents, and conjugation assessment.
- `_parse_response` converts numeric grades to integers and uses `max(0, int(val))`. It has no question maxima available, so it cannot enforce upper bounds; decimal partial credit is lost.
- The rubric is passed as free-form text. There is no deterministic objective scorer or enforced criterion/evidence schema.
- `gui.py:_list_cloud_model_names` contacts `https://ollama.com/api/tags`, combines local/cloud catalogs, and accepts an API key. `_run_job` pulls missing models automatically. The inspected version should not be assumed incapable of cloud operation based on its privacy README.
- Jobs live in process memory and temporary directories. Cleanup occurs after one hour or five minutes after download. No persistent answer-review, edit-history, or explicit final-approval model is present in the inspected route set.

Permalinks:

- https://github.com/lerlerchan/ai-grader/blob/aad56782bdbdb3eaadd756118cfc879d90b50af0/ai_grader/grader.py
- https://github.com/lerlerchan/ai-grader/blob/aad56782bdbdb3eaadd756118cfc879d90b50af0/ai_grader/gui.py
- https://github.com/lerlerchan/ai-grader/blob/aad56782bdbdb3eaadd756118cfc879d90b50af0/ai_grader/submission_loader.py
- https://github.com/lerlerchan/ai-grader/blob/aad56782bdbdb3eaadd756118cfc879d90b50af0/ai_grader/exporter.py

## SJSU Opengrader

Package: https://pypi.org/project/opengrader/0.1.0/

The linked GitHub repository returned 404 through the connector. Actual implementation was nevertheless inspected by downloading the published `0.1.0` source distribution and verifying its SHA-256 against PyPI metadata:

`e6103f886b409034d720996b7f5a2e58eadd654a098517ed24b924e82cb16d0e`

Source archive: https://files.pythonhosted.org/packages/24/5f/14be7d50f9752e2cd4044fcc07364ad6d5480372848fb7d39b0e19e6d378/opengrader-0.1.0.tar.gz

License: MIT. Python requirement: `>=3.12,<3.13`.

Verified strengths:

- `core/ocr/vision_prompts.py` has literal OCR instructions preserving mistakes and treating visible instructions as document content. It distinguishes absent handwriting from `[unclear]` and rejects malformed/truncated JSON.
- `core/grading/solutions.py` scores objective answers deterministically; `llm_grader.py` resolves allowed rubric labels and computes points in code.
- `core/history/journal.py` stores old/new grading, comment, rubric, identity, and transcription values.
- `core/submissions/review.py` flags unreadable, ungraded, stale-rubric, missing-region, and other issues. There are source crops, editable regions, SQLite workspaces, and a browser interface.
- `core/app_config/consent.py` implements `OPENGRADER_AI_ALLOW_CLOUD=0`. Cloud features can be disabled; cloud processing is not inherently required.

Differences needing adaptation:

- `llm_grader.py` requests model-reported confidence. `automated.py` applies suggestions using a confidence threshold; review and release use confidence percentages. That is not validated probability and does not match this app's proposed review policy.
- `core/release.py` describes unconfirmed low-confidence AI grades as warnings. `core/export/gradebook.py` sums scores present and leaves ungraded cells blank; it does not itself impose approval of every answer. A stricter final-export gate would be needed.
- `solutions.py` has choice/numeric and exact-answer checks but not the desired per-question Spanish normalization and explicitly scored alternative-answer configuration.
- The package includes a broad pipeline, terminal and browser apps, class management, Canvas, cloud provider integrations, and optional Google authentication. This is more maintenance surface than the focused first version needs.
- Its README explicitly says Linux and Windows installers have not been tested on real machines. We did not run Opengrader itself or assess its grading accuracy.

The evidence supports choosing a smaller implementation for this milestone, not claiming that local alternatives do not exist or that Opengrader lacks meaningful safeguards.
