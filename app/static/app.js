/* EZ Grade: local-only browser client. No external dependencies or requests. */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const e = escapeHTML;
const endpoint = value => encodeURIComponent(String(value));
const safeURL = value => typeof value === "string" && value.startsWith("/api/") && !value.includes("\\") ? value : "";
const fmt = n => n === null || n === undefined || !Number.isFinite(Number(n)) ? "—" : Number(n).toLocaleString(undefined, {maximumFractionDigits: 3});
const fmtFactor = n => n === null || n === undefined || !Number.isFinite(Number(n)) ? "—" : Number(n).toLocaleString(undefined, {maximumSignificantDigits: 8});
const dateText = value => value ? new Date(value).toLocaleString() : "—";
const stateLabel = value => ({pending:"Pending", needs_review:"Needs review", approved:"Approved", draft:"Draft key"}[value] || value || "Pending");
const badge = (state, label) => `<span class="badge ${["pending","needs_review","approved","draft","synthetic"].includes(state) ? state : "pending"}">${e(label || stateLabel(state))}</span>`;
const blankQuestion = index => ({id:String(index),type:"exact",prompt:"",max_points:1,accepted_answers:[],partial_answers:[],normalization:{trim:true,collapse_whitespace:true,case_sensitive:false,ignore_terminal_punctuation:false,accent_policy:"strict"},rubric:[],notes:""});
const state = {settings:null, assessments:[], assessment:null, submissions:[], responses:[], current:null, questionId:null, filter:"all", selectedId:null, tab:"review", assessmentSequence:0, detailSequence:0, transcriptionDirty:false, scoreDirty:false, crop:null, editor:null, editorId:null, keySource:null, extractionWarnings:[], activeJob:null, polling:null, toastTimer:null, timer:null, timerInterval:null, auth:false};
Object.assign(state,{editorSession:0,keyRequestSequence:0,keyReviews:[],keyDetails:null,scoring:null,scoringSession:0,sectionSerial:0});
Object.assign(state,{reviewMode:"question",quizSubmissionId:null,questionModeFilter:"all"});
Object.assign(state,{regionEditor:null,regionSession:0});
try { if(localStorage.getItem("ez-grade.review-mode")==="quiz")state.reviewMode="quiz"; } catch {}
const keySourceCache = new Map();

