# EZ Grade 0.3.0 API contract

Single teacher, local FastAPI + SQLite + static browser app. API base `/api`.
All POST/PUT/PATCH/DELETE browser calls include `X-Grader-Request: 1`; credentials same-origin.
Errors use JSON `detail` (string or Pydantic issues). Auth is a local password cookie.

## Models

Question: `{id:string, type:'choice'|'exact'|'rubric', prompt:string, max_points:number,
accepted_answers:string[], partial_answers:[{answer:string,points:number,feedback:string}],
normalization:{trim:boolean,collapse_whitespace:boolean,case_sensitive:boolean,ignore_terminal_punctuation:boolean,accent_policy:'strict'|'ignore_acute'},
rubric:[{id:string,description:string,max_points:number}], notes:string}`.
Rubric criterion maxima must sum to max_points. Objective questions require accepted_answers.
No accent stripping by default. `ignore_acute` affects áéíóú only; ñ and ü remain distinct.

Assessment creation/update payload: `{title,course,instructions,questions,model?:string,num_ctx?:number,synthetic?:boolean,key_source?:string}`.
Retain the returned `key_source` when saving an extracted/generated draft so its original source and provenance remain attached.
Read assessment also has `{id,status:'draft'|'approved',version,approved_at,model,num_ctx,
model_digest:null|string,ollama_version:null|string,prompt_version,prompt_hash,created_at,
max_points,raw_max_points,adjusted_max_points,scoring_revision,scoring_sections,
counts:{pending,needs_review,approved,total},key_source?,active_job?}`.
`version` is a key/rubric hash. Once approved, key + model + context are immutable; create another assessment to change them.
Assessment `max_points` remains the raw maximum for compatibility; `adjusted_max_points` reflects the current section policy.
Section policies are separate from the approved key and do not edit raw scores or answer approvals.

Submission: `{id,assessment_id,student_id,source_kind:'typed'|'scan',original_text?,original_url?,pages:[{index,url,width,height}],created_at}`.
Response: `{id,submission_id,assessment_id,student_id,question_id,source_kind,original_text,
transcription:null|string,transcription_status:'pending'|'ok'|'blank'|'ambiguous'|'failed'|'manual',
transcription_flags:string[],transcription_confirmed:boolean,region:null|{page,x,y,width,height},crop_url:null|string,
suggestion:null|{suggested_points,criteria:[{criterion_id,points,evidence,feedback}],feedback,review_reasons:string[],method:'deterministic'|'model'|'blank'},
suggested_points:null|number,points:null|number,feedback:string,state:'pending'|'needs_review'|'approved',
error:null|string,revision:number,approved_at,history:[{id,kind,created_at,details:object}]}`.
Region uses 0–1 normalized coordinates. Source text and all suggestions/history are immutable.
Transcription edits invalidate current scoring; score edits retain suggestion. Approvals require valid transcription + score.
On scans, teacher must confirm transcription (including true blank) before approval. OCR blanks never become scores automatically.
Updates use `revision` to reject stale edits. A successful mutation returns updated response.

## Routes

