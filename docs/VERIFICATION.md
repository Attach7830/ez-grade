# Verification record

This record describes development checks, not a classroom-accuracy study or a deployment on `ianchat`.

## 0.3.0 release status

This release adds local proposals for printed question numbers and answer
regions, teacher verification before mappings are applied, and **One quiz**
review navigation. It uses database schema 3, adding persistent detections while
retaining existing raw keys, mappings, grades, and approvals. The transcription
and grading prompts remain unchanged.

The 0.3.0 full suite passed **174 synthetic automated tests** in the development
workspace, including 22 detector tests and 18 region-route tests. One detector
integration test ran the real local Tesseract binary against a labeled synthetic
printed worksheet. The
[GitHub workflow](https://github.com/Attach7830/ez-grade/actions/workflows/checks.yml)
runs the browser and Docker checks and records their results for each published
commit; consult that run for its actual status. Model tests use controlled fake
responses, and the browser test uses controlled detector proposals through the
real mapping routes. Synthetic printed-page OCR checks exercise software behavior
and do not establish real handwriting or classroom grading accuracy.

## Checked in the development workspace

- All **174 synthetic automated tests** passed, covering deterministic Spanish scoring, structured-result validation, approval/export, audit, model provenance, pilot metrics, generated keys, section scaling, local layout proposals, protected existing work, atomic mapping/crop rollback, stale revisions/source hashes, and database migration.
- Model calls in these tests used controlled mock responses. No real Ollama model or GPU was used to produce the test grades or transcriptions.
- A local live HTTP startup check exercised the running application's basic HTTP behavior. API/in-process tests exercised assessment creation, imports, review, approvals, exports, and stored state.
- Python source compilation and relevant JavaScript/shell syntax checks passed during implementation.
- The setup helper created private configuration/data and refused overwrite. Backup/restore completed a synthetic round trip and rejected unsafe archive paths, links, duplicate entries, and existing restore targets. Docker stop/start orchestration was tested with an explicit stub.
- Compose YAML parsed and its app-only host networking and local data mount were inspected. Synthetic examples are included in the Docker build context.

To rerun the Python suite after installing the pinned dependencies:

```bash
python3 -m unittest discover -s tests -v
```

## GitHub checks

The repository includes `.github/workflows/checks.yml` to run synthetic software tests, a real browser smoke test, and a Compose container build/startup check on pushes to `main`. It installs local Tesseract with `eng` and `spa` before the test suite and verifies those language datasets both on the runner and inside the built container. These are development checks using invented data, not student grading or application hosting. Consult the workflow run for its actual status; adding the workflow does not establish a successful run.

## Environment and accuracy limits

| Check | Status |
| --- | --- |
| Building or running Docker in the development workspace | Unavailable here; the GitHub workflow builds and starts the container separately |
| Chromium in the development workspace | Blocked by the environment's required socket access; the GitHub workflow runs the browser test separately |
| Access to `ianchat`, its OS packages, or NVIDIA driver | No server access assumed or used |
| Live model compatibility and GPU memory/latency on the RTX 2080 Ti | Not tested on the server; the initial model choice was checked against official compatibility documentation |
| Detection accuracy on classroom scans or complex layouts | Not evaluated; each proposed region still requires teacher inspection |
| Handwriting or scoring accuracy on real student work | Not evaluated; examples and automated tests are synthetic |

## Checks to run on your server

Use [INSTALL.md](INSTALL.md) to build and start the app with Compose on port 8766. Confirm the host Ollama version and model, check that cloud features are disabled, then use the app's model check and labeled synthetic demonstration. Check actual GPU offload with `ollama ps` during a request. Inspect the browser workflow, image-region selection, and export on your Windows laptop.

For key generation, use one synthetic page with up to 10 short questions and
inspect every proposed answer, default point value, and rubric. Check that an
incomplete item cannot be saved until completed, and reopen a saved draft to
check its original page and review notes. For scaling, preview and apply a
known example such as 16/20 becoming 8/10, then restore the original weights.
Check raw and adjusted CSV columns, missing-answer handling, stale-preview
rejection, and unchanged answer approvals. Back up before the schema upgrade;
verify retained assessments and images after startup. These are deployment and
workflow checks, not a real-student accuracy evaluation.

For region detection, use a synthetic numbered single-column quiz, review the
overlays, adjust a box, and apply only the selected mappings. Check that missed
questions remain pending, no answer is transcribed or scored by detection, and
existing mappings or reviewed answers are protected. Use **One quiz** to move
through one student, including its last question, and verify that **Approve &
next** advances only after a successful approval. A changed transcription or
failed save must stay in place for review. Record the actual browser result on
your Windows laptop; these deployment checks have not been performed on
`ianchat` here.

Next, follow [VALIDATION.md](VALIDATION.md) with previously graded papers. Keep tuning papers separate from evaluation papers, inspect every answer, and measure total scanning/review time. A passing software test suite does not establish that OCR preserves a particular student's handwriting or that model suggestions match your grading policy.