async function api(path, {method="GET",body,form}={}) {
  const headers = {};
  if (method !== "GET") headers["X-Grader-Request"] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try { response = await fetch(`/api${path}`, {method,headers,body:form || (body === undefined ? undefined : JSON.stringify(body)),credentials:"same-origin",cache:"no-store"}); }
  catch { throw new Error("The local server could not be reached. Your unsaved edits are still on this page."); }
  if (!response.ok) {
    let detail;
    try { const result=await response.json(); detail=result.detail ?? result.error ?? result; } catch { detail=`Request failed (${response.status}).`; }
    if (response.status === 401 && path !== "/login") showLogin();
    if (Array.isArray(detail)) detail=detail.map(item=>`${(item.loc || []).filter(x=>x!=="body").join(" → ")}: ${item.msg || JSON.stringify(item)}`).join("\n");
    if (typeof detail !== "string") detail=JSON.stringify(detail);
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, error=false) {
  const node=$("#toast"); node.textContent=message; node.classList.toggle("error",error); node.classList.remove("hidden");
  clearTimeout(state.toastTimer); state.toastTimer=setTimeout(()=>node.classList.add("hidden"), error?10000:5500);
}
async function busy(button, task, label="Working…") {
  if (!button || button.disabled) return;
  const old=button.textContent;
  const controls=button.closest("#response-review") ? $$("input,textarea,select,button",$("#response-review")).map(node=>[node,node.disabled]) : [];
  controls.forEach(([node])=>{node.disabled=true;}); button.disabled=true; button.textContent=label;
  try { return await task(); } catch(error) { toast(error.message,true); throw error; }
  finally { controls.forEach(([node,wasDisabled])=>{if(node.isConnected)node.disabled=wasDisabled;}); if(button.isConnected) { button.textContent=old; button.disabled=["batch-transcribe","batch-grade"].includes(button.id) ? !!state.activeJob || state.assessment?.status!=="approved" : false; } }
}
function run(button, task, label) { return busy(button, task, label).catch(()=>{}); }
function leaveEdits() {
  if ((state.transcriptionDirty || state.scoreDirty) && !window.confirm("Discard the unsaved changes to this answer?")) return false;
  state.transcriptionDirty=false; state.scoreDirty=false; return true;
}
function markDirty(kind) {
  state[kind==="transcription"?"transcriptionDirty":"scoreDirty"]=true;
  const node=$("#unsaved-indicator"); if(node) node.textContent="Unsaved changes";
}
function showLogin() {
  state.auth=false; $("#workspace").classList.add("hidden"); $("#login-view").classList.remove("hidden"); $("#logout").classList.add("hidden"); $("#settings-open").classList.add("hidden");
  clearTimeout(state.polling); state.polling=null;
  for (const dialog of $$("dialog[open]")) dialog.close();
}
async function enterWorkspace() {
  state.auth=true; $("#login-view").classList.add("hidden"); $("#workspace").classList.remove("hidden"); $("#logout").classList.remove("hidden"); $("#settings-open").classList.remove("hidden");
  const [settings,assessments]=await Promise.all([api("/settings"),api("/assessments")]);
  state.settings=settings; state.assessments=assessments; renderAssessmentList();
  if(state.assessment && assessments.some(a=>a.id===state.assessment.id)) await selectAssessment(state.assessment.id,true);
  else if(assessments.length) await selectAssessment(assessments[0].id,true);
}
function renderAssessmentList() {
  $("#assessment-list").innerHTML=state.assessments.length?state.assessments.map(a=>`<button class="assessment-item ${a.id===state.assessment?.id?"active":""}" data-assessment="${e(a.id)}"><strong>${e(a.title)}</strong><small>${e(a.course)}</small><span class="meta">${badge(a.status,a.status==="approved"?"Key approved":"Draft key")}${a.synthetic?badge("synthetic","Synthetic"):""}</span></button>`).join(""):`<p class="small muted">Your assessments will appear here.</p>`;
}
async function selectAssessment(id, force=false) {
  if(!force && !leaveEdits()) return;
  const selectionSequence=++state.assessmentSequence; ++state.detailSequence;
  clearTimeout(state.polling); state.activeJob=null; state.current=null; state.selectedId=null; state.questionId=null;state.quizSubmissionId=null;
  const [a,submissions,responses]=await Promise.all([api(`/assessments/${endpoint(id)}`),api(`/assessments/${endpoint(id)}/submissions`),api(`/assessments/${endpoint(id)}/responses`)]);
  if(selectionSequence!==state.assessmentSequence)return;
  state.assessment=a; state.submissions=submissions; state.responses=responses; state.questionId=a.questions[0]?.id || null;
  $("#welcome").classList.add("hidden"); $("#assessment-workspace").classList.remove("hidden"); $("#job-status").classList.add("hidden");
  renderAssessmentList(); renderHeader(); renderQuestionSelect(); renderIntake(); renderKey(); await renderPilot(); renderQueue(); setTab("review");
  await selectFirstResponse();
  if(a.active_job) { const job=typeof a.active_job==="string"?{id:a.active_job}:a.active_job; state.activeJob=job; pollJob(job.id); }
}
async function refreshAssessment({refreshCurrent=true}={}) {
  if(!state.assessment) return;
  const id=state.assessment.id;
  const [a,responses,submissions]=await Promise.all([api(`/assessments/${endpoint(id)}`),api(`/assessments/${endpoint(id)}/responses`),api(`/assessments/${endpoint(id)}/submissions`)]);
  if(state.assessment?.id!==id) return;
  state.assessment=a; state.responses=responses; state.submissions=submissions;
  const i=state.assessments.findIndex(v=>v.id===id); if(i>=0) state.assessments[i]=a; else state.assessments.unshift(a);
  renderHeader(); renderAssessmentList(); renderQueue(); renderKey(); if(state.tab!=="intake")renderIntake();
  if(refreshCurrent && !state.transcriptionDirty && !state.scoreDirty) {
    if(state.selectedId && responses.some(r=>r.id===state.selectedId)) await selectResponse(state.selectedId,true);
    else await selectFirstResponse();
  }
}
function renderHeader() {
  const a=state.assessment, counts=a.counts || {};
  const total=counts.total ?? state.responses.length, approved=counts.approved ?? state.responses.filter(r=>r.state==="approved").length;
  const pending=counts.pending ?? state.responses.filter(r=>r.state==="pending").length, needs=counts.needs_review ?? state.responses.filter(r=>r.state==="needs_review").length;
  const rawMaximum=a.raw_max_points ?? a.max_points ?? a.questions.reduce((n,q)=>n+q.max_points,0);
  const adjustedMaximum=a.adjusted_max_points ?? rawMaximum;
  const adjusted=!!a.scoring_sections?.length;
  $("#assessment-header").innerHTML=`<div class="assessment-title-row"><div><p class="eyebrow">ASSESSMENT WORKSPACE</p><h1>${e(a.title)}</h1><div class="assessment-meta"><span>${e(a.course)}</span><span>·</span><span>${a.questions.length} questions</span><span>·</span><span>${adjusted?`${fmt(adjustedMaximum)} adjusted points · ${fmt(rawMaximum)} original points`:`${fmt(rawMaximum)} points`}</span>${a.synthetic?badge("synthetic","Synthetic examples"):""}</div></div><div class="assessment-actions"><button id="adjust-scoring-header" class="quiet" ${a.status!=="approved"?"disabled":""} title="Approve the answer key before adjusting section totals">Adjust scoring</button><button id="export-csv" class="secondary" ${total===0 || approved!==total?"disabled":""} title="Every response must be approved before final export; adjusted exports also preserve raw score columns">${adjusted?"Export adjusted CSV":"Export final CSV"}</button><button id="export-audit" class="quiet">Audit file</button></div></div><div class="progress-summary"><span><b>${approved}</b> / ${total} approved</span><div class="progress-track" aria-label="Approval progress"><span style="width:${total?approved/total*100:0}%"></span></div><span><b>${needs}</b> need review</span><span><b>${pending}</b> pending</span></div>`;
  $("#adjust-scoring-header").addEventListener("click",event=>run(event.currentTarget,()=>openScoring()));
  $("#export-csv").addEventListener("click",event=>run(event.currentTarget,()=>download(`/assessments/${endpoint(a.id)}/export.csv`,`${slug(a.title)}-final.csv`),"Exporting…"));
  $("#export-audit").addEventListener("click",event=>run(event.currentTarget,()=>download(`/assessments/${endpoint(a.id)}/audit.json`,`${slug(a.title)}-audit.json`),"Exporting…"));
  const gate=$("#key-gate"); gate.classList.toggle("hidden",a.status==="approved");
  if(a.status!=="approved") { gate.innerHTML=`<div><strong>Check the key before grading</strong><p>This draft has not been approved. Review accepted answers, accent rules, and point values.</p></div><button class="secondary" id="gate-key">Review answer key</button>`; $("#gate-key").addEventListener("click",()=>setTab("key")); }
  $("#batch-transcribe").disabled=a.status!=="approved" || !!state.activeJob;
  $("#batch-grade").disabled=a.status!=="approved" || !!state.activeJob;
}
function renderQuestionSelect() {
  $("#question-filter").innerHTML=state.assessment.questions.map(q=>`<option value="${e(q.id)}" ${String(q.id)===String(state.questionId)?"selected":""}>Q${e(q.id)} · ${e(q.prompt.slice(0,65))}</option>`).join("");
}
function setTab(tab) {
  state.tab=tab;
  $$(".tabs button").forEach(node=>node.classList.toggle("active",node.dataset.tab===tab));
  $$(".tab-panel").forEach(node=>node.classList.toggle("hidden",node.id!==`tab-${tab}`));
}
function visibleResponses() {
  return state.responses.filter(r=>String(r.question_id)===String(state.questionId) && (state.reviewMode==="quiz" || state.filter==="all" || r.state===state.filter));
}
function renderQueue() {
  const rows=visibleResponses(); $("#queue-count").textContent=String(rows.length);
  $("#review-mode").value=state.reviewMode;$("#queue-label").textContent=state.reviewMode==="quiz"?"Student quizzes":"Students";
  $("#state-filter").disabled=state.reviewMode==="quiz";$("#state-filter").title=state.reviewMode==="quiz"?"One quiz shows every question for the selected student.":"";
  if(state.reviewMode==="quiz")$("#state-filter").value="all";
  $("#student-list").innerHTML=rows.length?rows.map(r=>{
    const quiz=state.responses.filter(answer=>answer.submission_id===r.submission_id),approved=quiz.filter(answer=>answer.state==="approved").length;
    const detail=state.reviewMode==="quiz"?`${badge(approved===quiz.length?"approved":"pending",`${approved} / ${quiz.length} approved`)}`:`${badge(r.state)}<span class="student-score">${fmt(r.points ?? r.suggested_points)} / ${fmt(questionFor(r)?.max_points)}</span>`;
    return `<button class="student-row ${r.id===state.selectedId?"active":""}" data-response="${e(r.id)}"><strong>${e(r.student_id)}</strong><span class="student-row-meta">${detail}</span></button>`;
  }).join(""):`<p class="small muted" style="padding:12px">No answers in this view.</p>`;
  renderQuizNavigation();
}
function questionFor(response) { return state.assessment?.questions.find(q=>String(q.id)===String(response?.question_id)); }
function renderQuizNavigation() {
  const node=$("#quiz-navigation"),a=state.assessment;
  node.classList.toggle("hidden",state.reviewMode!=="quiz" || !a);if(state.reviewMode!=="quiz" || !a)return;
  const sub=state.submissions.find(item=>item.id===state.quizSubmissionId),answers=state.responses.filter(r=>r.submission_id===state.quizSubmissionId);
  const index=a.questions.findIndex(q=>String(q.id)===String(state.questionId)),approved=answers.filter(r=>r.state==="approved").length;
  node.innerHTML=`<div class="quiz-navigation-top"><div><strong>${e(sub?.student_id || "Select a student quiz")}</strong><span id="quiz-position">Question ${index+1} of ${a.questions.length}</span><span id="quiz-approved-count">${approved} / ${a.questions.length} approved</span></div><div class="button-row"><button id="previous-question" class="secondary" ${index<=0?"disabled":""}>← Previous question</button><button id="next-question" class="secondary" ${index>=a.questions.length-1?"disabled":""}>Next question →</button></div></div><div class="quiz-question-strip" aria-label="Questions in this quiz">${a.questions.map(q=>{const answer=answers.find(r=>String(r.question_id)===String(q.id));return `<button class="quiz-question ${String(q.id)===String(state.questionId)?"active":""} ${answer?.state==="approved"?"complete":""}" data-quiz-question="${e(q.id)}" aria-label="Question ${e(q.id)}, ${e(stateLabel(answer?.state))}" ${String(q.id)===String(state.questionId)?'aria-current="step"':""}>Q${e(q.id)}${answer?.state==="approved"?'<span aria-hidden="true"> ✓</span>':""}</button>`;}).join("")}</div><p class="small muted" style="margin:8px 0 0">Every question stays available. Moving between questions does not approve an answer.</p>`;
  $("#previous-question").addEventListener("click",()=>navigateQuestion(-1).catch(error=>toast(error.message,true)));
  $("#next-question").addEventListener("click",()=>navigateQuestion(1).catch(error=>toast(error.message,true)));
  $$("[data-quiz-question]",node).forEach(button=>button.addEventListener("click",()=>selectQuestion(button.dataset.quizQuestion).catch(error=>toast(error.message,true))));
}
async function setReviewMode(mode) {
  if(mode===state.reviewMode)return;
  if(!leaveEdits()){$("#review-mode").value=state.reviewMode;return;}
  if(mode==="quiz"){state.questionModeFilter=state.filter;state.filter="all";state.quizSubmissionId=state.current?.submission_id || state.quizSubmissionId || state.responses[0]?.submission_id || null;}
  else {state.filter=state.questionModeFilter;$("#state-filter").value=state.filter;}
  state.reviewMode=mode;try{localStorage.setItem("ez-grade.review-mode",mode);}catch{}
  const current=visibleResponses().find(r=>r.id===state.selectedId);
  if(current)await selectResponse(current.id,true);else await selectFirstResponse();
}
async function selectQuestion(id) {
  if(!state.assessment || String(id)===String(state.questionId))return;
  if(!leaveEdits()){$("#question-filter").value=state.questionId;return;}
  if(!state.assessment.questions.some(q=>String(q.id)===String(id)))return;
  const previous=state.questionId;state.questionId=id;renderQuestionSelect();
  try{await selectFirstResponse();}catch(error){if(String(state.questionId)===String(id)){state.questionId=previous;renderQuestionSelect();renderQueue();}throw error;}
}
async function selectFirstResponse() {
  const rows=visibleResponses();
  const preferred=state.reviewMode==="quiz" && state.quizSubmissionId?rows.find(row=>row.submission_id===state.quizSubmissionId):rows[0];
  if(preferred) await selectResponse(preferred.id,true);
  else { state.current=null; state.selectedId=null; renderQueue(); $("#response-review").innerHTML=`<div class="empty compact"><h2>${state.responses.length?"No answers in this view":"No answers yet"}</h2><p>${state.responses.length?"Choose another question or show every answer.":"Add a typed response or one PDF/image per student to begin."}</p>${state.responses.length?"":'<button id="empty-add" class="primary">Add submissions</button>'}</div>`; $("#empty-add")?.addEventListener("click",()=>setTab("intake")); }
}
async function selectResponse(id, force=false) {
  if(!force && !leaveEdits()) return;
  const seq=++state.detailSequence;
  const r=await api(`/responses/${endpoint(id)}`);
  if(seq!==state.detailSequence) return;
  state.current=r; state.selectedId=id; state.quizSubmissionId=r.submission_id;state.questionId=r.question_id;state.crop=null; state.transcriptionDirty=false; state.scoreDirty=false;
  renderQuestionSelect();
  renderQueue(); renderResponse();
}
function renderResponse() {
  const r=state.current, q=questionFor(r); if(!r || !q) return;
  const approved=r.state==="approved", disabled=approved?"disabled":"";
  const sub=state.submissions.find(s=>s.id===r.submission_id), suggestion=r.suggestion;
  const image=safeURL(r.crop_url), original=safeURL(sub?.original_url);
  const flags=[...(r.transcription_flags || []),...(suggestion?.review_reasons || [])];
  const transLabel={pending:"Not read yet",ok:"Machine transcription",blank:"Reported blank — check original",ambiguous:"Ambiguous handwriting",failed:"Reading failed",manual:"Teacher transcription"}[r.transcription_status] || r.transcription_status;
  let sourceHTML;
  if(r.source_kind==="typed") sourceHTML=`<div class="original-text ${r.original_text===""?"blank":""}">${r.original_text===null || r.original_text===undefined?'<span class="muted">No response was imported for this question. Missing data is not a blank answer.</span>':r.original_text===""?"Explicitly imported as a blank answer.":e(r.original_text)}</div><p class="source-caption">Original imported text · preserved as received</p>`;
  else sourceHTML=`${image?`<a href="${e(image)}" target="_blank" rel="noopener"><img class="answer-crop" src="${e(image)}" alt="Original answer region for question ${e(q.id)}"></a><p class="source-caption">Question region · click to inspect at full size</p>`:'<div class="source-empty">Select the answer region on the page below. A missing crop is not a blank answer.</div>'}${original?`<a class="small" href="${e(original)}" target="_blank" rel="noopener">Open original submission</a>`:""}${state.assessment.status==="approved"?'<div class="source-actions"><button id="review-region-detection" class="secondary">Detect / review answer regions</button></div>':''}${cropToolsHTML(r,sub,approved)}`;
  const suggestionHTML=suggestion?`<div class="suggestion-card"><div class="suggestion-title"><div>Original suggestion<div class="method">${e(suggestion.method==="deterministic"?"Answer-key matching":suggestion.method==="blank"?"Confirmed blank":"Local model · provisional")}</div></div><span class="suggestion-points">${fmt(suggestion.suggested_points)}<small> / ${fmt(q.max_points)}</small></span></div><p>${e(suggestion.feedback)}</p>${(suggestion.criteria || []).map(c=>`<div class="criterion"><div class="criterion-title"><span>${e(q.rubric.find(v=>v.id===c.criterion_id)?.description || c.criterion_id)}</span><span>${fmt(c.points)} pt</span></div>${c.evidence?`<blockquote>${e(c.evidence)}</blockquote>`:'<p class="muted">No quoted evidence.</p>'}${c.feedback?`<p>${e(c.feedback)}</p>`:""}</div>`).join("")}</div>`:'<div class="source-empty" style="margin:16px 0">No score suggestion yet. Read or enter the transcription, then draft a score.</div>';
  $("#response-review").innerHTML=`<div class="response-heading"><div><h2>${e(r.student_id)} <span class="muted">/ Q${e(q.id)}</span></h2><p>${e(q.type==="choice"?"Multiple choice":q.type==="exact"?"Exact / accepted answer":"Rubric interpretation")} · ${fmt(q.max_points)} points</p></div><div class="response-nav"><button class="icon-button" id="previous-answer" title="Previous student (K)" aria-label="Previous student">‹</button><button class="icon-button" id="next-answer" title="Next student (J)" aria-label="Next student">›</button>${badge(r.state)}</div></div><div class="question-context"><p>${e(q.prompt)}</p><details><summary>Key & scoring rules</summary><div class="key-answer">${e(q.type==="rubric"?q.rubric.map(c=>`${c.id}: ${c.description} (${c.max_points} pt)`).join("\n"):`Accepted: ${q.accepted_answers.join(" · ")}`)}</div><p class="small muted" style="margin:8px 0 0">${e(normalizationSummary(q))}</p>${q.notes?`<p class="small" style="white-space:pre-wrap;margin:8px 0 0">${e(q.notes)}</p>`:""}</details></div><div class="answer-columns"><div class="source-column"><div class="section-kicker"><span>Original answer</span><span>${r.source_kind==="typed"?"Typed":"Scanned"}</span></div>${sourceHTML}</div><div class="grading-column"><div class="section-kicker"><span>Transcription</span><span id="unsaved-indicator" class="unsaved"></span></div><label for="transcription"><span class="muted small">${e(transLabel)}</span><textarea id="transcription" rows="4" spellcheck="false" autocomplete="off" ${disabled} placeholder="Preserve the student's spelling, accents, and verb endings exactly.">${e(r.transcription ?? "")}</textarea></label><div class="transcription-actions"><label class="checkbox"><input id="transcription-confirmed" type="checkbox" ${r.transcription_confirmed?"checked":""} ${disabled}>I checked this text against the original${r.transcription===""?" (truly blank)":""}.</label><button id="save-transcription" class="secondary" ${disabled}>Save text</button></div>${r.source_kind==="scan"&&!approved?'<div class="source-actions"><button id="read-current" class="secondary">Read this crop</button></div>':""}${r.error?`<div class="error-box"><strong>Processing did not finish</strong>${e(r.error)}<p style="margin:6px 0 0">This is unresolved. No failure is treated as a zero.</p></div>`:""}${flags.length?`<div class="review-flags"><strong>Check before approving</strong><ul>${[...new Set(flags)].map(f=>`<li>${e(f)}</li>`).join("")}</ul></div>`:""}${suggestionHTML}<div class="section-kicker"><span>Your reviewed result</span>${!approved?'<button id="grade-current" class="quiet" style="font-size:11px;text-transform:none;letter-spacing:0">Draft this score</button>':""}</div><div class="score-fields"><label for="score">Points / ${fmt(q.max_points)}<input id="score" type="number" min="0" max="${q.max_points}" step="any" value="${r.points===null || r.points===undefined?"":e(r.points)}" ${disabled}></label><label for="feedback">Brief feedback<textarea id="feedback" rows="3" ${disabled}>${e(r.feedback || "")}</textarea></label></div><details class="review-note"><summary class="small muted">Teacher review note</summary><textarea id="review-note" rows="2" ${disabled} placeholder="Optional explanation for an adjustment."></textarea></details><div class="approve-actions">${approved?'<button id="reopen-response" class="secondary">Reopen for correction</button>':`<button id="save-score" class="secondary">Save result</button><button id="approve-response" class="${state.reviewMode==="quiz"?"secondary":"primary"}">Approve answer</button>${state.reviewMode==="quiz"?'<button id="approve-next" class="primary">Approve & next</button>':""}`}</div><p class="small muted" style="margin:10px 0 0">${approved?`Approved ${e(dateText(r.approved_at))}`:"Only your approval makes this result final."}</p></div></div><div class="response-footer"><span>${e(transLabel)}</span><span>·</span><span>Revision ${r.revision}</span><details><summary>Suggestion & edit history (${(r.history || []).length})</summary><div class="history-list">${historyHTML(r.history || [])}</div></details></div>`;
  $("#previous-answer").addEventListener("click",()=>navigateStudent(-1)); $("#next-answer").addEventListener("click",()=>navigateStudent(1));
  $("#transcription").addEventListener("input",()=>markDirty("transcription")); $("#transcription-confirmed").addEventListener("change",()=>markDirty("transcription"));
  ["#score","#feedback","#review-note"].forEach(sel=>$(sel).addEventListener("input",()=>markDirty("score")));
  $("#save-transcription").addEventListener("click",event=>run(event.currentTarget,()=>saveTranscription(),"Saving…"));
  $("#save-score")?.addEventListener("click",event=>run(event.currentTarget,()=>saveScore(),"Saving…"));
  $("#approve-next")?.addEventListener("click",event=>run(event.currentTarget,()=>approveAndNext(),"Approving…"));
  $("#approve-response")?.addEventListener("click",event=>run(event.currentTarget,()=>approveCurrent(),"Approving…"));
  $("#reopen-response")?.addEventListener("click",event=>run(event.currentTarget,async()=>{const next=await api(`/responses/${endpoint(r.id)}/reopen`,{method:"POST",body:{revision:state.current.revision}});await acceptResponse(next);toast("Answer reopened. Previous approval is preserved in history.");},"Reopening…"));
  $("#read-current")?.addEventListener("click",event=>run(event.currentTarget,()=>startJob("transcribe",[r.id]),"Starting…"));
  $("#grade-current")?.addEventListener("click",event=>run(event.currentTarget,()=>startJob("grade",[r.id]),"Starting…"));
  $("#review-region-detection")?.addEventListener("click",event=>run(event.currentTarget,()=>openRegionDetection(r.submission_id)));
  if(r.source_kind==="scan") setupCrop(r,sub,approved);
}
function historyHTML(history) {
  return history.length?[...history].reverse().map(item=>`<div class="history-item"><strong>${e(item.kind)} <span class="muted">· ${e(dateText(item.created_at))}</span></strong><pre>${e(JSON.stringify(item.details,null,2))}</pre></div>`).join(""):'<p class="muted">No history entries yet.</p>';
}
function normalizationSummary(q) {
  const n=q.normalization || {};
  return `${n.accent_policy==="ignore_acute"?"Acute accents ignored for this question only; ñ and ü remain distinct.":"Spanish accents are significant."} ${n.case_sensitive?"Case sensitive.":"Case ignored."}${n.ignore_terminal_punctuation?" Final punctuation ignored.":""}`;
}
async function acceptResponse(r, {message}={}) {
  if(r.assessment_id!==state.assessment?.id)return;
  const stillSelected=state.selectedId===r.id;
  if(stillSelected){state.current=r;state.transcriptionDirty=false;state.scoreDirty=false;}
  const i=state.responses.findIndex(v=>v.id===r.id); if(i>=0) state.responses[i]=r;
  renderQueue(); if(stillSelected)renderResponse();
  const assessmentId=state.assessment.id;
  const a=await api(`/assessments/${endpoint(assessmentId)}`); if(state.assessment?.id!==assessmentId)return; state.assessment=a; renderHeader();
  const ai=state.assessments.findIndex(v=>v.id===a.id); if(ai>=0) state.assessments[ai]=a; renderAssessmentList();
  if(message) toast(message);
}
async function saveTranscription() {
  const r=state.current; if(!r || r.state==="approved") return;
  const text=$("#transcription").value, confirmed=$("#transcription-confirmed").checked;
  if(!text.trim() && !confirmed) throw new Error("Check the original and confirm that this is a truly blank answer. Leave failed extraction unresolved until it is checked.");
  const next=await api(`/responses/${endpoint(r.id)}`,{method:"PATCH",body:{revision:r.revision,transcription:text,transcription_confirmed:confirmed}});
  await acceptResponse(next,{message:"Transcription saved. Any previous score must be reviewed again."});
}
async function saveScore() {
  const r=state.current; if(!r || r.state==="approved") return;
  if(state.transcriptionDirty) throw new Error("Save the transcription first. Changing the text invalidates the current score.");
  const points=$("#score").valueAsNumber,q=questionFor(r);
  if(!Number.isFinite(points) || points<0 || points>q.max_points) throw new Error(`Enter a score between 0 and ${q.max_points}. An empty score is unresolved.`);
  const next=await api(`/responses/${endpoint(r.id)}`,{method:"PATCH",body:{revision:r.revision,points,feedback:$("#feedback").value,review_note:$("#review-note").value}});
  await acceptResponse(next,{message:"Reviewed result saved. Approval is still required."});
}
async function approveCurrent() {
  if(!state.current || state.current.state==="approved") return;
  if(String(state.current.question_id)!==String(state.questionId)){toast("Wait for the selected question to finish loading before approving.");return;}
  const targetId=state.current.id,targetSequence=state.detailSequence,targetAssessment=state.assessment.id,targetQuestion=state.questionId;
  if(state.transcriptionDirty) {await saveTranscription();toast("Text saved. Review or enter a fresh score before approving.");return;}
  if(state.scoreDirty) await saveScore();
  if(state.current?.id!==targetId || state.selectedId!==targetId || state.detailSequence!==targetSequence || state.assessment?.id!==targetAssessment || String(state.questionId)!==String(targetQuestion)){toast("Result saved. Selection changed, so no answer was approved.");return;}
  const r=state.current;
  const next=await api(`/responses/${endpoint(r.id)}/approve`,{method:"POST",body:{revision:r.revision}});
  await acceptResponse(next,{message:`${r.student_id}, Q${r.question_id}: approved.`});
  return next;
}
async function approveAndNext() {
  const original=state.current;if(!original || state.reviewMode!=="quiz")return;
  const sequence=state.detailSequence,assessmentId=state.assessment.id,questionId=state.questionId;
  const approved=await approveCurrent();
  if(!approved || approved.state!=="approved" || state.reviewMode!=="quiz" || state.current?.id!==original.id || state.quizSubmissionId!==original.submission_id || state.detailSequence!==sequence || state.assessment?.id!==assessmentId || String(state.questionId)!==String(questionId))return;
  await navigateQuestion(1);
}
async function navigateStudent(delta) {
  const rows=visibleResponses(); if(!rows.length) return;
  const i=rows.findIndex(r=>r.id===state.selectedId), target=Math.max(0,Math.min(rows.length-1,i+delta));
  if(rows[target].id!==state.selectedId) try{await selectResponse(rows[target].id);}catch(error){toast(error.message,true);}
}
async function navigateQuestion(delta) {
  if(!state.assessment)return;
  const questions=state.assessment.questions,i=questions.findIndex(q=>String(q.id)===String(state.questionId)),next=questions[Math.max(0,Math.min(questions.length-1,i+delta))];
  if(next)await selectQuestion(next.id);
}

function regionSessionActive(session) {
  return state.regionEditor?.session===session && state.regionSession===session && $("#regions-dialog").open && state.regionEditor.assessmentId===state.assessment?.id;
}
function leaveRegionEdits() {
  return !state.regionEditor?.dirty || window.confirm("Discard the unsaved box and selection changes?");
}
function regionError(message) {
  $("#regions-error").textContent=message || "";$("#regions-error").classList.toggle("hidden",!message);
}
function validRegionBox(box) {
  return box && [box.x,box.y,box.width,box.height,box.page].every(Number.isFinite) && Number.isInteger(box.page) && box.page>=0 && box.x>=0 && box.y>=0 && box.width>0 && box.height>0 && box.x+box.width<=1.000001 && box.y+box.height<=1.000001;
}
function regionFlagText(flag) {
  const labels={teacher_review_required:"Check the question number and full answer before saving.",printed_prompt_match_limited:"The printed prompt was only partly matched. Confirm this is the right question.",number_punctuation_unclear:"The question number or its punctuation is unclear. Confirm the number.",multiline_printed_prompt:"The printed prompt spans several lines. Check where the student's answer begins.",check_column_boundary:"Check that the box stays within this question's column.",multiple_answer_spaces:"This question may have several answer spaces. Include every part of the response.",contains_printed_prompt:"The box includes printed question text. Keep the student's answer visible.",choice_marks_require_review:"Inspect the selected choice and any crossed-out or changed marks.",answer_extent_uncertain:"The end of the response is uncertain. Check that the whole answer is inside the box.",adjacent_printed_text:"Nearby printed text may belong to another question. Check the boundaries.",ink_near_crop_edge:"Ink is close to an edge. Enlarge the box if part of the response is cut off."};
  return labels[flag] || String(flag).replace(/_/g," ");
}
async function openRegionDetection(submissionId) {
  if(state.assessment?.status!=="approved")throw new Error("Approve the answer key before detecting answer regions.");
  if(state.transcriptionDirty || state.scoreDirty)throw new Error("Save your current answer edits before reviewing page regions.");
  const submission=state.submissions.find(item=>item.id===submissionId);
  if(!submission || submission.source_kind!=="scan")throw new Error("Choose a scanned submission to detect regions.");
  const session=++state.regionSession;
  state.regionEditor={session,submissionId,assessmentId:state.assessment.id,record:null,boxes:{},included:new Set(),questionId:state.current?.submission_id===submissionId?state.current.question_id:null,page:submission.pages[0]?.index ?? 0,dirty:false,drawing:false,working:true};
  $("#regions-title").textContent=`Review answer regions · ${submission.student_id}`;
  const original=safeURL(submission.original_url);$("#regions-original").href=original || "#";$("#regions-original").classList.toggle("hidden",!original);
  $("#regions-editor").classList.add("hidden");$("#regions-warnings").classList.add("hidden");$("#regions-loading").textContent="Loading saved region suggestions…";regionError("");$("#detect-regions-run").disabled=true;$("#regions-dialog").showModal();
  try {
    const record=await api(`/submissions/${endpoint(submissionId)}/region-detections/latest`);
    if(!regionSessionActive(session))return;
    state.regionEditor.working=false;
    if(record)installRegionRecord(record);else await detectRegions();
  } catch(error){if(regionSessionActive(session)){state.regionEditor.working=false;$("#regions-loading").textContent="";$("#detect-regions-run").disabled=false;regionError(error.message);}}
}
function installRegionRecord(record) {
  const editor=state.regionEditor;if(!editor)return;
  editor.record=record;editor.boxes={};editor.included=new Set();editor.dirty=false;editor.drawing=false;editor.working=false;
  for(const proposal of record.proposals || [])if(validRegionBox(proposal))editor.boxes[proposal.question_id]={...proposal};
  const questions=record.questions || [];
  if(!questions.some(q=>q.question_id===editor.questionId))editor.questionId=questions.find(q=>q.can_apply)?.question_id || questions[0]?.question_id || null;
  editor.page=editor.boxes[editor.questionId]?.page ?? record.pages[0]?.index ?? 0;
  $("#regions-confirm").checked=false;$("#regions-loading").textContent="";
  regionError(record.status==="failed"?`${record.error || "Automatic detection failed."} No answer mappings were changed. Retry detection or use manual mapping instead.`:"");
  renderRegionEditor();
}
async function detectRegions() {
  const editor=state.regionEditor;if(!editor || editor.working)return;
  if(editor.dirty && !window.confirm("Discard unsaved box and selection changes and run detection again?"))return;
  const session=editor.session;editor.working=true;editor.drawing=false;
  $("#detect-regions-run").disabled=true;$("#regions-loading").textContent="Detecting numbered answer regions locally…";regionError("");
  if(editor.record)renderRegionEditor();
  try {
    const record=await api(`/submissions/${endpoint(editor.submissionId)}/region-detections`,{method:"POST"});
    if(regionSessionActive(session))installRegionRecord(record);
  } catch(error){if(regionSessionActive(session)){editor.working=false;$("#regions-loading").textContent="";regionError(error.message);if(editor.record)renderRegionEditor();else $("#detect-regions-run").disabled=false;}}
}
function regionMetadata(id) { return state.regionEditor?.record?.questions?.find(question=>question.question_id===id); }
function invalidateRegionConfirmation() {
  const editor=state.regionEditor;if(editor)editor.dirty=true;$("#regions-confirm").checked=false;updateRegionSaveControls();
}
function updateRegionSaveControls() {
  const editor=state.regionEditor,ready=editor?.record?.status==="ready" && !editor.working;
  const count=editor?[...editor.included].filter(id=>regionMetadata(id)?.can_apply && validRegionBox(editor.boxes[id])).length:0;
  $("#regions-selection-count").textContent=`${count} question ${count===1?"mapping":"mappings"} selected. Saved transcriptions, scores, and existing mappings are protected.`;
  $("#regions-confirm").disabled=!ready || !count;$("#regions-apply").disabled=!ready || !count || !$("#regions-confirm").checked;
}
function selectRegionQuestion(id,followPage=true) {
  const editor=state.regionEditor;if(!editor)return;
  editor.questionId=id;editor.drawing=false;
  if(followPage && editor.boxes[id])editor.page=editor.boxes[id].page;
  renderRegionEditor();
}
function renderRegionEditor() {
  const editor=state.regionEditor,record=editor?.record;if(!record)return;
  const ready=record.status==="ready" && !editor.working,questions=record.questions || [],pages=record.pages || [];
  $("#regions-editor").classList.remove("hidden");$("#detect-regions-run").disabled=editor.working;$("#detect-regions-run").textContent="Run detection again";
  const warnings=[...(record.warnings || [])];if(record.missing_question_ids?.length)warnings.push(`No automatic box for ${record.missing_question_ids.map(id=>`Q${id}`).join(", ")}. ${record.status==="failed"?"Retry detection or use the individual question mapper.":"Draw these manually or use the individual question mapper."}`);
  $("#regions-warnings").classList.toggle("hidden",!warnings.length);$("#regions-warnings").innerHTML=warnings.length?`<strong>Inspect the suggestions</strong><ul>${warnings.map(message=>`<li>${e(message)}</li>`).join("")}</ul>`:"";
  $("#regions-question").innerHTML=questions.map(q=>`<option value="${e(q.question_id)}" ${q.question_id===editor.questionId?"selected":""}>Q${e(q.question_id)}${q.can_apply?"":" · protected"}</option>`).join("");
  $("#regions-page").innerHTML=pages.map((page,index)=>`<option value="${page.index}" ${page.index===editor.page?"selected":""}>Page ${index+1}</option>`).join("");
  $("#regions-question").disabled=editor.working;$("#regions-page").disabled=editor.working;
  const page=pages.find(item=>item.index===editor.page),image=$("#regions-page-image"),url=safeURL(page?.url);
  if(url){if(image.getAttribute("src")!==url)image.src=url;}else image.removeAttribute("src");
  const metadata=regionMetadata(editor.questionId);$("#regions-draw").disabled=!ready || !metadata?.can_apply || !url;$("#regions-draw").setAttribute("aria-pressed",String(editor.drawing));
  $("#regions-stage").classList.toggle("drawing",editor.drawing);$("#regions-draw-help").textContent=editor.drawing?`Drag a new rectangle for Q${editor.questionId} on this page. Keep the full response and crossed-out work visible.`:metadata&&!metadata.can_apply?metadata.blocked_reason || "This answer is protected. Existing work cannot be replaced here.":"Click a box to inspect its question. Use Draw / redraw to change boundaries or map a missing question.";
  $("#regions-draft-box").classList.add("hidden");
  $("#regions-overlays").innerHTML=questions.filter(q=>validRegionBox(editor.boxes[q.question_id]) && editor.boxes[q.question_id].page===editor.page).map(q=>{const box=editor.boxes[q.question_id];return `<button type="button" class="region-proposal ${editor.questionId===q.question_id?"active":""} ${editor.included.has(q.question_id)?"included":""} ${q.can_apply?"":"protected"}" data-region-question="${e(q.question_id)}" style="left:${box.x*100}%;top:${box.y*100}%;width:${box.width*100}%;height:${box.height*100}%" aria-label="Inspect suggested region for question ${e(q.question_id)}"><span>Q${e(q.question_id)}${editor.included.has(q.question_id)?" ✓":""}</span></button>`;}).join("");
  $("#regions-list").innerHTML=questions.map(q=>{const box=editor.boxes[q.question_id],known=questionFor({question_id:q.question_id}),available=ready&&q.can_apply&&validRegionBox(box);return `<article class="region-question-row ${editor.questionId===q.question_id?"active":""}"><div><label class="checkbox"><input type="checkbox" data-region-include="${e(q.question_id)}" ${editor.included.has(q.question_id)?"checked":""} ${available?"":"disabled"}><span class="sr-only">Include Q${e(q.question_id)} in saved mappings</span></label><button type="button" class="quiet" data-region-select="${e(q.question_id)}"><strong>Q${e(q.question_id)}</strong>${validRegionBox(box)?` · page ${pages.findIndex(page=>page.index===box.page)+1}`:" · no box"}</button></div><p>${e(known?.prompt?.slice(0,140) || "")}</p>${!q.can_apply?`<p class="region-blocked">${e(q.blocked_reason || "Existing answer protected")}</p>`:!box?'<p class="muted">Select this question and draw its answer region.</p>':`<p class="muted">${e(box.manual?"Teacher-adjusted box":box.reason || "Automatic proposal — inspect before saving.")}</p>${box.flags?.length?`<ul>${box.flags.map(flag=>`<li>${e(regionFlagText(flag))}</li>`).join("")}</ul>`:""}`}</article>`;}).join("");
  $$("[data-region-question]").forEach(button=>button.addEventListener("click",()=>selectRegionQuestion(button.dataset.regionQuestion,false)));
  $$("[data-region-select]").forEach(button=>button.addEventListener("click",()=>selectRegionQuestion(button.dataset.regionSelect)));
  $$("[data-region-include]").forEach(input=>input.addEventListener("change",()=>{if(input.checked)editor.included.add(input.dataset.regionInclude);else editor.included.delete(input.dataset.regionInclude);invalidateRegionConfirmation();renderRegionEditor();}));
  $("#regions-select-all").disabled=!ready;updateRegionSaveControls();
}
function setupRegionDrawing() {
  const stage=$("#regions-stage"),draft=$("#regions-draft-box");let start=null;
  const position=event=>{const box=stage.getBoundingClientRect();return{x:Math.min(1,Math.max(0,(event.clientX-box.left)/box.width)),y:Math.min(1,Math.max(0,(event.clientY-box.top)/box.height))};};
  const preview=point=>({x:Math.min(start.x,point.x),y:Math.min(start.y,point.y),width:Math.abs(point.x-start.x),height:Math.abs(point.y-start.y)});
  stage.addEventListener("pointerdown",event=>{const editor=state.regionEditor,image=$("#regions-page-image");if(!editor?.drawing || editor.working || !regionMetadata(editor.questionId)?.can_apply || !image.complete || !image.naturalWidth)return;event.preventDefault();start=position(event);stage.setPointerCapture(event.pointerId);});
  stage.addEventListener("pointermove",event=>{if(!start)return;const box=preview(position(event));draft.classList.remove("hidden");Object.assign(draft.style,{left:`${box.x*100}%`,top:`${box.y*100}%`,width:`${box.width*100}%`,height:`${box.height*100}%`});});
  stage.addEventListener("pointerup",event=>{if(!start)return;const editor=state.regionEditor,box=preview(position(event));start=null;draft.classList.add("hidden");if(!editor)return;if(box.width<.005 || box.height<.005){regionError("Draw a larger rectangle around the full answer.");return;}editor.boxes[editor.questionId]={question_id:editor.questionId,page:editor.page,...box,manual:true,flags:[]};editor.drawing=false;regionError("");invalidateRegionConfirmation();renderRegionEditor();});
  stage.addEventListener("pointercancel",()=>{start=null;draft.classList.add("hidden");});
}
async function applyRegions() {
  const editor=state.regionEditor,record=editor?.record;
  if(!record || record.status!=="ready" || !$("#regions-confirm").checked)throw new Error("Inspect the selected mappings and confirm them before saving.");
  const regions=[...editor.included].map(id=>{const q=regionMetadata(id),box=editor.boxes[id];if(!q?.can_apply || !validRegionBox(box))throw new Error(`Q${id} cannot be applied. Refresh the suggestions or map it manually.`);return{question_id:id,revision:q.revision,page:box.page,x:box.x,y:box.y,width:box.width,height:box.height};});
  if(!regions.length)throw new Error("Select at least one inspected box to save.");
  const session=editor.session;editor.working=true;renderRegionEditor();regionError("");
  try {
    const result=await api(`/region-detections/${endpoint(record.id)}/apply`,{method:"POST",body:{confirmed:true,regions}});
    if(!regionSessionActive(session))return;
    editor.dirty=false;$("#regions-dialog").close();
    if(state.assessment?.id===editor.assessmentId)await refreshAssessment();
    toast(`${result.applied} inspected answer mappings saved. Transcription and grading remain separate steps.`);
  } catch(error){if(regionSessionActive(session)){editor.working=false;$("#regions-confirm").checked=false;renderRegionEditor();regionError(error.message);}throw error;}
}

function cropToolsHTML(r,sub,approved) {
  const pages=sub?.pages || [];
  if(!pages.length) return '<p class="small muted">Rendered pages are unavailable.</p>';
  return `<details class="crop-tools" ${!r.region?"open":""}><summary>${r.region?"Inspect / change question region":"Map this question on the page"}</summary><label>Page<select id="crop-page">${pages.map((p,index)=>`<option value="${p.index}" ${Number(p.index)===Number(r.region?.page ?? pages[0].index)?"selected":""}>Page ${index+1}</option>`).join("")}</select></label><div id="crop-stage" class="crop-stage"><img id="crop-page-image" alt="Submission page for manual answer selection" draggable="false"><div id="crop-rect" class="crop-rect hidden"></div></div><p class="crop-instruction">${approved?"The saved region is shown. Reopen the answer to change it.":"Drag a rectangle around this question’s answer only. Keep crossed-out or overwritten work visible."}</p><div id="crop-coordinates" class="crop-coordinates"></div>${approved?"":'<button id="save-region" class="secondary">Save question region</button>'}</details>`;
}
function setupCrop(r,sub,approved) {
  const stage=$("#crop-stage"), pageSelect=$("#crop-page"), img=$("#crop-page-image"), rect=$("#crop-rect"); if(!stage) return;
  const pages=sub.pages;
  const show=()=>{
    const page=pages.find(p=>Number(p.index)===Number(pageSelect.value)); if(!page)return;
    img.src=safeURL(page.url);
    state.crop=r.region && Number(r.region.page)===Number(page.index)?{...r.region}:null;
    draw();
  };
  const draw=()=>{
    const crop=state.crop;
    rect.classList.toggle("hidden",!crop);
    if(crop){Object.assign(rect.style,{left:`${crop.x*100}%`,top:`${crop.y*100}%`,width:`${crop.width*100}%`,height:`${crop.height*100}%`});$("#crop-coordinates").textContent=`Region: ${Math.round(crop.width*100)}% of page width × ${Math.round(crop.height*100)}% of page height`;}
    else $("#crop-coordinates").textContent="No region selected for this page.";
  };
  pageSelect.addEventListener("change",show); show();
  if(approved)return;
  let start=null;
  const position=event=>{const box=stage.getBoundingClientRect();return{x:Math.min(1,Math.max(0,(event.clientX-box.left)/box.width)),y:Math.min(1,Math.max(0,(event.clientY-box.top)/box.height))};};
  stage.addEventListener("pointerdown",event=>{if(!img.complete || !img.naturalWidth)return;event.preventDefault();stage.setPointerCapture(event.pointerId);start=position(event);state.crop={page:Number(pageSelect.value),x:start.x,y:start.y,width:0,height:0};draw();});
  stage.addEventListener("pointermove",event=>{if(!start)return;const p=position(event);state.crop={page:Number(pageSelect.value),x:Math.min(start.x,p.x),y:Math.min(start.y,p.y),width:Math.abs(p.x-start.x),height:Math.abs(p.y-start.y)};draw();});
  stage.addEventListener("pointerup",()=>{start=null;}); stage.addEventListener("pointercancel",()=>{start=null;});
  $("#save-region").addEventListener("click",event=>run(event.currentTarget,async()=>{
    if(!state.crop || state.crop.width<.005 || state.crop.height<.005) throw new Error("Drag a rectangle around the answer before saving.");
    if((state.transcriptionDirty || state.scoreDirty) && !leaveEdits())return;
    const next=await api(`/responses/${endpoint(r.id)}/region`,{method:"PUT",body:{revision:state.current.revision,...state.crop}});
    await acceptResponse(next,{message:"Question region saved. Its transcription and score need to be generated again."});
  },"Saving…"));
}

async function startJob(action,ids) {
  if(!state.assessment || state.assessment.status!=="approved") throw new Error("Review and approve the answer key before processing responses.");
  if(state.transcriptionDirty || state.scoreDirty) throw new Error("Save your current edits before starting processing.");
  const assessmentId=state.assessment.id;
  const result=await api(`/assessments/${endpoint(assessmentId)}/jobs`,{method:"POST",body:{action,...(ids?{response_ids:ids}:{})}});
  if(state.assessment?.id!==assessmentId)return;
  state.activeJob=result; renderHeader(); await pollJob(result.id);
}
async function pollJob(id) {
  clearTimeout(state.polling);
  try {
    const job=await api(`/jobs/${endpoint(id)}`); if(!state.auth || (job.assessment_id && job.assessment_id!==state.assessment?.id))return;
    state.activeJob=job;
    const done=["completed","failed","interrupted"].includes(job.status), node=$("#job-status");
    node.classList.remove("hidden"); node.classList.toggle("error",job.status==="failed"||job.status==="interrupted"||Number(job.errors)>0);
    node.innerHTML=`<span><strong>${e(job.action==="transcribe"?"Reading answer crops":"Drafting scores")}</strong> · ${e(job.status)} · ${job.done || 0} / ${job.total || 0}${job.errors?` · ${e(job.errors)} need attention`:""}${job.error?`<br>${e(job.error)}`:""}</span><div class="job-progress"><span style="width:${job.total?Math.min(100,100*job.done/job.total):0}%"></span></div>`;
    if(done) {state.activeJob=null;await refreshAssessment({refreshCurrent:!state.transcriptionDirty&&!state.scoreDirty});if(state.transcriptionDirty||state.scoreDirty)toast("Processing finished. Save your current edits, then select the answer again to see any new draft.");renderHeader();}
    else state.polling=setTimeout(()=>pollJob(id),1200);
  } catch(error) { state.activeJob=null; renderHeader(); $("#job-status").classList.remove("hidden"); $("#job-status").classList.add("error"); $("#job-status").textContent=`Status check failed: ${error.message} Reload the assessment to reconnect; no score is approved automatically.`; }
}

function renderIntake() {
  const a=state.assessment;if(!a)return;
  $("#intake-content").innerHTML=`<div class="intake-grid"><div><section class="panel"><h3>Type or paste a student’s answers</h3><p>Enter exactly what the student wrote. An empty captured answer is a true blank; turn off “captured” when an answer is missing from the import.</p><form id="typed-form" class="form-stack"><label>Student identifier<input name="student_id" required maxlength="80" placeholder="Student 001" autocomplete="off"></label>${a.questions.map(q=>`<div class="typed-answer"><label>Q${e(q.id)} · ${e(q.prompt)}<textarea data-answer="${e(q.id)}" rows="2" spellcheck="false"></textarea></label><label class="checkbox"><input type="checkbox" data-captured="${e(q.id)}" checked>Answer captured (empty means truly blank)</label></div>`).join("")}<button class="primary" type="submit">Add typed submission</button><p class="inline-error" id="typed-error"></p></form></section></div><div><section class="panel"><h3>Upload one student’s paper</h3><p>One PDF or image per student. Review detected question regions, or draw each answer region manually.</p><form id="scan-form" class="form-stack"><label>Student identifier<input name="student_id" required maxlength="80" placeholder="Student 002" autocomplete="off"></label><label>Submission file<input name="file" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" required></label><button class="primary" type="submit">Upload submission</button><p class="inline-error" id="scan-error"></p></form></section><section class="panel"><h3>Import a small typed batch</h3><p>CSV columns: <code>student_id,${a.questions.map(q=>`q${e(q.id)}`).join(",")}</code>. UTF-8 preserves Spanish accents. Missing question columns remain unresolved.</p><form id="csv-form" class="form-stack"><label>Response CSV<input name="file" type="file" accept=".csv,text/csv" required></label><button class="secondary" type="submit">Import typed CSV</button><p class="inline-error" id="csv-error"></p></form></section><section class="panel"><h3>Imported submissions <span class="muted">(${state.submissions.length})</span></h3><div class="intake-status">${state.submissions.length?state.submissions.map(s=>`<div class="submission-row"><span><strong>${e(s.student_id)}</strong><br><span class="muted">${s.source_kind==="scan"?`${(s.pages || []).length} page(s)` :"Typed answers"}</span></span><div class="button-row">${s.source_kind==="scan"?`<button class="quiet" data-detect-submission="${e(s.id)}" ${a.status!=="approved"?"disabled":""}>Detect regions</button>`:""}<button class="quiet" data-review-submission="${e(s.id)}">Review</button></div></div>`).join(""):'<p>No submissions yet.</p>'}</div></section></div></div>`;
  $("#typed-form").addEventListener("submit",event=>{event.preventDefault();const form=event.currentTarget;run($("button[type=submit]",form),async()=>{
    $("#typed-error").textContent=""; const answers={};
    for(const field of $$("[data-answer]",form)){const captured=$$("[data-captured]",form).find(n=>n.dataset.captured===field.dataset.answer);if(captured.checked)answers[field.dataset.answer]=field.value;}
    const sub=await api(`/assessments/${endpoint(a.id)}/submissions/typed`,{method:"POST",body:{student_id:form.elements.student_id.value,answers}});
    await refreshAssessment({refreshCurrent:false});renderIntake();toast("Typed submission added. Original text has been preserved.");await reviewSubmission(sub.id);
  },"Adding…");});
  $("#scan-form").addEventListener("submit",event=>{event.preventDefault();const form=event.currentTarget;run($("button[type=submit]",form),async()=>{
    const sub=await api(`/assessments/${endpoint(a.id)}/submissions/scan`,{method:"POST",form:new FormData(form)}); await refreshAssessment({refreshCurrent:false});renderIntake();toast("Submission added. Map the answer regions to their question numbers.");await reviewSubmission(sub.id);
  },"Rendering pages…");});
  $("#csv-form").addEventListener("submit",event=>{event.preventDefault();const form=event.currentTarget;run($("button[type=submit]",form),async()=>{
    const result=await api(`/assessments/${endpoint(a.id)}/submissions/csv`,{method:"POST",form:new FormData(form)});await refreshAssessment();renderIntake();toast(`${result.imported} typed submissions imported.`);setTab("review");
  },"Importing…");});
  $$("[data-detect-submission]").forEach(button=>button.addEventListener("click",()=>openRegionDetection(button.dataset.detectSubmission).catch(error=>toast(error.message,true))));
  $$("[data-review-submission]").forEach(button=>button.addEventListener("click",()=>reviewSubmission(button.dataset.reviewSubmission).catch(error=>toast(error.message,true))));
}
async function reviewSubmission(id) {
  if(!leaveEdits())return;
  const r=state.responses.find(v=>v.submission_id===id && String(v.question_id)===String(state.questionId)) || state.responses.find(v=>v.submission_id===id);
  if(!r)return;
  state.questionId=r.question_id;state.filter="all";$("#state-filter").value="all";renderQuestionSelect();setTab("review");await selectResponse(r.id,true);
}

function renderKey() {
  const a=state.assessment;if(!a)return;
  $("#key-content").innerHTML=`<section class="panel"><div class="section-heading"><div><h3>${a.status==="approved"?"Approved answer key":"Draft answer key"}</h3><p style="margin:6px 0 0">${a.status==="approved"?"These scoring rules are fixed for this assessment.":"Review every question before enabling grading."}</p></div><div class="button-row">${a.status!=="approved"?'<button id="edit-key" class="secondary">Edit key</button><button id="approve-key" class="primary">Approve key</button>':'<button id="duplicate-key" class="secondary">Copy to new assessment</button>'}</div></div>${a.key_source?`<div id="key-source-details" data-source-id="${e(a.key_source)}"><p class="small muted">Loading original source and review notes…</p></div>`:""}${a.instructions?`<p style="white-space:pre-wrap">${e(a.instructions)}</p>`:""}${a.questions.map(q=>`<article class="key-question"><div class="title"><span>Question ${e(q.id)} · ${e(q.type==="choice"?"Multiple choice":q.type==="exact"?"Accepted-answer matching":"Rubric interpretation")}</span><span>${fmt(q.max_points)} points</span></div><p class="prompt">${e(q.prompt)}</p>${q.type!=="rubric"?`<p><strong>Full credit:</strong> ${q.accepted_answers.map(v=>`<code>${e(v)}</code>`).join(" · ")}</p>${q.partial_answers?.length?`<ul>${q.partial_answers.map(p=>`<li>${fmt(p.points)} pt for <code>${e(p.answer)}</code> · ${e(p.feedback)}</li>`).join("")}</ul>`:""}`:`<ul>${q.rubric.map(c=>`<li><strong>${e(c.id)} · ${fmt(c.max_points)} pt:</strong> ${e(c.description)}</li>`).join("")}</ul>`}<div class="key-rules"><span>${e(normalizationSummary(q))}</span>${q.normalization?.trim?"<span>Trim outer whitespace</span>":""}${q.normalization?.collapse_whitespace?"<span>Collapse repeated whitespace</span>":""}</div>${q.notes?`<p class="notes"><strong>Question policy:</strong> ${e(q.notes)}</p>`:""}</article>`).join("")}</section>${scoringSummaryHTML(a)}<section class="panel subtle"><h3>Batch consistency</h3><p class="version-detail">Model: ${e(a.model)} · Context: ${e(a.num_ctx)} tokens<br>Rubric version: ${e(a.version)}<br>Prompt version: ${e(a.prompt_version)}<br>Model digest: ${e(a.model_digest || "Recorded when local inference first runs")}</p></section>`;
  $("#adjust-scoring")?.addEventListener("click",event=>run(event.currentTarget,()=>openScoring()));
  if(a.key_source)loadKeySource(a.key_source,$("#key-source-details"));
  $("#edit-key")?.addEventListener("click",()=>openEditor(a));
  $("#duplicate-key")?.addEventListener("click",()=>{openEditor({...a,title:`${a.title} — new version`},true);});
  $("#approve-key")?.addEventListener("click",()=>{$("#key-checked").checked=false;$("#confirm-key-approval").disabled=true;$("#approval-dialog").showModal();});
}

function scoringSummaryHTML(a) {
  const sections=a.scoring_sections || [], raw=a.raw_max_points ?? a.max_points;
  const adjusted=a.adjusted_max_points ?? raw;
  return `<section class="panel"><div class="section-heading"><div><h3>Section scoring</h3><p style="margin:6px 0 0">${sections.length?`${fmt(raw)} original points → ${fmt(adjusted)} adjusted points. Final exports include the original raw scores.`:"Original question point values are in use."}</p></div><button id="adjust-scoring" class="secondary" ${a.status!=="approved"?"disabled":""}>Adjust scoring</button></div>${a.status!=="approved"?'<p class="small muted">Approve the answer key before adjusting section totals.</p>':""}${sections.length?`<ul class="scoring-summary">${sections.map(section=>{const rawSection=a.questions.filter(q=>section.question_ids.includes(q.id)).reduce((total,q)=>total+q.max_points,0);return `<li><strong>${e(section.label)}</strong>: ${fmt(rawSection)} → ${fmt(section.target_points)} points <span class="muted">(Q${section.question_ids.map(e).join(", Q")})</span></li>`;}).join("")}</ul><p class="small muted">Answer review continues to use raw points. Questions outside a section retain their original weight.</p>`:""}</section>`;
}

function sourceDetailsHTML(details, fallbackId) {
  const generated=String(details.method || "").toLowerCase().includes("generat");
  const warnings=Array.isArray(details.warnings)?details.warnings:[];
  const reviews=Array.isArray(details.question_reviews)?details.question_reviews:[];
  const original=safeURL(details.original_url) || `/api/key/sources/${endpoint(details.id || fallbackId)}`;
  const pages=Array.isArray(details.pages)?details.pages:[];
  return `<details class="key-source panel subtle" ${generated?"open":""}><summary>${generated?"Generated key · source and required review":"Original key · source and review notes"}</summary><p class="small"><a href="${e(original)}" target="_blank" rel="noopener">Open ${e(details.name || "original source")}</a></p>${generated?'<p class="generated-label">Answers and any proposed rubric were generated by a model. Check them against the assessment before approving the key.</p>':""}${warnings.length?`<ul class="source-warnings">${warnings.map(w=>`<li>${e(w)}</li>`).join("")}</ul>`:""}${reviews.length?`<div class="source-question-reviews">${reviews.map(review=>`<div><strong>Q${e(review.question_id)}${review.status==="incomplete"?" · incomplete":""}</strong>${review.points_source==="teacher_default"?'<span class="badge draft">Default points assumed</span>':""}${review.rubric_source==="proposed"?'<span class="badge draft">Proposed rubric</span>':""}${review.answer_source==="model_proposed"?'<span class="badge draft">Proposed answer</span>':""}${review.answer_source==="unresolved"?'<span class="badge needs_review">Answer unresolved</span>':""}${review.review_reasons?.length?`<ul>${review.review_reasons.map(reason=>`<li>${e(reason)}</li>`).join("")}</ul>`:""}</div>`).join("")}</div>`:""}${pages.length?`<div class="key-source-pages">${pages.filter(page=>safeURL(page.url)).map((page,index)=>`<a href="${e(safeURL(page.url))}" target="_blank" rel="noopener"><img src="${e(safeURL(page.url))}" alt="Original assessment source, page ${index+1}" loading="lazy"><span>Page ${index+1} · open full size</span></a>`).join("")}</div>`:'<p class="small muted">Open the original file to compare it with this key.</p>'}<p class="small muted">Source notes describe the initial extraction or generation. Your saved key may include later corrections.</p></details>`;
}
async function keySourceDetails(sourceId) {
  if(keySourceCache.has(sourceId))return keySourceCache.get(sourceId);
  const details=await api(`/key/sources/${endpoint(sourceId)}/details`);
  keySourceCache.set(sourceId,details);return details;
}
async function loadKeySource(sourceId,node) {
  if(!node)return;
  try {const details=await keySourceDetails(sourceId);if(node.isConnected)node.innerHTML=sourceDetailsHTML(details,sourceId);}
  catch(error){if(node.isConnected)node.innerHTML=`<p class="inline-error">Source preview could not be loaded: ${e(error.message)}</p><p class="small"><a href="/api/key/sources/${endpoint(sourceId)}" target="_blank" rel="noopener">Open original source</a></p>`;}
}
async function loadEditorSource(sourceId,session) {
  try {
    const details=await keySourceDetails(sourceId);
    if(session!==state.editorSession || state.keySource!==sourceId || !$("#editor-dialog").open)return;
    state.keyDetails=details;state.keyReviews=details.question_reviews || [];
    $("#editor-key-source").innerHTML=sourceDetailsHTML(details,sourceId);
    collectEditor();renderQuestionEditor();
  } catch(error) {
    if(session===state.editorSession && state.keySource===sourceId && $("#editor-dialog").open)$("#editor-key-source").innerHTML+=`<p class="inline-error">Source preview could not be loaded: ${e(error.message)}</p>`;
  }
}
function questionReviewHTML(id) {
  const review=state.keyReviews.find(item=>String(item.question_id)===String(id));if(!review)return "";
  const notes=[...(review.review_reasons || [])];
  if(review.points_source==="teacher_default")notes.push("The point value uses your default because the source did not specify it.");
  if(review.rubric_source==="proposed")notes.push("These rubric criteria are model proposals. Review each criterion and point value.");
  if(review.answer_source==="model_proposed")notes.push("The answer is a model proposal and needs your check.");
  if(review.status==="incomplete")notes.unshift("Complete the missing answer or rubric before saving this draft.");
  return notes.length?`<div class="review-flags"><strong>Review this generated question</strong><ul>${[...new Set(notes)].map(note=>`<li>${e(note)}</li>`).join("")}</ul></div>`:"";
}

function openEditor(assessment=null,copy=false) {
  if(!leaveEdits())return;
  ++state.editorSession;
  state.editorId=assessment&&!copy?assessment.id:null;
  state.editor=structuredClone(assessment || {title:"",course:"Spanish 2 CP",instructions:"",questions:[blankQuestion(1)],model:state.settings?.model || "qwen3-vl:4b-instruct-q4_K_M",num_ctx:state.settings?.num_ctx || 4096,synthetic:false});
  state.keySource=assessment?.key_source || null;state.extractionWarnings=[];state.keyReviews=[];state.keyDetails=null;
  $("#editor-title").textContent=state.editorId?"Edit draft answer key":"Create an assessment";
  $("#key-json").value="";$("#editor-error").textContent="";$("#extraction-notice").classList.add("hidden");
  $("#key-upload").value="";$("#assessment-key-upload").value="";$("#json-key-upload").value="";$("#generation-default-points").value="1";
  $("#editor-key-source").innerHTML="";
  fillEditor();$("#editor-dialog").showModal();
  if(state.keySource)loadEditorSource(state.keySource,state.editorSession);
}
function fillEditor() {
  const form=$("#assessment-form"),a=state.editor;
  for(const key of ["title","course","instructions","model","num_ctx"])form.elements[key].value=a[key]??(key==="num_ctx"?4096:"");
  if(!form.elements.course.value) { const opt=document.createElement("option");opt.value=a.course;opt.textContent=a.course;form.elements.course.append(opt);form.elements.course.value=a.course; }
  renderQuestionEditor();
}
function renderQuestionEditor() {
  $("#question-editor").innerHTML=state.editor.questions.map((q,i)=>{
    const n={...blankQuestion(1).normalization,...q.normalization};
    return `<section class="editor-question" data-q-index="${i}"><div class="q-heading"><strong>Question ${i+1}</strong><button type="button" class="quiet danger" data-remove-question="${i}">Remove</button></div><div class="q-main"><label>Number<input data-field="id" value="${e(q.id)}" required maxlength="32"></label><label>Scoring method<select data-field="type"><option value="choice" ${q.type==="choice"?"selected":""}>Multiple choice</option><option value="exact" ${q.type==="exact"?"selected":""}>Exact / accepted answers</option><option value="rubric" ${q.type==="rubric"?"selected":""}>Rubric · local model</option></select></label><label>Max points<input data-field="max_points" type="number" min="0.001" step="any" max="100" value="${e(q.max_points)}" required></label></div><div class="q-body">${questionReviewHTML(q.id)}<label>Question text (include choices if applicable)<textarea data-field="prompt" rows="2" required>${e(q.prompt)}</textarea></label>${q.type!=="rubric"?`<label>Accepted full-credit answers · one per line<textarea data-field="accepted_answers" rows="2" spellcheck="false" placeholder="está">${e((q.accepted_answers || []).join("\n"))}</textarea></label><label>Partial-credit alternatives · one per line<textarea data-field="partial_answers" rows="2" spellcheck="false" placeholder="esta | 0.5 | Add the written accent.">${e((q.partial_answers || []).map(p=>`${p.answer} | ${p.points} | ${p.feedback}`).join("\n"))}</textarea></label><p class="small muted">Format: answer | points | feedback. Only explicitly listed alternatives earn partial credit.</p>`:`<div><strong class="small">Rubric criteria</strong><p class="small muted">Criterion point values must add up to this question’s maximum.</p><div class="rubric-rows">${(q.rubric || []).map((c,ci)=>`<div class="criterion-editor" data-criterion="${ci}"><label>ID<input data-c-field="id" value="${e(c.id)}" required></label><label>Criterion<input data-c-field="description" value="${e(c.description)}" required></label><label>Max points<input data-c-field="max_points" type="number" min="0.001" step="any" value="${e(c.max_points)}" required></label><button type="button" class="icon-button danger" data-remove-criterion="${ci}" data-question="${i}" aria-label="Remove criterion">×</button></div>`).join("")}</div><button type="button" data-add-criterion="${i}" class="quiet" style="margin-top:9px">+ Add criterion</button></div>`}<label>Question policy / rubric guidance<textarea data-field="notes" rows="2" placeholder="State how to handle spelling, acceptable meanings, or accent errors.">${e(q.notes || "")}</textarea></label></div><details><summary>Normalization rules · ${n.accent_policy==="ignore_acute"?"acute accents ignored":"accents preserved"}</summary><div class="normalization-options"><label class="checkbox"><input type="checkbox" data-normalization="trim" ${n.trim?"checked":""}>Trim surrounding whitespace</label><label class="checkbox"><input type="checkbox" data-normalization="collapse_whitespace" ${n.collapse_whitespace?"checked":""}>Collapse repeated whitespace</label><label class="checkbox"><input type="checkbox" data-normalization="case_sensitive" ${n.case_sensitive?"checked":""}>Require matching letter case</label><label class="checkbox"><input type="checkbox" data-normalization="ignore_terminal_punctuation" ${n.ignore_terminal_punctuation?"checked":""}>Ignore terminal punctuation</label><label class="span-2">Accent policy<select data-normalization="accent_policy"><option value="strict" ${n.accent_policy==="strict"?"selected":""}>Strict · está and esta are different</option><option value="ignore_acute" ${n.accent_policy==="ignore_acute"?"selected":""}>Ignore acute accents for this question only</option></select><span class="muted small">ñ and ü always remain distinct. Original text is never normalized or rewritten.</span></label></div></details></section>`;
  }).join("");
  $$("[data-field=type]",$("#question-editor")).forEach(node=>node.addEventListener("change",()=>{collectEditor();const i=Number(node.closest("[data-q-index]").dataset.qIndex),q=state.editor.questions[i];if(q.type==="rubric"&&!q.rubric.length)q.rubric=[{id:"meaning",description:"",max_points:q.max_points}];renderQuestionEditor();}));
  $$("[data-remove-question]").forEach(button=>button.addEventListener("click",()=>{collectEditor();state.editor.questions.splice(Number(button.dataset.removeQuestion),1);renderQuestionEditor();}));
  $$("[data-add-criterion]").forEach(button=>button.addEventListener("click",()=>{collectEditor();const q=state.editor.questions[Number(button.dataset.addCriterion)];q.rubric.push({id:`c${q.rubric.length+1}`,description:"",max_points:1});renderQuestionEditor();}));
  $$("[data-remove-criterion]").forEach(button=>button.addEventListener("click",()=>{collectEditor();state.editor.questions[Number(button.dataset.question)].rubric.splice(Number(button.dataset.removeCriterion),1);renderQuestionEditor();}));
}
function collectEditor() {
  const form=$("#assessment-form");
  for(const key of ["title","course","instructions","model"])state.editor[key]=form.elements[key].value;
  state.editor.num_ctx=Number(form.elements.num_ctx.value);
  state.editor.questions=$$("[data-q-index]").map(section=>{
    const i=Number(section.dataset.qIndex),previous=state.editor.questions[i],q={...blankQuestion(i+1),...previous};
    for(const field of $$("[data-field]",section)){
      const key=field.dataset.field;
      if(key==="accepted_answers")q[key]=field.value.split(/\r?\n/).filter(v=>v!=="");
      else if(key==="partial_answers")q[key]=field.value.split(/\r?\n/).filter(v=>v.trim()).map(line=>{const [answer,points,...feedback]=line.split("|");return {answer:answer.trim(),points:Number(points?.trim()),feedback:feedback.join("|").trim()};});
      else if(key==="max_points")q[key]=Number(field.value);
      else q[key]=field.value;
    }
    for(const field of $$("[data-normalization]",section))q.normalization[field.dataset.normalization]=field.type==="checkbox"?field.checked:field.value;
    const criteria=$$("[data-criterion]",section);
    if(criteria.length)q.rubric=criteria.map(row=>Object.fromEntries($$("[data-c-field]",row).map(f=>[f.dataset.cField,f.dataset.cField==="max_points"?Number(f.value):f.value])));
    else if(q.type==="rubric")q.rubric=[];
    if(q.type==="rubric") { q.accepted_answers=[]; q.partial_answers=[]; }
    else q.rubric=[];
    return q;
  });
  return state.editor;
}
function editorPayload() {
  const a=collectEditor();
  const payload={title:a.title,course:a.course,instructions:a.instructions,questions:a.questions,model:a.model,num_ctx:a.num_ctx,synthetic:!!a.synthetic};
  if(state.keySource)payload.key_source=state.keySource;
  return payload;
}
function applyJSON(value,{preserveSource=false}={}) {
  const parsed=JSON.parse(value); const a=parsed.draft || parsed;
  if(!a || !Array.isArray(a.questions) || !a.questions.length) throw new Error("A structured key needs a questions array with at least one question.");
  state.editor={...state.editor,...a,questions:a.questions.map((q,i)=>({...blankQuestion(i+1),...q,normalization:{...blankQuestion(1).normalization,...q.normalization}}))};
  if(!preserveSource){state.keySource=a.key_source || null;state.keyReviews=[];state.keyDetails=null;$("#editor-key-source").innerHTML="";$("#extraction-notice").classList.add("hidden");}
  fillEditor();toast("Key loaded into the editable form. Review it before saving and approving.");
  if(!preserveSource && state.keySource)loadEditorSource(state.keySource,state.editorSession);
}

function editorFingerprint() {
  const fileStamp=id=>{const file=$(id).files[0];return file?[file.name,file.size,file.lastModified]:null;};
  return JSON.stringify({key:editorPayload(),existingFile:fileStamp("#key-upload"),assessmentFile:fileStamp("#assessment-key-upload"),defaultPoints:$("#generation-default-points").value});
}
function installReturnedKey(result,mode) {
  state.keySource=result.source_id;state.extractionWarnings=result.warnings || [];state.keyReviews=result.question_reviews || [];
  applyJSON(JSON.stringify(result.draft),{preserveSource:true});
  const generated=mode==="generate",note=$("#extraction-notice");note.classList.remove("hidden");
  note.innerHTML=`<div><strong>${generated?"Model-generated answer key · teacher review required":"Automatically extracted existing key · teacher review required"}</strong><p>${generated?"Check every proposed answer and rubric. Verify all assumed point values and complete any unresolved questions before saving.":"Check all questions, points, answers, and rubric criteria against the source."}</p>${state.extractionWarnings.length?`<ul>${state.extractionWarnings.map(w=>`<li>${e(w)}</li>`).join("")}</ul>`:""}</div>`;
  const details={id:result.source_id,method:generated?"model_generated_key":"model_extraction",warnings:state.extractionWarnings,question_reviews:state.keyReviews,provenance:result.provenance || {},pages:[],original_url:`/api/key/sources/${endpoint(result.source_id)}`};
  $("#editor-key-source").innerHTML=sourceDetailsHTML(details,result.source_id);
  renderQuestionEditor();loadEditorSource(result.source_id,state.editorSession);
}
async function requestKeyDraft(mode) {
  $("#editor-error").textContent="";
  const generated=mode==="generate",file=$(generated?"#assessment-key-upload":"#key-upload").files[0];
  if(!file)throw new Error(generated?"Choose an assessment image or single-page PDF first.":"Choose a PDF, image, or text answer key first.");
  const current=collectEditor(),session=state.editorSession,request=++state.keyRequestSequence;
  if(generated && current.instructions.length>2000)throw new Error("For key generation, keep the overall grading instructions to 2,000 characters or fewer. You can add more detail after reviewing the draft.");
  const form=new FormData();form.append("file",file);form.append("model",current.model);form.append("num_ctx",String(current.num_ctx));
  if(generated){
    const points=Number($("#generation-default-points").value);
    if(!Number.isFinite(points)||points<=0)throw new Error("Enter a positive default point value per question.");
    form.append("course",current.course);form.append("instructions",current.instructions);form.append("default_points",String(points));
  }
  const fingerprint=editorFingerprint();
  const result=await api(generated?"/key/generate":"/key/extract",{method:"POST",form});
  if(session!==state.editorSession || request!==state.keyRequestSequence || !$("#editor-dialog").open){toast("A key request finished for a previous editor. The current form was left unchanged.");return;}
  if(fingerprint!==editorFingerprint()){
    const note=$("#extraction-notice");note.classList.remove("hidden");
    note.innerHTML='<div><strong>Draft ready · your current edits were kept</strong><p>You changed the form while the request was running. Loading this returned draft will replace the current question form.</p><button type="button" class="secondary" id="load-returned-key">Replace form with returned draft</button></div>';
    $("#load-returned-key").addEventListener("click",()=>{if(session===state.editorSession && request===state.keyRequestSequence && $("#editor-dialog").open)installReturnedKey(result,mode);});
    return;
  }
  installReturnedKey(result,mode);
}

async function openScoring() {
  if(!state.assessment || state.assessment.status!=="approved")throw new Error("Approve the answer key before adjusting section totals.");
  if(state.transcriptionDirty || state.scoreDirty)throw new Error("Save your current answer edits before adjusting scoring.");
  const session=++state.scoringSession,assessmentId=state.assessment.id;
  state.scoring=null;$("#scoring-loading").textContent="Loading current scoring…";$("#scoring-loading").classList.remove("hidden");$("#scoring-editor").classList.add("hidden");$("#scoring-dialog").showModal();
  try {
    const result=await api(`/assessments/${endpoint(assessmentId)}/scoring`);
    if(session!==state.scoringSession || !$("#scoring-dialog").open || state.assessment?.id!==assessmentId)return;
    state.scoring={assessmentId,session,revision:result.revision,sections:structuredClone(result.sections || []),policy:result.policy,history:result.history || [],preview:null};
    $("#scoring-note").value="";$("#scoring-error").textContent="";$("#scoring-loading").classList.add("hidden");$("#scoring-editor").classList.remove("hidden");
    renderScoringSections();invalidateScoringPreview(false);renderScoringHistory();
  } catch(error){if(session===state.scoringSession && $("#scoring-dialog").open)$("#scoring-loading").textContent=error.message;}
}
function collectScoring() {
  if(!state.scoring)return null;
  state.scoring.sections=$$("[data-scoring-section]").map(node=>({
    id:node.dataset.scoringSection,
    label:$("[data-section-label]",node).value,
    question_ids:$$("[data-section-question]:checked",node).map(input=>input.dataset.sectionQuestion),
    target_points:Number($("[data-section-target]",node).value),
  }));
  return {revision:state.scoring.revision,sections:structuredClone(state.scoring.sections),note:$("#scoring-note").value};
}
function invalidateScoringPreview(changed=true) {
  if(state.scoring)state.scoring.preview=null;
  $("#scoring-confirm").checked=false;$("#scoring-confirm").disabled=true;$("#apply-scoring").disabled=true;
  $("#scoring-preview").innerHTML=`<p class="small muted">${changed?"Scoring settings changed. Preview the updated totals before applying.":"Preview the changes before applying an adjustment."}</p>`;
}
function renderScoringSections() {
  if(!state.scoring)return;
  const sections=state.scoring.sections,questions=state.assessment.questions;
  $("#scoring-sections").innerHTML=sections.length?sections.map((section,index)=>{
    const elsewhere=new Set(sections.filter((_,i)=>i!==index).flatMap(s=>s.question_ids));
    return `<section class="scoring-section" data-scoring-section="${e(section.id)}"><div class="section-heading"><strong>Section ${index+1}</strong><button type="button" class="quiet danger" data-remove-scoring-section="${index}">Remove section</button></div><div class="field-grid"><label>Section label<input data-section-label maxlength="200" value="${e(section.label)}" placeholder="Multiple choice"></label><label>New section maximum<input data-section-target type="number" min="0.01" step="any" value="${e(section.target_points)}"></label></div><fieldset class="section-question-options"><legend>Questions in this section</legend>${questions.map(q=>`<label class="checkbox"><input type="checkbox" data-section-question="${e(q.id)}" ${section.question_ids.includes(q.id)?"checked":""} ${elsewhere.has(q.id)?"disabled":""}><span><strong>Q${e(q.id)}</strong> · ${fmt(q.max_points)} raw points<span class="question-option-text">${e(q.prompt)}</span>${elsewhere.has(q.id)?'<span class="small muted">Already assigned to another section</span>':""}</span></label>`).join("")}</fieldset><p class="small muted">Uncheck a question in its current section before assigning it to another.</p></section>`;
  }).join(""):'<div class="source-empty">No section adjustments. A preview will restore or retain the original question point values.</div>';
  const assigned=new Set(sections.flatMap(section=>section.question_ids)),unassigned=questions.filter(q=>!assigned.has(q.id));
  $("#scoring-unassigned").textContent=unassigned.length?`Unassigned questions keep their original weight: ${unassigned.map(q=>`Q${q.id}`).join(", ")} (${fmt(unassigned.reduce((total,q)=>total+q.max_points,0))} points).`:"All questions are assigned to sections.";
  $$("[data-section-label],[data-section-target]").forEach(input=>input.addEventListener("input",()=>invalidateScoringPreview()));
  $$("[data-section-question]").forEach(input=>input.addEventListener("change",()=>{collectScoring();invalidateScoringPreview();renderScoringSections();}));
  $$("[data-remove-scoring-section]").forEach(button=>button.addEventListener("click",()=>{collectScoring();state.scoring.sections.splice(Number(button.dataset.removeScoringSection),1);invalidateScoringPreview();renderScoringSections();}));
}
function renderScoringHistory() {
  const history=state.scoring?.history || [];
  $("#scoring-history").innerHTML=history.length?`<details class="scoring-history"><summary>Previous scoring adjustments (${history.length})</summary>${[...history].reverse().map(item=>`<div class="history-item"><strong>${e(dateText(item.created_at || item.applied_at))}${item.revision!==undefined?` · Revision ${e(item.revision)}`:""}</strong>${item.note?`<p class="small">${e(item.note)}</p>`:""}<details><summary class="small">View recorded adjustment</summary><pre>${e(JSON.stringify(item,null,2))}</pre></details></div>`).join("")}</details>`:"";
}
function scoringTotal(value,maximum,complete) {
  return !complete || value===null || value===undefined?'<span class="muted">Incomplete</span>':`${fmt(value)}${maximum===null || maximum===undefined?"":` <span class="muted">/ ${fmt(maximum)}</span>`}`;
}
async function previewScoring() {
  const scoring=state.scoring;if(!scoring)return;
  $("#scoring-error").textContent="";
  const payload=collectScoring();
  for(const section of payload.sections){
    if(!section.label.trim())throw new Error("Give every section a label.");
    if(!section.question_ids.length)throw new Error(`Select at least one question for ${section.label}.`);
    if(!Number.isFinite(section.target_points)||section.target_points<=0)throw new Error(`Enter a positive maximum for ${section.label}.`);
  }
  const fingerprint=JSON.stringify(payload),session=scoring.session;
  invalidateScoringPreview(false);
  const result=await api(`/assessments/${endpoint(scoring.assessmentId)}/scoring/preview`,{method:"POST",body:payload});
  if(!state.scoring || session!==state.scoringSession || !$("#scoring-dialog").open)return;
  if(fingerprint!==JSON.stringify(collectScoring()))throw new Error("Scoring settings changed while the preview was being calculated. Preview the current settings again.");
  state.scoring.preview={result,payload,fingerprint};
  const policy=result.policy,students=result.students || [];
  $("#scoring-preview").innerHTML=`<div class="section-heading"><h3>Review the proposed totals</h3><strong>${fmt(policy.raw_max_points)} raw → ${fmt(policy.max_points)} adjusted points</strong></div>${policy.sections?.length?`<ul class="scoring-summary">${policy.sections.map(section=>`<li><strong>${e(section.label)}</strong>: earned points ÷ ${fmt(section.raw_max_points)} × ${fmt(section.target_points)} <span class="muted">(factor ${fmtFactor(section.factor)})</span></li>`).join("")}</ul>`:'<p class="small">This restores the original point values for every question.</p>'}${students.length?`<div class="scoring-table-wrap"><table class="scoring-table"><thead><tr><th scope="col">Student</th><th scope="col">Original raw total</th><th scope="col">Current total</th><th scope="col">Proposed total</th><th scope="col">Answer status</th></tr></thead><tbody>${students.map(student=>`<tr><th scope="row">${e(student.student_id)}</th><td>${scoringTotal(student.raw_total,student.raw_max_points ?? policy.raw_max_points,student.complete)}</td><td>${scoringTotal(student.previous_total,student.previous_max_points,student.complete)}</td><td>${scoringTotal(student.total,student.max_points ?? policy.max_points,student.complete)}</td><td>${badge(student.all_approved?"approved":student.complete?"needs_review":"pending",student.all_approved?"Approved":student.complete?"Awaiting approval":"Incomplete")}</td></tr>`).join("")}</tbody></table></div>`:'<p class="small muted">No student submissions yet. This policy will apply as answers are graded.</p>'}<p class="small muted">Incomplete responses remain unresolved. Applying this adjustment preserves the original question scores and their existing approvals.</p>`;
  $("#scoring-confirm").disabled=false;
}
async function applyScoring() {
  const scoring=state.scoring,preview=scoring?.preview;
  if(!preview || !$("#scoring-confirm").checked)throw new Error("Preview and confirm the proposed totals before applying.");
  if(preview.fingerprint!==JSON.stringify(collectScoring())){invalidateScoringPreview();throw new Error("The section settings changed. Generate a fresh preview.");}
  const session=scoring.session;
  try {
    await api(`/assessments/${endpoint(scoring.assessmentId)}/scoring`,{method:"PUT",body:{...preview.payload,preview_hash:preview.result.preview_hash,confirmed:true}});
  } catch(error){if(session===state.scoringSession)invalidateScoringPreview();throw error;}
  if(session!==state.scoringSession || !$("#scoring-dialog").open)return;
  $("#scoring-dialog").close();
  if(state.assessment?.id===scoring.assessmentId)await refreshAssessment({refreshCurrent:false});
  toast(preview.payload.sections.length?"Reviewed section adjustment applied. Original scores and approvals are preserved.":"Original question point values restored. Scores and approvals are preserved.");
}

async function renderPilot() {
  if(!state.assessment)return;
  const a=state.assessment;
  let result={entries:[],totals:{}};
  try{result=await api(`/assessments/${endpoint(a.id)}/time`);}catch(error){result.error=error.message;}
  if(state.assessment?.id!==a.id)return;
  const entries=Array.isArray(result)?result:result.entries || result.records || [];
  const totals=result.totals || Object.fromEntries(["scanning","review","baseline"].map(phase=>[phase,entries.filter(v=>v.phase===phase).reduce((n,v)=>n+v.seconds,0)]));
  $("#pilot-content").innerHTML=`<div class="intake-grid"><section class="panel"><h3>Record the whole grading workflow</h3><p>Include scanning and reviewing time. Compare it with your previous manual grading time for a similar batch.</p><div class="time-metrics">${["scanning","review","baseline"].map(phase=>`<div class="time-metric"><strong>${fmt((totals[phase] || 0)/60)}<small> min</small></strong><span>${phase==="baseline"?"Manual baseline":phase==="review"?"Reviewing":"Scanning / intake"}</span></div>`).join("")}</div><div class="form-stack"><label>Timer phase<select id="timer-phase"><option value="review">Reviewing</option><option value="scanning">Scanning / intake</option><option value="baseline">Manual baseline</option></select></label><div class="button-row"><button id="timer-toggle" class="secondary">${state.timer?"Stop & record":"Start timer"}</button><span id="timer-display" class="timer-running">${state.timer?timerText():"00:00"}</span></div></div><hr><form id="time-form" class="form-stack"><div class="field-grid"><label>Record time manually<select name="phase"><option value="scanning">Scanning / intake</option><option value="review">Reviewing</option><option value="baseline">Manual baseline</option></select></label><label>Minutes<input name="minutes" type="number" min="0.01" step="any" required></label></div><label>Note<input name="note" placeholder="Optional: scan setup, checking faint handwriting…"></label><button class="secondary" type="submit">Record time</button></form>${result.error?`<p class="inline-error">${e(result.error)}</p>`:""}</section><section class="panel"><h3>A useful first pilot</h3><p>Use two separate sets of previously graded papers: one to adjust the key and prompts, and one held aside for evaluation. Keep the model and rubric fixed inside each assessment.</p><p>Inspect every answer in the held-out set. Count transcription errors, wrongly awarded points, wrongly withheld points, and errors that received no review flag. Break results down by question type.</p><p>“No review flag” does not mean “correct.” Model confidence is not shown as a validated probability.</p><p>${a.synthetic?"This is a synthetic batch. Its results test the workflow, not classroom accuracy.":"The audit file preserves original suggestions, edits, versions, and timestamps for evaluation."}</p><h4 style="margin-top:21px">Recorded time</h4>${entries.length?entries.map(entry=>`<div class="time-row"><span>${e(entry.phase)}${entry.note?`<br><span class="muted">${e(entry.note)}</span>`:""}</span><strong>${fmt(entry.seconds/60)} min</strong></div>`).join(""):'<p>No time recorded yet.</p>'}</section></div>`;
  $("#time-form").addEventListener("submit",event=>{event.preventDefault();const form=event.currentTarget;run($("button",form),async()=>{await api(`/assessments/${endpoint(a.id)}/time`,{method:"POST",body:{phase:form.elements.phase.value,seconds:Number(form.elements.minutes.value)*60,note:form.elements.note.value}});await renderPilot();toast("Time recorded.");},"Saving…");});
  $("#timer-toggle").addEventListener("click",event=>run(event.currentTarget,async()=>{
    if(!state.timer){state.timer={assessmentId:a.id,start:Date.now(),phase:$("#timer-phase").value};clearInterval(state.timerInterval);state.timerInterval=setInterval(()=>{const node=$("#timer-display");if(node)node.textContent=timerText();},1000);}
    else {const timer=state.timer;await api(`/assessments/${endpoint(timer.assessmentId)}/time`,{method:"POST",body:{phase:timer.phase,seconds:Math.max(1,Math.round((Date.now()-timer.start)/1000)),note:"Browser timer"}});state.timer=null;clearInterval(state.timerInterval);toast("Timer recorded.");}
    await renderPilot();
  }));
}
function timerText(){const seconds=state.timer?Math.floor((Date.now()-state.timer.start)/1000):0;return `${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`;}

async function showSettings() {
  $("#settings-dialog").showModal();await refreshConnection();
}
async function refreshConnection() {
  const s=state.settings;$("#settings-content").innerHTML=`<p class="muted">Checking the configured local Ollama service…</p>`;
  let status;
  try {status=await api("/ollama");}catch(error){status={ok:false,error:error.message};}
  $("#settings-content").innerHTML=`<dl class="connection-details"><dt>Endpoint</dt><dd>${e(s.ollama_url)}</dd><dt>Default model</dt><dd>${e(s.model)}</dd><dt>Context</dt><dd>${e(s.num_ctx)} tokens</dd><dt>App version</dt><dd>${e(s.app_version)}</dd><dt>Ollama version</dt><dd>${e(status.version || status.ollama_version || "Unavailable")}</dd></dl>${status.ok?'<p class="badge approved">Local service reached</p>':`<div class="error-box">${e(status.error || "Ollama is unavailable.")}</div>`}<p class="small muted">Change connection and model defaults in the server configuration, then restart the app. Existing approved assessments retain their model settings.</p>${status.models?.length?`<h4>Installed local models</h4><pre class="connection-output">${e(status.models.map(m=>`${m.name || m.model} · ${m.digest || "no digest"}`).join("\n"))}</pre>`:""}<div id="model-check-result"></div>`;
}
async function checkModel() {
  const target=state.assessment?.model || state.settings?.model;
  const result=await api("/ollama/check",{method:"POST",body:{model:target}});
  $("#model-check-result").innerHTML=`<h4>Compatibility check</h4><pre class="connection-output">${e(JSON.stringify(result,null,2))}</pre>`;
  toast("Synthetic model check finished. See the returned results below.");
}
function slug(value){return String(value).replace(/[^a-zA-Z0-9_-]+/g,"-").replace(/^-|-$/g,"").slice(0,100)||"assessment";}
async function download(path,filename) {
  const response=await fetch(`/api${path}`,{credentials:"same-origin",cache:"no-store"});
  if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{detail=`Export failed (${response.status}).`;}throw new Error(typeof detail==="string"?detail:JSON.stringify(detail));}
  const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
}

