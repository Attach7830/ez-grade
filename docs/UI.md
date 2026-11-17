# Browser workflow

The browser loads its HTML, CSS, and JavaScript from this app. It uses no CDN,
analytics, hosted assets, or external fonts.

## First pass

1. Sign in using the local workspace password.
2. Choose **Create an assessment** or **Try synthetic examples**.
3. Enter a question key in the form, import structured JSON, extract an existing
   explicit answer key, or generate a proposed key from an assessment page as
   described below. For objective questions, list accepted answers and explicit
   partial-credit alternatives. For interpretation, define rubric criteria whose
   maxima add up to the question total.
4. Inspect **Answer key**, including accent policies, and select **Approve key**.
   A separate confirmation records that the teacher checked the key. Changes
   after approval require **Copy to new assessment**.
5. Use **Add submissions** for typed responses, a UTF-8 CSV, or one PDF/image per
   student. An uncaptured typed answer remains missing. A captured empty value
   explicitly records a blank.
6. For a numbered scanned quiz, review and apply proposed question regions, or
   open **Map this question on the page**, select a page, draw a rectangle, and
   save it manually. Keep crossed-out work inside the crop. **Read this crop**
   transcribes only the saved region; **Read mapped scans** processes eligible
   mapped answers sequentially. Detection alone does not read or score answers.
7. Use **Draft scores**, then choose **By question** or **One quiz** for review. The
   original typed text or image remains visible. Click an image to inspect it at
   full size; the original full submission is also available.
8. Save transcription changes first. A changed transcription invalidates its
   previous score. Check the source and confirmation box, then generate a fresh
   draft or enter a teacher score. Save the result and explicitly approve it.
9. If needed, use **Answer key → Adjust scoring** to preview and apply proportional
   section weights. Answer review continues to show and edit raw points.
10. Export the final CSV after all responses are approved. The audit export is
   available throughout the process and includes original suggestions and edits.

## Detect and verify question regions

Local Tesseract can propose mappings for known printed question numbers on an
upright, clearly scanned worksheet. Start with a simple single-column quiz. This
stage uses printed text and page geometry; it does not generate the student's
transcription or consult the accepted answers to invent one.
The key's question prompt should contain the printed wording, since detection
checks both number and prompt words. Generic labels can remain unmatched.
Numeric aliases such as `q3` or `B_21` are usable when unambiguous; repeated
printed numbers across sections may need manual mapping.
An exact-answer question also needs a visible answer rule. If the rule is absent
or obscured, draw its region manually; a missing proposal does not mean the
student left that question blank.

Approve the key first. In **Add submissions**, choose **Detect regions** beside
an imported scan, or use **Detect / review answer regions** while reviewing an
answer. The **Review answer regions** dialog names the selected student and
shows the full original page. It opens the latest stored proposals, or runs the
first detection if none exist. **Run detection again** creates a fresh proposal.

Inspect each proposed box over the original page. Check the question number,
page, and complete answer area. Adjust the rectangle if it includes a different
question, cuts off handwriting, or misses work after a line break. Keep crossed-out
and overwritten text available for later reading review. Leave unsuitable
proposals unselected, and map missing questions manually. Choose **Draw / redraw box** to
change a selected question's rectangle, or **Use manual mapping instead** to
return to the ordinary editor. Multiple columns, faint scans,
unusual numbering, and answers outside the expected area need particular care.

Proposals start unchecked. Select individual boxes or explicitly choose
**Select all available boxes**, then exclude any you have not verified. Check
**I inspected each selected box, question number, and page**, then choose
**Save selected mappings**. Apply only mappings you have checked. Applying selected boxes is
atomic: a stale or protected answer stops the operation instead of applying only
part of it. Existing saved mappings, transcriptions, scores, and approved answers
are protected from replacement by detected proposals. Revise an existing mapping
through its ordinary manual review controls if needed. Detection history retains
the original proposals and your applied changes.
Changing boxes or their selection clears the inspection confirmation. Closing
the dialog, rerunning detection, or switching to manual mapping asks before
discarding unsaved changes. A failed detection is read-only; retry it or use
manual mapping.

Only after mappings are applied should you run **Read mapped scans** or **Read
this crop**. Check those literal transcriptions separately before grading and
approval. A missing proposal or detector failure leaves the answer unresolved;
it never means a blank answer or zero points.

## Review one student's quiz

Set **Review mode → One quiz** to keep one student selected while moving through
the assessment's questions. The question dropdown, numbered question strip,
**Previous question**, and **Next question** all stay within that quiz. Its
position and approved-answer count remain visible. The state filter is disabled
in this mode so every question stays available, including approved and unflagged
answers. Select **By question** to compare students on one question again.

**Approve & next** saves the current reviewed result, approves it only after a
successful save and validation, and then opens the next question for the same
student. A changed transcription saves first and stops for a fresh score review;
a failed save or approval does not advance. At the last question, the control
stays on the same student rather than moving into another quiz. Ordinary question
navigation never approves anything. Use **J/K** or the student queue to choose a
different student deliberately.