GET `/session` -> `{authenticated:boolean}`
POST `/login` `{password}`; POST `/logout`
GET `/health` public minimal liveness
GET `/settings` -> `{ollama_url,model,num_ctx,prompt_version,app_version}` (no secrets)
GET `/ollama` -> `{ok,version?,models:[{name,digest,size,capabilities?}],error?}`; no downloads
POST `/ollama/check` `{model?}` -> capabilities/digest/structured test (no real data)
GET `/assessments` -> assessment array
POST `/assessments` -> assessment
GET `/assessments/{id}` -> assessment
PUT `/assessments/{id}` -> assessment (draft only)
POST `/assessments/{id}/approve` -> assessment
POST `/key/extract` multipart `file`, optional `model`, `num_ctx` -> `{draft,source_id,warnings}`; extracts an explicit supplied answer key or imports structured JSON. Never saves or approves an assessment automatically.
POST `/key/generate` multipart `file`, optional `model`, `num_ctx`, `course`, `instructions`, `default_points` -> `{draft,source_id,warnings,question_reviews,provenance}`; proposes answers from an assessment without a supplied key. See the generation contract below.
GET `/key/sources/{id}/details` -> `{id,name,method,status,warnings,question_reviews,provenance,original_url,pages:[{index,url,width,height}]}`
GET `/key/sources/{id}/pages/{index}` -> locally rendered PNG; zero-based page index
GET `/key/sources/{id}` -> original uploaded source file
GET `/assessments/{id}/scoring` -> current policy and history; see the scaling contract below
POST `/assessments/{id}/scoring/preview` -> current/proposed student totals and `preview_hash`; no mutation
PUT `/assessments/{id}/scoring` -> explicitly confirmed policy and updated history
POST `/demo` -> `{assessment_id}` creates labeled synthetic draft + typed submissions, never approves key.
GET `/assessments/{id}/submissions` -> submission array
POST `/assessments/{id}/submissions/typed` `{student_id,answers:{'1':'B',...}}` -> submission; omitted questions remain pending (not blank); empty string explicitly means blank
POST `/assessments/{id}/submissions/csv` multipart `file` -> `{imported,submission_ids}`; CSV student_id,q1,q2,...; all present cells explicit, missing columns pending
POST `/assessments/{id}/submissions/scan` multipart `file`, `student_id` -> submission
GET `/submissions/{id}` -> submission with pages
POST `/submissions/{id}/region-detections` -> persisted detection with `status:'ready'|'failed'`; no answer mutation
GET `/submissions/{id}/region-detections/latest` -> latest detection or `null`
GET `/region-detections/{id}` -> original detection plus current mapping eligibility
POST `/region-detections/{id}/apply` `{confirmed:true,regions:[{question_id,revision,page,x,y,width,height}]}` -> `{detection_id,applied,responses}`; atomic teacher-confirmed mappings
GET `/assessments/{id}/responses` -> all responses, question-major order
GET `/responses/{id}` -> response including history
PUT `/responses/{id}/region` `{revision,page,x,y,width,height}` -> response; mapping invalidates OCR and grade
PATCH `/responses/{id}` `{revision,transcription?,transcription_confirmed?,points?,feedback?,review_note?}` -> response; transcription changes must be saved separately from a new score
POST `/responses/{id}/approve` `{revision}` -> response
POST `/responses/{id}/reopen` `{revision}` -> response
POST `/assessments/{id}/jobs` `{action:'transcribe'|'grade',response_ids?:string[]}` -> job; asynchronous sequential processing, no approval; omission selects eligible unapproved responses
GET `/jobs/{id}` -> `{id,status:'queued'|'running'|'completed'|'failed'|'interrupted',action,total,done,errors,created_at,finished_at,error?,model_digest?,prompt_version,rubric_version}`
GET `/assessments/{id}/export.csv` -> approved final totals ONLY; HTTP409 until EVERY response is approved
GET `/assessments/{id}/audit.json` -> full local assessment export, responses, history, jobs, timer entries, key source metadata, scoring policies/totals/history, and region detections
POST `/assessments/{id}/time` `{phase:'scanning'|'review'|'baseline',seconds:number,note?:string}` -> time entry
GET `/assessments/{id}/time` -> time entries + totals

Synthetic demo uses beginner Spanish, no vosotros. UI offers all, pending, needs_review, approved filters,
question selector and student queue; original on left, transcription/scoring on right. Plain DOM text for all user/model content.
Review mode can be By question or One quiz. One quiz retains the selected submission across questions and shows every question regardless of state. Keyboard shortcuts when not typing: J/K next/previous student, [/] or left/right arrows previous/next question; Alt+left/right also navigates from editors through the unsaved-edit guard. Ctrl+Enter saves then approves; Ctrl+Shift+Enter in One quiz advances only after successful approval. T focuses transcription and S focuses points. A transcription change requires a fresh score review and stops approval/advancement. Navigation itself never approves an answer.

## Question-region detection

Detection is a local printed-layout operation using Tesseract TSV and image geometry. It does not call Ollama, produce student transcriptions, or use accepted answers/rubrics/scores. Its input is the stored scan plus approved question IDs, types, and prompts. The initial format is an upright, clearly printed numbered worksheet, preferably a single column. All proposed boxes require teacher inspection.

Detection supports at most 10 rendered pages and 100 known questions, with a local OCR budget of 20 seconds per page and 75 seconds per batch. Image and TSV size limits are also enforced. Actual printed wording should be present in each key prompt. Plain numeric IDs, `qN`, and unambiguous section-prefixed numeric IDs can be matched; repeated numbers/aliases and missing or mismatched prompt words remain manual cases. A timeout or unsupported layout does not infer empty answers.