// Top-level event wiring. All imported/student/model text is escaped or assigned with textContent.
$("#login-form").addEventListener("submit",event=>{event.preventDefault();const form=event.currentTarget,button=$("button",form);busy(button,async()=>{
  $("#login-error").textContent="";await api("/login",{method:"POST",body:{password:$("#password").value}});$("#password").value="";await enterWorkspace();
},"Opening…").catch(error=>{$("#login-error").textContent=error.message;});});
$("#logout").addEventListener("click",event=>{if(!leaveEdits())return;run(event.currentTarget,async()=>{await api("/logout",{method:"POST",body:{}});showLogin();});});
$("#new-assessment").addEventListener("click",()=>openEditor());$("#welcome-new").addEventListener("click",()=>openEditor());
$("#demo-button").addEventListener("click",event=>{if(!leaveEdits())return;run(event.currentTarget,async()=>{const result=await api("/demo",{method:"POST",body:{}});state.assessments=await api("/assessments");await selectAssessment(result.assessment_id,true);toast("Synthetic examples loaded. Review and approve their draft key to try scoring.");},"Creating examples…");});
$("#assessment-list").addEventListener("click",event=>{const node=event.target.closest("[data-assessment]");if(node)selectAssessment(node.dataset.assessment).catch(error=>toast(error.message,true));});
$("#student-list").addEventListener("click",event=>{const node=event.target.closest("[data-response]");if(node)selectResponse(node.dataset.response).catch(error=>toast(error.message,true));});
$$(".tabs button").forEach(button=>button.addEventListener("click",()=>setTab(button.dataset.tab)));
$("#review-mode").addEventListener("change",event=>setReviewMode(event.target.value).catch(error=>toast(error.message,true)));
$("#question-filter").addEventListener("change",event=>selectQuestion(event.target.value).catch(error=>toast(error.message,true)));
$("#state-filter").addEventListener("change",async event=>{if(!leaveEdits()){event.target.value=state.filter;return;}state.filter=event.target.value;try{await selectFirstResponse();}catch(error){toast(error.message,true);}});
$("#batch-transcribe").addEventListener("click",event=>run(event.currentTarget,()=>startJob("transcribe"),"Starting…"));
$("#batch-grade").addEventListener("click",event=>run(event.currentTarget,()=>startJob("grade"),"Starting…"));
$("#settings-open").addEventListener("click",()=>showSettings().catch(error=>toast(error.message,true)));
$("#refresh-ollama").addEventListener("click",event=>run(event.currentTarget,()=>refreshConnection(),"Checking…"));
$("#check-ollama").addEventListener("click",event=>busy(event.currentTarget,()=>checkModel(),"Testing local model…").catch(error=>{const node=$("#model-check-result");node.textContent=error.message;node.className="error-box";}));
$$("[data-close]").forEach(button=>button.addEventListener("click",()=>{if(button.dataset.close==="regions-dialog"&&!leaveRegionEdits())return;document.getElementById(button.dataset.close).close();}));
$("#regions-dialog").addEventListener("cancel",event=>{if(!leaveRegionEdits())event.preventDefault();});
$("#regions-dialog").addEventListener("close",()=>{++state.regionSession;state.regionEditor=null;});
$("#detect-regions-run").addEventListener("click",()=>detectRegions().catch(error=>regionError(error.message)));
$("#regions-question").addEventListener("change",event=>selectRegionQuestion(event.target.value));
$("#regions-page").addEventListener("change",event=>{if(!state.regionEditor)return;state.regionEditor.page=Number(event.target.value);state.regionEditor.drawing=false;renderRegionEditor();});
$("#regions-draw").addEventListener("click",()=>{if(!state.regionEditor)return;state.regionEditor.drawing=!state.regionEditor.drawing;renderRegionEditor();});
$("#regions-select-all").addEventListener("click",()=>{const editor=state.regionEditor;if(!editor || editor.record?.status!=="ready" || editor.working)return;for(const q of editor.record.questions)if(q.can_apply && validRegionBox(editor.boxes[q.question_id]))editor.included.add(q.question_id);invalidateRegionConfirmation();renderRegionEditor();});
$("#regions-confirm").addEventListener("change",()=>updateRegionSaveControls());
$("#regions-apply").addEventListener("click",event=>{const session=state.regionSession;busy(event.currentTarget,applyRegions,"Saving mappings…").catch(error=>{if(regionSessionActive(session))regionError(error.message);}).finally(()=>{if(regionSessionActive(session))updateRegionSaveControls();});});
$("#regions-manual").addEventListener("click",()=>{if(!leaveRegionEdits())return;const submission=state.regionEditor?.submissionId;$("#regions-dialog").close();if(submission)reviewSubmission(submission).catch(error=>toast(error.message,true));});
setupRegionDrawing();
$("#key-checked").addEventListener("change",event=>{$("#confirm-key-approval").disabled=!event.target.checked;});
$("#confirm-key-approval").addEventListener("click",event=>run(event.currentTarget,async()=>{await api(`/assessments/${endpoint(state.assessment.id)}/approve`,{method:"POST",body:{}});$("#approval-dialog").close();await refreshAssessment();toast("Answer key approved. Scoring rules are now fixed.");},"Approving…"));
$("#scoring-dialog").addEventListener("close",()=>{++state.scoringSession;state.scoring=null;});
$("#scoring-note").addEventListener("input",()=>invalidateScoringPreview());
$("#scoring-confirm").addEventListener("change",event=>{$("#apply-scoring").disabled=!event.target.checked || !state.scoring?.preview;});
$("#add-scoring-section").addEventListener("click",()=>{if(!state.scoring)return;collectScoring();state.scoring.sections.push({id:`section_${Date.now().toString(36)}_${++state.sectionSerial}`,label:"",question_ids:[],target_points:1});invalidateScoringPreview();renderScoringSections();$$("[data-section-label]").at(-1)?.focus();});
$("#clear-scoring-sections").addEventListener("click",()=>{if(!state.scoring)return;state.scoring.sections=[];invalidateScoringPreview();renderScoringSections();});
for(const [id,task,label] of [["#preview-scoring",previewScoring,"Calculating…"],["#apply-scoring",applyScoring,"Applying…"]]){
  $(id).addEventListener("click",event=>{const session=state.scoringSession;busy(event.currentTarget,task,label).catch(error=>{if(session===state.scoringSession && $("#scoring-dialog").open){$("#scoring-error").textContent=error.message;$("#scoring-error").scrollIntoView({block:"nearest"});}}).finally(()=>{if(session===state.scoringSession && $("#scoring-dialog").open)$("#apply-scoring").disabled=!state.scoring?.preview || !$("#scoring-confirm").checked;});});
}
$("#add-question").addEventListener("click",()=>{collectEditor();const ids=new Set(state.editor.questions.map(q=>String(q.id)));let id=state.editor.questions.length+1;while(ids.has(String(id)))id++;state.editor.questions.push(blankQuestion(id));renderQuestionEditor();$$(".editor-question").at(-1)?.scrollIntoView({block:"nearest"});});
$("#show-json").addEventListener("click",()=>{$("#key-json").value=JSON.stringify(editorPayload(),null,2);});
$("#apply-json").addEventListener("click",()=>{try{$("#editor-error").textContent="";applyJSON($("#key-json").value);}catch(error){$("#editor-error").textContent=error.message;$("#editor-error").scrollIntoView({block:"nearest"});}});
$("#json-key-upload").addEventListener("change",async event=>{const file=event.target.files[0];if(!file)return;try{$("#editor-error").textContent="";applyJSON(await file.text());}catch(error){$("#editor-error").textContent=error.message;$("#editor-error").scrollIntoView({block:"nearest"});}});
for(const [id,mode,label] of [["#extract-key","extract","Extracting locally…"],["#generate-key","generate","Generating locally…"]]){
  $(id).addEventListener("click",event=>{const session=state.editorSession;busy(event.currentTarget,()=>requestKeyDraft(mode),label).catch(error=>{if(session===state.editorSession && $("#editor-dialog").open){$("#editor-error").textContent=error.message;$("#editor-error").scrollIntoView({block:"nearest"});}});});
}
$("#assessment-form").addEventListener("submit",event=>{event.preventDefault();const button=$("button[type=submit]",event.currentTarget);busy(button,async()=>{
  ++state.keyRequestSequence;$("#editor-error").textContent="";const payload=editorPayload();if(!payload.questions.length)throw new Error("Add at least one question.");
  const incomplete=payload.questions.filter(q=>q.type==="rubric"?(!q.rubric.length||q.rubric.some(c=>!c.description.trim())):(!q.accepted_answers.length||!q.accepted_answers.some(answer=>answer.trim())));
  if(incomplete.length)throw new Error(`Complete the missing answers or rubric for Q${incomplete.map(q=>q.id).join(", Q")} before saving.`);
  const result=await api(state.editorId?`/assessments/${endpoint(state.editorId)}`:"/assessments",{method:state.editorId?"PUT":"POST",body:payload});
  $("#editor-dialog").close();state.assessments=await api("/assessments");await selectAssessment(result.id,true);setTab("key");toast("Draft key saved. Review it, then approve to enable scoring.");
},"Saving…").catch(error=>{$("#editor-error").textContent=error.message;});});

