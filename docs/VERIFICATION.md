# Verification record

This record describes development checks, not a classroom-accuracy study or a deployment on `ianchat`.

## 0.2.0 release status

This release adds proposed key generation from one assessment page, persistent
source review/provenance, and proportional section policies with preview and
explicit confirmation. It uses database schema 2; schema-1 data is migrated by
adding two tables, while raw keys, responses, and approvals are retained.

The 0.2.0 full software suite passed **134 synthetic automated tests** in the
development workspace. The [GitHub workflow](https://github.com/Attach7830/ez-grade/actions/workflows/checks.yml)
records browser and Docker results for each published commit. New checks cover generated-key validation and failures,
default versus printed points, incomplete items, source provenance, section
arithmetic, overlapping groups, unresolved scores, stale previews, approval
gates, CSV/audit fields, and schema migration. Model-generation tests use
controlled fake responses. Their purpose is to test application behavior, not
the correctness of live model-generated answers.

## Checked in the development workspace

- The full suite passed **134 synthetic automated tests**, including the existing deterministic Spanish scoring, structured-result validation, approval/export, audit, model-provenance, and pilot-metric checks, plus generated-key, section-scaling, migration, and concurrent-read regressions.
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

## Environment and accuracy limits

| Check | Status |
| --- | --- |
| Building or running Docker in the development workspace | Unavailable here; the GitHub workflow builds and starts the container separately |
| Chromium in the development workspace | Blocked by the environment's required socket access; the GitHub workflow runs the browser test separately |
| Access to `ianchat`, its OS packages, or NVIDIA driver | No server access assumed or used |
| Live model compatibility and GPU memory/latency on the RTX 2080 Ti | Not tested on the server; the initial model choice was checked against official compatibility documentation |
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

Next, follow [VALIDATION.md](VALIDATION.md) with previously graded papers. Keep tuning papers separate from evaluation papers, inspect every answer, and measure total scanning/review time. A passing software test suite does not establish that OCR preserves a particular student's handwriting or that model suggestions match your grading policy.