Exact-answer questions require a detected inline or below-prompt answer rule. Missing or obscured rules leave those questions unresolved for manual mapping. Open-space fallback is limited to rubric questions whose prompt has no blank marker. A choice-block fallback explicitly flags that its proposal includes printed question text and needs inspection.

POST detection requires a scanned submission and an approved key. A typed submission returns HTTP 422; an unapproved key or another active detection returns HTTP 409. The call waits for the local detector and persists the outcome without changing response regions, transcriptions, points, or states. Processing failures are returned as a stored detection with `status:'failed'` and `error`; clients must inspect `status`, even after HTTP 200. Retrying creates a new record while retaining the earlier failure.

A detection contains:

```text
id, submission_id, assessment_id, key_version, status, error, created_at,
detector_version, engine_version, source_sha256, page_hashes,
warnings, missing_question_ids, pages,
proposals:[{question_id,response_id,revision,page,x,y,width,height,reason,flags,can_apply,blocked_reason}],
questions:[{question_id,response_id,revision,can_apply,blocked_reason}]
```

Coordinates are normalized to 0–1, with a zero-based integer page. The original proposal and response-revision snapshot are stored immutably; `can_apply` and `blocked_reason` are recomputed for the current response state. A missing proposal is unresolved, not a blank answer. The authoritative missing list comes from approved question IDs absent from the proposals. No confidence percentage is exposed as a validated probability.

After reviewing overlays, apply a selected subset with the strict boolean `confirmed:true` and 1–100 unique regions. Each region uses its original detection `revision`, an approved question ID from that detection, a valid page, and finite coordinates whose positive rectangle fits within the page. Teacher-adjusted boxes are allowed, including a manually drawn box for a question missed by the detector. A response must still be unmapped, unapproved, and without saved transcription or scoring work. Existing blank transcriptions and zero scores count as saved work and are protected.

The server validates every selected answer before creating crops. It rejects stale response revisions, a changed approved key, changed source-page hashes, an existing mapping, or saved reading/scoring work. Sending a newer revision cannot bypass the original detection snapshot. All selected mappings and history entries are committed together; crop or validation failure rolls back the selection. HTTP 409 identifies stale/protected work, while invalid coordinates, duplicates, or a missing confirmation return HTTP 422.

Each applied response records a `detected_region_applied` history event with the detection ID, detector/engine version, original proposal, applied region, whether the teacher changed it, and explicit confirmation. Its reading/grade remains pending with no points until separate transcription and grading actions. Detection results cannot approve answers. Existing manual region editing remains available for deliberate revisions to saved work.

The audit adds `region_detections` without changing `format_version:1`. It retains original proposals, warnings, failures, source hashes, and detector provenance; response histories retain applied changes. The GET latest endpoint returns JSON `null` for an existing submission with no detections yet.

## Key extraction and generation

Extraction expects explicit answers in a `.txt`, `.md`, image, or PDF source. It accepts one raster/PDF page or at most 6,000 text characters; structured `.json` import follows normal assessment validation. A conservative combined request budget can reject otherwise short content. It is not an exact tokenizer or a guarantee that every request below a character limit fits the selected context.

Generation accepts one `.png`, `.jpg`, `.jpeg`, `.webp`, or single-page `.pdf`, with up to 10 short questions. Optional multipart fields are `model` (maximum 200 characters), `num_ctx` (2048–8192), `course` (maximum 200 characters), `instructions` (maximum 2000 characters), and finite `default_points` greater than 0 and at most 1000 (default 1).

The returned draft has ordinary assessment fields, plus its `key_source`, chosen model, and context. Generation proposes objective accepted answers or a model answer and rubric for interpretation. Printed item maxima are used when present; otherwise the explicit teacher default is used. A printed section total is not automatically allocated across questions. Strict accent rules remain the default, and no objective partial-credit alternatives or accent/spelling deductions are inferred.

`question_reviews` contains each `question_id`, source identifier, `status` (`ready` or `incomplete`), `review_reasons`, `points_source` (`printed` or `teacher_default`), `source_points`, `rubric_source`, and `answer_source`. `ready` means the proposal passed structural checks; it does not mean teacher-approved or correct. Incomplete items retain visible wording but have empty accepted answers/rubrics, so normal assessment validation prevents saving until the teacher supplies the missing key. Generation fails explicitly if the model reports the whole page unreadable or overlong, reports that it could not include every item, or returns no usable proposed question. A failed request never creates an assessment or approved key.