document.addEventListener("keydown",event=>{
  if(!state.auth || state.tab!=="review" || !state.current || $("dialog[open]"))return;
  const typing=event.target.matches("input,textarea,select,[contenteditable=true]");
  if(event.ctrlKey&&event.key==="Enter"){event.preventDefault();const next=event.shiftKey && state.reviewMode==="quiz",button=$(next?"#approve-next":"#approve-response");if(button&&!button.disabled)run(button,next?approveAndNext:approveCurrent,"Approving…");return;}
  if(event.altKey && !event.ctrlKey && !event.metaKey && ["ArrowLeft","ArrowRight"].includes(event.key)){event.preventDefault();navigateQuestion(event.key==="ArrowLeft"?-1:1).catch(error=>toast(error.message,true));return;}
  if(typing || event.ctrlKey || event.metaKey || event.altKey)return;
  const key=event.key.toLowerCase();
  if(key==="j"){event.preventDefault();navigateStudent(1);}else if(key==="k"){event.preventDefault();navigateStudent(-1);}else if(key==="t"){event.preventDefault();$("#transcription")?.focus();}else if(key==="s"){event.preventDefault();$("#score")?.focus();}else if(["[","]","arrowleft","arrowright"].includes(key)){event.preventDefault();navigateQuestion(key==="["||key==="arrowleft"?-1:1).catch(error=>toast(error.message,true));}
});
window.addEventListener("beforeunload",event=>{if(state.transcriptionDirty||state.scoreDirty||state.timer||state.regionEditor?.dirty){event.preventDefault();event.returnValue="";}});
(async()=>{try{const session=await api("/session");if(session.authenticated)await enterWorkspace();else showLogin();}catch(error){showLogin();$("#login-error").textContent=error.message;}})();
