# Milestone 1 implementation contract

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

Assessment creation/update payload: `{title,course,instructions,questions,model?:string,num_ctx?:number,synthetic?:boolean}`.
Read assessment also has `{id,status:'draft'|'approved',version,approved_at,model,num_ctx,
model_digest:null|string,prompt_version,created_at,max_points,counts:{pending,needs_review,approved,total},key_source?,active_job?}`.
`version` is a key/rubric hash. Once approved, key + model + context are immutable; create another assessment to change them.

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
POST `/key/extract` multipart `file`, optional `model` -> `{draft:{title,course,instructions,questions,model,num_ctx},source_id,warnings}`; draft is NEVER approved automatically. Source URL `/api/key/sources/{source_id}`.
POST `/demo` -> `{assessment_id}` creates labeled synthetic draft + typed submissions, never approves key.
GET `/assessments/{id}/submissions` -> submission array
POST `/assessments/{id}/submissions/typed` `{student_id,answers:{'1':'B',...}}` -> submission; omitted questions remain pending (not blank); empty string explicitly means blank
POST `/assessments/{id}/submissions/csv` multipart `file` -> `{imported,submission_ids}`; CSV student_id,q1,q2,...; all present cells explicit, missing columns pending
POST `/assessments/{id}/submissions/scan` multipart `file`, `student_id` -> submission
GET `/submissions/{id}` -> submission with pages
GET `/assessments/{id}/responses` -> all responses, question-major order
GET `/responses/{id}` -> response including history
PUT `/responses/{id}/region` `{revision,page,x,y,width,height}` -> response; mapping invalidates OCR and grade
PATCH `/responses/{id}` `{revision,transcription?,transcription_confirmed?,points?,feedback?,review_note?}` -> response; transcription changes must be saved separately from a new score
POST `/responses/{id}/approve` `{revision}` -> response
POST `/responses/{id}/reopen` `{revision}` -> response
POST `/assessments/{id}/jobs` `{action:'transcribe'|'grade',response_ids?:string[]}` -> job; asynchronous sequential processing, no approval; omission selects eligible unapproved responses
GET `/jobs/{id}` -> `{id,status:'queued'|'running'|'completed'|'failed'|'interrupted',action,total,done,errors,created_at,finished_at,error?,model_digest?,prompt_version,rubric_version}`
GET `/assessments/{id}/export.csv` -> approved final totals ONLY; HTTP409 until EVERY response is approved
GET `/assessments/{id}/audit.json` -> full local assessment export, responses, history, jobs, timer entries
POST `/assessments/{id}/time` `{phase:'scanning'|'review'|'baseline',seconds:number,note?:string}` -> time entry
GET `/assessments/{id}/time` -> time entries + totals

Synthetic demo uses beginner Spanish, no vosotros. UI offers all, pending, needs_review, approved filters,
question selector and student queue; original on left, transcription/scoring on right. Plain DOM text for all user/model content.
Keyboard shortcuts when not typing: J/K next/previous student, [/] previous/next question; Ctrl+Enter save then approve, T focus transcription, S focus points. Never approve via a shortcut if saves failed.