Successful source uploads retain the original, page previews, initial warnings, draft, and provenance. The details endpoint exposes initial generation/extraction notes; these do not change when the teacher later edits the saved key. Generation provenance records model/digest, runtime, context, source hash, default-points policy, and a separate key-generation prompt/schema version and hash. The existing transcription/grading prompt version and hashes remain unchanged in 0.3.0. Creating a draft with POST `/assessments` and approving it with POST `/assessments/{id}/approve` remain separate teacher actions.

## Section scaling

Preview and apply require an approved key. GET scoring returns `{revision,sections,policy,note,updated_at,version,history}`; an assessment without a policy starts at revision 0 with `sections: []`. `version` is a hash of the immutable key version and sections.

Preview request:

```json
{
  "revision": 0,
  "sections": [
    {"id": "written", "label": "Written responses", "question_ids": ["3", "4"], "target_points": 10}
  ],
  "note": "Count this section out of 10 points."
}
```

`revision` is a nonnegative integer. Sections have unique identifiers, nonempty labels of at most 200 characters, and nonempty lists of existing question IDs. No question may occur twice or belong to two sections. `target_points` must be finite and positive; it may increase or decrease a section maximum. The note is optional and limited to 1000 characters. At most 100 sections and 100 questions per section are accepted, within the assessment's own question limit.

The preview returns `{revision,sections,policy,students,preview_hash}`. `policy` contains raw and adjusted maxima, section factors, and per-question effective maxima. A factor is `target_points / sum(original question maxima)`. Questions outside sections use factor 1. Each student entry includes raw total, proposed total, previous total/maximum, proposed maximum, `complete`, and `all_approved`. Missing points or failed/pending extraction produce `null` totals, not zeroes. A complete draft total can still be awaiting approval.

Apply uses the same request plus `preview_hash` and the strict boolean `confirmed: true`. A hash is required, bounded to 128 characters. The server recomputes it against the exact proposal, note, current policy revision, key, submissions, response revisions, points, errors, transcription states, and approval states. Stale policy revisions or snapshots return HTTP 409; missing explicit confirmation returns HTTP 422. A successful apply increments the policy revision and records before/after policy, teacher note, preview totals at confirmation, and hash in immutable history. Raw question maxima, rubrics, draft suggestions, raw response points, and answer approvals do not change.

To remove scaling, preview `sections: []` at the current revision and explicitly apply that preview. This restores weight 1 for all questions and records another history entry.

Arithmetic uses decimal inputs and exact rational weighting without rounding individual contributions first. Numeric `total` and `max_points` are unrounded representations; presentation fields ending in `_display` use two decimal places. Exact raw decimal strings and rational `{numerator,denominator}` values are retained in policy/calculation details. For three full-credit one-point questions in a section worth one point, the total is 1 even when individual displayed contributions appear as 0.33. A target of 0.005 remains numerically 0.005, rather than being rounded to 0.01 for validation or export.

## CSV and audit exports

Final CSV remains blocked until every answer has an approved, bounded raw score and no processing error.

| Field | Meaning |
| --- | --- |
| `student_id` | Teacher-provided identifier |
| `q{id}` | Original raw score, preserved as a decimal string |
| `adjusted_q{id}` | Proportional contribution using the current section policy |
| `total`, `max_points` | Adjusted assessment total and maximum; without sections these preserve the original raw totals |
| `percentage` | Adjusted total divided by adjusted maximum, expressed to two decimal places |
| `raw_total`, `raw_max_points` | Original raw totals as decimal strings |
| `scoring_revision`, `scoring_version` | Current policy revision and hash |
| `assessment_version`, `model`, `model_digest`, `prompt_version` | Original grading provenance |

Weighted contributions and totals use their numeric representation without a fixed two-decimal rounding step. They may contain more decimal places than the browser shows. Exact weighted ratios are available in the audit for reproducibility.

Audit `format_version` remains **1** with additive `scoring` data. It contains the current policy/history and per-student calculations, including exact ratios and missing-answer status. Source metadata preserves generation/extraction method, original draft, warnings, question review notes, and provenance. The pilot evaluator continues to compare immutable first **raw** suggestions against raw teacher reference scores, independently of section weighting.

## Storage compatibility

Application 0.3.0 uses SQLite schema **3**. Startup adds `region_detections` to schema 2; an upgrade from schema 1 also adds `scoring_policies` and `scoring_history`. Approved keys, existing mappings, grades, and grading prompt hashes remain unchanged. Take a full backup before upgrading. An older app cannot open schema 3; rollback requires its matching source and pre-upgrade backup.