## Generate a proposed answer key

In **Create an assessment**, choose the class, enter any grading instructions,
and open **Generate a draft key from an assessment**. Upload one image or a
single-page PDF with up to 10 short questions. This operation uses the selected
local model and context settings to propose answers without an existing key.
The separate **Import an answer key** panel expects explicit answers already
present in the uploaded source.

Set **Default points per question** before generating; it starts at 1. A printed
item maximum takes precedence. A section total is not automatically divided
among questions. Review the original page beside the editable key, including
every printed choice, proposed answer, rubric criterion, and point value.
Labels distinguish default points, proposed rubrics, and unresolved answers.
The model does not infer your objective partial-credit or accent-deduction rules;
enter those explicitly.

Complete all items labeled incomplete before selecting **Save draft key**. Empty
answer lists or rubrics intentionally prevent saving an unusable key. A wholly
unreadable page or failed generation request leaves no approved assessment.
After saving, open **Answer key**, check every question against the source, and
use **Approve key** with its teacher confirmation.

Source previews, initial warnings, and question review notes remain accessible
when the assessment is reopened. They describe the original proposal even after
you correct the saved key. Generation model/runtime and prompt version/hash are
retained in the audit. The generation prompt is separate from the existing
transcription/grading prompts; approved assessments from the earlier release
remain usable.

## Adjust a section's point value

After key approval, open **Answer key → Adjust scoring** and **Add section**.
Give it a label, select the question IDs, and enter its target maximum. Groups
must be disjoint and targets positive. Both increases and decreases are allowed;
unassigned questions retain weight 1. A section worth 20 raw points can be set
to 10, making a student's 16/20 contribute 8/10.

Choose **Preview totals** to inspect original and adjusted maxima and each
student's current/proposed total. Incomplete responses remain unresolved, and
a complete draft total can still await answer approval. Check **I reviewed the
proposed section values and student totals**, then **Apply reviewed adjustment**.
Changing the proposal clears its preview. If another operation changes a score,
submission, approval, or policy, the server rejects the stale preview and asks
for a new one.

The raw question values, rubric, original suggestions, scores, and existing
answer approvals are preserved. **Restore original totals** clears the proposed
sections; preview and explicitly apply that change to return all weights to 1.
The dialog retains adjustment history and teacher notes.

CSV `q{id}` columns contain raw points, while `adjusted_q{id}` columns contain
weighted contributions. `total` and `max_points` use the adjusted policy;
`raw_total`, `raw_max_points`, and scoring version/revision identify both views.
Displayed values may be rounded, but totals are calculated without first
rounding each contribution. The audit retains exact ratios and the totals that
were previewed at each confirmation. Every answer still requires approval
before final CSV export.

## Keyboard controls

| Shortcut | Action |
| --- | --- |
| J / K | Next / previous student for the selected question |
| [ / ] | Previous / next question |
| Left / right arrow | Previous / next question outside text inputs |
| Alt + Left / Alt + Right | Previous / next question from editors, with the unsaved-edit guard |
| T | Focus transcription |
| S | Focus points |
| Ctrl + Enter | Save reviewed result and approve the current answer |
| Ctrl + Shift + Enter | In One quiz, approve successfully and then move to its next question |

Single-letter shortcuts do not fire while typing. Ctrl + Enter stops if a save
fails. If transcription changed, it saves that text and stops so a fresh score
can be reviewed. It does not approve a score from the previous transcription.

Navigating away from unsaved answer edits asks whether to discard them. Job
polling does not overwrite unsaved transcription, score, or intake fields.
Question navigation in One quiz preserves the selected student. A shortcut that
would leave unsaved work uses the same guard as the visible navigation controls.

## Pilot timing

**Pilot & timing** records scanning/intake, reviewing, and manual-baseline time.
The browser timer is an explicit tool; it is not an automatic claim about active
teacher time. Stop it during interruptions. Manual entries can capture scanning
done before opening the browser. A timer records to the assessment where it was
started, even if another assessment is subsequently opened.

## Development verification limits

Earlier development checks covered JavaScript syntax, HTML identifiers/local
assets, text escaping, allowed image URLs, and approval sequencing. In
particular, failed text or score saves cannot continue into approval, and a
transcription correction requires a fresh score review. See
[VERIFICATION.md](VERIFICATION.md) for the current release's test status.

A complete browser rendering and pointer interaction test was not possible in
this build environment: the installed Chromium executable cannot create its
required socket, and the separate attached browser cannot reach the isolated
test server. The GitHub workflow runs the real-browser test separately, including crop
dragging, uploads, keyboard controls, section scaling/restoration, and key
generation with an explicitly controlled fake model response. Consult the
published commit's workflow result for its status. Check the interface on your
Windows laptop after deployment as well. Browser checks establish software
behavior, not live OCR or grading accuracy.
