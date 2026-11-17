# Verification record

This record describes development checks, not a classroom-accuracy study or a deployment on `ianchat`.

## Checked in the development workspace

- The full suite passed **81 synthetic automated tests**. The suite covers deterministic Spanish normalization and partial credit, score bounds, structured-result validation, failure handling, approval/export gates, audit history, model provenance, and pilot-evaluation metrics.
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

The repository includes `.github/workflows/checks.yml` to run synthetic software tests, a real browser smoke test, and a Compose container build/startup check on pushes to `main`. These are development checks using invented data, not student grading or application hosting. Consult the workflow run for its actual status; adding the workflow does not establish a successful run.

## Not checked here

| Check | Status |
| --- | --- |
| Building or running the Docker image | Not performed: a Docker executable/daemon was unavailable in the development environment |
| Rendered Chromium/browser interaction | Not completed: the environment blocked the browser's required socket access; no rendered-browser QA is claimed |
| Access to `ianchat`, its OS packages, or NVIDIA driver | No server access assumed or used |
| Live model compatibility and GPU memory/latency on the RTX 2080 Ti | Not tested on the server; the initial model choice was checked against official compatibility documentation |
| Handwriting or scoring accuracy on real student work | Not evaluated; examples and automated tests are synthetic |

## Checks to run on your server

Use [INSTALL.md](INSTALL.md) to build and start the app with Compose. Confirm the host Ollama version and model, check that cloud features are disabled, then use the app's model check and labeled synthetic demonstration. Check actual GPU offload with `ollama ps` during a request. Inspect the browser workflow, image-region selection, and export on your Windows laptop.

Next, follow [VALIDATION.md](VALIDATION.md) with previously graded papers. Keep tuning papers separate from evaluation papers, inspect every answer, and measure total scanning/review time. A passing software test suite does not establish that OCR preserves a particular student's handwriting or that model suggestions match your grading policy.
