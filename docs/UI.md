# Browser workflow

The browser loads its HTML, CSS, and JavaScript from this app. It uses no CDN,
analytics, hosted assets, or external fonts.

## First pass

1. Sign in using the local workspace password.
2. Choose **Create an assessment** or **Try synthetic examples**.
3. Enter a question key in the form, import structured JSON, or extract a draft
   from a local file. For objective questions, list accepted answers and explicit
   partial-credit alternatives. For interpretation, define rubric criteria whose
   maxima add up to the question total.
4. Inspect **Answer key**, including accent policies, and select **Approve key**.
   A separate confirmation records that the teacher checked the key. Changes
   after approval require **Copy to new assessment**.
5. Use **Add submissions** for typed responses, a UTF-8 CSV, or one PDF/image per
   student. An uncaptured typed answer remains missing. A captured empty value
   explicitly records a blank.
6. For each scanned answer, open **Map this question on the page**, select a page,
   drag a rectangle around the answer, and save it. Keep crossed-out work inside
   the crop. **Read this crop** transcribes only that region; **Read mapped scans**
   processes eligible mapped answers sequentially.
7. Use **Draft scores**, then review by question across the student queue. The
   original typed text or image remains visible. Click an image to inspect it at
   full size; the original full submission is also available.
8. Save transcription changes first. A changed transcription invalidates its
   previous score. Check the source and confirmation box, then generate a fresh
   draft or enter a teacher score. Save the result and explicitly approve it.
9. Export the final CSV after all responses are approved. The audit export is
   available throughout the process and includes original suggestions and edits.

## Keyboard controls

| Shortcut | Action |
| --- | --- |
| J / K | Next / previous student for the selected question |
| [ / ] | Previous / next question |
| T | Focus transcription |
| S | Focus points |
| Ctrl + Enter | Save reviewed result and approve the current answer |

Single-letter shortcuts do not fire while typing. Ctrl + Enter stops if a save
fails. If transcription changed, it saves that text and stops so a fresh score
can be reviewed. It does not approve a score from the previous transcription.

Navigating away from unsaved answer edits asks whether to discard them. Job
polling does not overwrite unsaved transcription, score, or intake fields.

## Pilot timing

**Pilot & timing** records scanning/intake, reviewing, and manual-baseline time.
The browser timer is an explicit tool; it is not an automatic claim about active
teacher time. Stop it during interruptions. Manual entries can capture scanning
done before opening the browser. A timer records to the assessment where it was
started, even if another assessment is subsequently opened.

## Development verification limits

JavaScript syntax, HTML identifiers/local assets, text escaping, allowed image
URLs, and approval sequencing have been checked here. In particular, failed text
or score saves cannot continue into approval, and a transcription correction
requires a fresh score review.

A complete browser rendering and pointer interaction test was not possible in
this build environment: the installed Chromium executable cannot create its
required socket, and the separate attached browser cannot reach the isolated
test server. The interface therefore still needs a real browser smoke test on
the local installation, including crop dragging, upload, and keyboard controls.
These constraints do not establish any OCR or grading accuracy.
