/* EZ Grade: local-only browser client. No external dependencies or requests. */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const e = escapeHTML;
const endpoint = value => encodeURIComponent(String(value));
const safeURL = value => typeof value === "string" && value.startsWith("/api/") && !value.includes("\\") ? value : "";
const fmt = n => n === null || n === undefined || !Number.isFinite(Number(n)) ? "—" : Number(n).toLocaleString(undefined, {maximumFractionDigits: 3});
const dateText = value => value ? new Date(value).toLocaleString() : "—";
const stateLabel = value => ({pending:"Pending", needs_review:"Needs review", approved:"Approved", draft:"Draft key"}[value] || value || "Pending");
const badge = (state, label) => `<span class="badge ${["pending","needs_review","approved","draft","synthetic"].includes(state) ? state : "pending"}">${e(label || stateLabel(state))}</span>`;
const blankQuestion = index => ({id:String(index),type:"exact",prompt:"",max_points:1,accepted_answers:[],partial_answers:[],normalization:{trim:true,collapse_whitespace:true,case_sensitive:false,ignore_terminal_punctuation:false,accent_policy:"strict"},rubric:[],notes:""});
const state = {settings:null, assessments:[], assessment:null, submissions:[], responses:[], current:null, questionId:null, filter:"all", selectedId:null, tab:"review", assessmentSequence:0, detailSequence:0, transcriptionDirty:false, scoreDirty:false, crop:null, editor:null, editorId:null, keySource:null, extractionWarnings:[], activeJob:null, polling:null, toastTimer:null, timer:null, timerInterval:null, auth:false};

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
  clearTimeout(state.polling); state.activeJob=null; state.current=null; state.selectedId=null; state.questionId=null;
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
  $("#assessment-header").innerHTML=`<div class="assessment-title-row"><div><p class="eyebrow">ASSESSMENT WORKSPACE</p><h1>${e(a.title)}</h1><div class="assessment-meta"><span>${e(a.course)}</span><span>·</span><span>${a.questions.length} questions</span><span>·</span><span>${fmt(a.max_points ?? a.questions.reduce((n,q)=>n+q.max_points,0))} points</span>${a.synthetic?badge("synthetic","Synthetic examples"):""}</div></div><div class="assessment-actions"><button id="export-csv" class="secondary" ${total===0 || approved!==total?"disabled":""} title="Every response must be approved before final export">Export final CSV</button><button id="export-audit" class="quiet">Audit file</button></div></div><div class="progress-summary"><span><b>${approved}</b> / ${total} approved</span><div class="progress-track" aria-label="Approval progress"><span style="width:${total?approved/total*100:0}%"></span></div><span><b>${needs}</b> need review</span><span><b>${pending}</b> pending</span></div>`;
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
  return state.responses.filter(r=>String(r.question_id)===String(state.questionId) && (state.filter==="all" || r.state===state.filter));
}
function renderQueue() {
  const rows=visibleResponses(); $("#queue-count").textContent=String(rows.length);
  $("#student-list").innerHTML=rows.length?rows.map(r=>`<button class="student-row ${r.id===state.selectedId?"active":""}" data-response="${e(r.id)}"><strong>${e(r.student_id)}</strong><span class="student-row-meta">${badge(r.state)}<span class="student-score">${fmt(r.points ?? r.suggested_points)} / ${fmt(questionFor(r)?.max_points)}</span></span></button>`).join(""):`<p class="small muted" style="padding:12px">No answers in this view.</p>`;
}
function questionFor(response) { return state.assessment?.questions.find(q=>String(q.id)===String(response?.question_id)); }
async function selectFirstResponse() {
  const rows=visibleResponses();
  if(rows.length) await selectResponse(rows[0].id,true);
  else { state.current=null; state.selectedId=null; renderQueue(); $("#response-review").innerHTML=`<div class="empty compact"><h2>${state.responses.length?"No answers in this view":"No answers yet"}</h2><p>${state.responses.length?"Choose another question or show every answer.":"Add a typed response or one PDF/image per student to begin."}</p>${state.responses.length?"":'<button id="empty-add" class="primary">Add submissions</button>'}</div>`; $("#empty-add")?.addEventListener("click",()=>setTab("intake")); }
}
async function selectResponse(id, force=false) {
  if(!force && !leaveEdits()) return;
  const seq=++state.detailSequence;
  const r=await api(`/responses/${endpoint(id)}`);
  if(seq!==state.detailSequence) return;
  state.current=r; state.selectedId=id; state.crop=null; state.transcriptionDirty=false; state.scoreDirty=false;
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
  else sourceHTML=`${image?`<a href="${e(image)}" target="_blank" rel="noopener"><img class="answer-crop" src="${e(image)}" alt="Original answer region for question ${e(q.id)}"></a><p class="source-caption">Question region · click to inspect at full size</p>`:'<div class="source-empty">Select the answer region on the page below. A missing crop is not a blank answer.</div>'}${original?`<a class="small" href="${e(original)}" target="_blank" rel="noopener">Open original submission</a>`:""}${cropToolsHTML(r,sub,approved)}`;
  const suggestionHTML=suggestion?`<div class="suggestion-card"><div class="suggestion-title"><div>Original suggestion<div class="method">${e(suggestion.method==="deterministic"?"Answer-key matching":suggestion.method==="blank"?"Confirmed blank":"Local model · provisional")}</div></div><span class="suggestion-points">${fmt(suggestion.suggested_points)}<small> / ${fmt(q.max_points)}</small></span></div><p>${e(suggestion.feedback)}</p>${(suggestion.criteria || []).map(c=>`<div class="criterion"><div class="criterion-title"><span>${e(q.rubric.find(v=>v.id===c.criterion_id)?.description || c.criterion_id)}</span><span>${fmt(c.points)} pt</span></div>${c.evidence?`<blockquote>${e(c.evidence)}</blockquote>`:'<p class="muted">No quoted evidence.</p>'}${c.feedback?`<p>${e(c.feedback)}</p>`:""}</div>`).join("")}</div>`:'<div class="source-empty" style="margin:16px 0">No score suggestion yet. Read or enter the transcription, then draft a score.</div>';
  $("#response-review").innerHTML=`<div class="response-heading"><div><h2>${e(r.student_id)} <span class="muted">/ Q${e(q.id)}</span></h2><p>${e(q.type==="choice"?"Multiple choice":q.type==="exact"?"Exact / accepted answer":"Rubric interpretation")} · ${fmt(q.max_points)} points</p></div><div class="response-nav"><button class="icon-button" id="previous-answer" title="Previous student (K)" aria-label="Previous student">‹</button><button class="icon-button" id="next-answer" title="Next student (J)" aria-label="Next student">›</button>${badge(r.state)}</div></div><div class="question-context"><p>${e(q.prompt)}</p><details><summary>Key & scoring rules</summary><div class="key-answer">${e(q.type==="rubric"?q.rubric.map(c=>`${c.id}: ${c.description} (${c.max_points} pt)`).join("\n"):`Accepted: ${q.accepted_answers.join(" · ")}`)}</div><p class="small muted" style="margin:8px 0 0">${e(normalizationSummary(q))}</p>${q.notes?`<p class="small" style="white-space:pre-wrap;margin:8px 0 0">${e(q.notes)}</p>`:""}</details></div><div class="answer-columns"><div class="source-column"><div class="section-kicker"><span>Original answer</span><span>${r.source_kind==="typed"?"Typed":"Scanned"}</span></div>${sourceHTML}</div><div class="grading-column"><div class="section-kicker"><span>Transcription</span><span id="unsaved-indicator" class="unsaved"></span></div><label for="transcription"><span class="muted small">${e(transLabel)}</span><textarea id="transcription" rows="4" spellcheck="false" autocomplete="off" ${disabled} placeholder="Preserve the student's spelling, accents, and verb endings exactly.">${e(r.transcription ?? "")}</textarea></label><div class="transcription-actions"><label class="checkbox"><input id="transcription-confirmed" type="checkbox" ${r.transcription_confirmed?"checked":""} ${disabled}>I checked this text against the original${r.transcription===""?" (truly blank)":""}.</label><button id="save-transcription" class="secondary" ${disabled}>Save text</button></div>${r.source_kind==="scan"&&!approved?'<div class="source-actions"><button id="read-current" class="secondary">Read this crop</button></div>':""}${r.error?`<div class="error-box"><strong>Processing did not finish</strong>${e(r.error)}<p style="margin:6px 0 0">This is unresolved. No failure is treated as a zero.</p></div>`:""}${flags.length?`<div class="review-flags"><strong>Check before approving</strong><ul>${[...new Set(flags)].map(f=>`<li>${e(f)}</li>`).join("")}</ul></div>`:""}${suggestionHTML}<div class="section-kicker"><span>Your reviewed result</span>${!approved?'<button id="grade-current" class="quiet" style="font-size:11px;text-transform:none;letter-spacing:0">Draft this score</button>':""}</div><div class="score-fields"><label for="score">Points / ${fmt(q.max_points)}<input id="score" type="number" min="0" max="${q.max_points}" step="any" value="${r.points===null || r.points===undefined?"":e(r.points)}" ${disabled}></label><label for="feedback">Brief feedback<textarea id="feedback" rows="3" ${disabled}>${e(r.feedback || "")}</textarea></label></div><details class="review-note"><summary class="small muted">Teacher review note</summary><textarea id="review-note" rows="2" ${disabled} placeholder="Optional explanation for an adjustment."></textarea></details><div class="approve-actions">${approved?'<button id="reopen-response" class="secondary">Reopen for correction</button>':'<button id="save-score" class="secondary">Save result</button><button id="approve-response" class="primary">Approve answer</button>'}</div><p class="small muted" style="margin:10px 0 0">${approved?`Approved ${e(dateText(r.approved_at))}`:"Only your approval makes this result final."}</p></div></div><div class="response-footer"><span>${e(transLabel)}</span><span>·</span><span>Revision ${r.revision}</span><details><summary>Suggestion & edit history (${(r.history || []).length})</summary><div class="history-list">${historyHTML(r.history || [])}</div></details></div>`;
  $("#previous-answer").addEventListener("click",()=>navigateStudent(-1)); $("#next-answer").addEventListener("click",()=>navigateStudent(1));
  $("#transcription").addEventListener("input",()=>markDirty("transcription")); $("#transcription-confirmed").addEventListener("change",()=>markDirty("transcription"));
  ["#score","#feedback","#review-note"].forEach(sel=>$(sel).addEventListener("input",()=>markDirty("score")));
  $("#save-transcription").addEventListener("click",event=>run(event.currentTarget,()=>saveTranscription(),"Saving…"));
  $("#save-score")?.addEventListener("click",event=>run(event.currentTarget,()=>saveScore(),"Saving…"));
  $("#approve-response")?.addEventListener("click",event=>run(event.currentTarget,()=>approveCurrent(),"Approving…"));
  $("#reopen-response")?.addEventListener("click",event=>run(event.currentTarget,async()=>{const next=await api(`/responses/${endpoint(r.id)}/reopen`,{method:"POST",body:{revision:state.current.revision}});await acceptResponse(next);toast("Answer reopened. Previous approval is preserved in history.");},"Reopening…"));
  $("#read-current")?.addEventListener("click",event=>run(event.currentTarget,()=>startJob("transcribe",[r.id]),"Starting…"));
  $("#grade-current")?.addEventListener("click",event=>run(event.currentTarget,()=>startJob("grade",[r.id]),"Starting…"));
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
  const targetId=state.current.id;
  if(state.transcriptionDirty) {await saveTranscription();toast("Text saved. Review or enter a fresh score before approving.");return;}
  if(state.scoreDirty) await saveScore();
  if(state.current?.id!==targetId || state.selectedId!==targetId){toast("Result saved. Selection changed, so no answer was approved.");return;}
  const r=state.current;
  const next=await api(`/responses/${endpoint(r.id)}/approve`,{method:"POST",body:{revision:r.revision}});
  await acceptResponse(next,{message:`${r.student_id}, Q${r.question_id}: approved.`});
}
async function navigateStudent(delta) {
  const rows=visibleResponses(); if(!rows.length) return;
  const i=rows.findIndex(r=>r.id===state.selectedId), target=Math.max(0,Math.min(rows.length-1,i+delta));
  if(rows[target].id!==state.selectedId) try{await selectResponse(rows[target].id);}catch(error){toast(error.message,true);}
}
async function navigateQuestion(delta) {
  if(!state.assessment || !leaveEdits()) return;
  const questions=state.assessment.questions,i=questions.findIndex(q=>String(q.id)===String(state.questionId)),next=questions[Math.max(0,Math.min(questions.length-1,i+delta))];
  if(next) {state.questionId=next.id;renderQuestionSelect();await selectFirstResponse();}
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
  $("#intake-content").innerHTML=`<div class="intake-grid"><div><section class="panel"><h3>Type or paste a student’s answers</h3><p>Enter exactly what the student wrote. An empty captured answer is a true blank; turn off “captured” when an answer is missing from the import.</p><form id="typed-form" class="form-stack"><label>Student identifier<input name="student_id" required maxlength="80" placeholder="Student 001" autocomplete="off"></label>${a.questions.map(q=>`<div class="typed-answer"><label>Q${e(q.id)} · ${e(q.prompt)}<textarea data-answer="${e(q.id)}" rows="2" spellcheck="false"></textarea></label><label class="checkbox"><input type="checkbox" data-captured="${e(q.id)}" checked>Answer captured (empty means truly blank)</label></div>`).join("")}<button class="primary" type="submit">Add typed submission</button><p class="inline-error" id="typed-error"></p></form></section></div><div><section class="panel"><h3>Upload one student’s paper</h3><p>One PDF or image per student. Select each question’s answer region in the review workspace.</p><form id="scan-form" class="form-stack"><label>Student identifier<input name="student_id" required maxlength="80" placeholder="Student 002" autocomplete="off"></label><label>Submission file<input name="file" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" required></label><button class="primary" type="submit">Upload submission</button><p class="inline-error" id="scan-error"></p></form></section><section class="panel"><h3>Import a small typed batch</h3><p>CSV columns: <code>student_id,${a.questions.map(q=>`q${e(q.id)}`).join(",")}</code>. UTF-8 preserves Spanish accents. Missing question columns remain unresolved.</p><form id="csv-form" class="form-stack"><label>Response CSV<input name="file" type="file" accept=".csv,text/csv" required></label><button class="secondary" type="submit">Import typed CSV</button><p class="inline-error" id="csv-error"></p></form></section><section class="panel"><h3>Imported submissions <span class="muted">(${state.submissions.length})</span></h3><div class="intake-status">${state.submissions.length?state.submissions.map(s=>`<div class="submission-row"><span><strong>${e(s.student_id)}</strong><br><span class="muted">${s.source_kind==="scan"?`${(s.pages || []).length} page(s)` :"Typed answers"}</span></span><button class="quiet" data-review-submission="${e(s.id)}">Review</button></div>`).join(""):'<p>No submissions yet.</p>'}</div></section></div></div>`;
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
  $("#key-content").innerHTML=`<section class="panel"><div class="section-heading"><div><h3>${a.status==="approved"?"Approved answer key":"Draft answer key"}</h3><p style="margin:6px 0 0">${a.status==="approved"?"These scoring rules are fixed for this assessment.":"Review every question before enabling grading."}</p></div><div class="button-row">${a.status!=="approved"?'<button id="edit-key" class="secondary">Edit key</button><button id="approve-key" class="primary">Approve key</button>':'<button id="duplicate-key" class="secondary">Copy to new assessment</button>'}</div></div>${a.key_source?`<p><a href="/api/key/sources/${endpoint(a.key_source)}" target="_blank" rel="noopener">Inspect original uploaded key</a></p>`:""}${a.instructions?`<p style="white-space:pre-wrap">${e(a.instructions)}</p>`:""}${a.questions.map(q=>`<article class="key-question"><div class="title"><span>Question ${e(q.id)} · ${e(q.type==="choice"?"Multiple choice":q.type==="exact"?"Accepted-answer matching":"Rubric interpretation")}</span><span>${fmt(q.max_points)} points</span></div><p class="prompt">${e(q.prompt)}</p>${q.type!=="rubric"?`<p><strong>Full credit:</strong> ${q.accepted_answers.map(v=>`<code>${e(v)}</code>`).join(" · ")}</p>${q.partial_answers?.length?`<ul>${q.partial_answers.map(p=>`<li>${fmt(p.points)} pt for <code>${e(p.answer)}</code> · ${e(p.feedback)}</li>`).join("")}</ul>`:""}`:`<ul>${q.rubric.map(c=>`<li><strong>${e(c.id)} · ${fmt(c.max_points)} pt:</strong> ${e(c.description)}</li>`).join("")}</ul>`}<div class="key-rules"><span>${e(normalizationSummary(q))}</span>${q.normalization?.trim?"<span>Trim outer whitespace</span>":""}${q.normalization?.collapse_whitespace?"<span>Collapse repeated whitespace</span>":""}</div>${q.notes?`<p class="notes"><strong>Question policy:</strong> ${e(q.notes)}</p>`:""}</article>`).join("")}</section><section class="panel subtle"><h3>Batch consistency</h3><p class="version-detail">Model: ${e(a.model)} · Context: ${e(a.num_ctx)} tokens<br>Rubric version: ${e(a.version)}<br>Prompt version: ${e(a.prompt_version)}<br>Model digest: ${e(a.model_digest || "Recorded when local inference first runs")}</p></section>`;
  $("#edit-key")?.addEventListener("click",()=>openEditor(a));
  $("#duplicate-key")?.addEventListener("click",()=>{openEditor({...a,title:`${a.title} — new version`},true);});
  $("#approve-key")?.addEventListener("click",()=>{$("#key-checked").checked=false;$("#confirm-key-approval").disabled=true;$("#approval-dialog").showModal();});
}

function openEditor(assessment=null,copy=false) {
  if(!leaveEdits())return;
  state.editorId=assessment&&!copy?assessment.id:null;
  state.editor=structuredClone(assessment || {title:"",course:"Spanish 2 CP",instructions:"",questions:[blankQuestion(1)],model:state.settings?.model || "qwen3-vl:4b-instruct-q4_K_M",num_ctx:state.settings?.num_ctx || 4096,synthetic:false});
  state.keySource=assessment?.key_source || null;state.extractionWarnings=[];
  $("#editor-title").textContent=state.editorId?"Edit draft answer key":"Create an assessment";
  $("#key-json").value="";$("#editor-error").textContent="";$("#extraction-notice").classList.add("hidden");
  fillEditor();$("#editor-dialog").showModal();
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
    return `<section class="editor-question" data-q-index="${i}"><div class="q-heading"><strong>Question ${i+1}</strong><button type="button" class="quiet danger" data-remove-question="${i}">Remove</button></div><div class="q-main"><label>Number<input data-field="id" value="${e(q.id)}" required maxlength="32"></label><label>Scoring method<select data-field="type"><option value="choice" ${q.type==="choice"?"selected":""}>Multiple choice</option><option value="exact" ${q.type==="exact"?"selected":""}>Exact / accepted answers</option><option value="rubric" ${q.type==="rubric"?"selected":""}>Rubric · local model</option></select></label><label>Max points<input data-field="max_points" type="number" min="0.001" step="any" max="100" value="${e(q.max_points)}" required></label></div><div class="q-body"><label>Question text (include choices if applicable)<textarea data-field="prompt" rows="2" required>${e(q.prompt)}</textarea></label>${q.type!=="rubric"?`<label>Accepted full-credit answers · one per line<textarea data-field="accepted_answers" rows="2" spellcheck="false" placeholder="está">${e((q.accepted_answers || []).join("\n"))}</textarea></label><label>Partial-credit alternatives · one per line<textarea data-field="partial_answers" rows="2" spellcheck="false" placeholder="esta | 0.5 | Add the written accent.">${e((q.partial_answers || []).map(p=>`${p.answer} | ${p.points} | ${p.feedback}`).join("\n"))}</textarea></label><p class="small muted">Format: answer | points | feedback. Only explicitly listed alternatives earn partial credit.</p>`:`<div><strong class="small">Rubric criteria</strong><p class="small muted">Criterion point values must add up to this question’s maximum.</p><div class="rubric-rows">${(q.rubric || []).map((c,ci)=>`<div class="criterion-editor" data-criterion="${ci}"><label>ID<input data-c-field="id" value="${e(c.id)}" required></label><label>Criterion<input data-c-field="description" value="${e(c.description)}" required></label><label>Max points<input data-c-field="max_points" type="number" min="0.001" step="any" value="${e(c.max_points)}" required></label><button type="button" class="icon-button danger" data-remove-criterion="${ci}" data-question="${i}" aria-label="Remove criterion">×</button></div>`).join("")}</div><button type="button" data-add-criterion="${i}" class="quiet" style="margin-top:9px">+ Add criterion</button></div>`}<label>Question policy / rubric guidance<textarea data-field="notes" rows="2" placeholder="State how to handle spelling, acceptable meanings, or accent errors.">${e(q.notes || "")}</textarea></label></div><details><summary>Normalization rules · ${n.accent_policy==="ignore_acute"?"acute accents ignored":"accents preserved"}</summary><div class="normalization-options"><label class="checkbox"><input type="checkbox" data-normalization="trim" ${n.trim?"checked":""}>Trim surrounding whitespace</label><label class="checkbox"><input type="checkbox" data-normalization="collapse_whitespace" ${n.collapse_whitespace?"checked":""}>Collapse repeated whitespace</label><label class="checkbox"><input type="checkbox" data-normalization="case_sensitive" ${n.case_sensitive?"checked":""}>Require matching letter case</label><label class="checkbox"><input type="checkbox" data-normalization="ignore_terminal_punctuation" ${n.ignore_terminal_punctuation?"checked":""}>Ignore terminal punctuation</label><label class="span-2">Accent policy<select data-normalization="accent_policy"><option value="strict" ${n.accent_policy==="strict"?"selected":""}>Strict · está and esta are different</option><option value="ignore_acute" ${n.accent_policy==="ignore_acute"?"selected":""}>Ignore acute accents for this question only</option></select><span class="muted small">ñ and ü always remain distinct. Original text is never normalized or rewritten.</span></label></div></details></section>`;
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
function applyJSON(value) {
  const parsed=JSON.parse(value); const a=parsed.draft || parsed;
  if(!a || !Array.isArray(a.questions) || !a.questions.length) throw new Error("A structured key needs a questions array with at least one question.");
  state.editor={...state.editor,...a,questions:a.questions.map((q,i)=>({...blankQuestion(i+1),...q,normalization:{...blankQuestion(1).normalization,...q.normalization}}))};
  fillEditor();toast("Key loaded into the editable form. Review it before saving and approving.");
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
$("#question-filter").addEventListener("change",async event=>{if(!leaveEdits()){event.target.value=state.questionId;return;}state.questionId=event.target.value;try{await selectFirstResponse();}catch(error){toast(error.message,true);}});
$("#state-filter").addEventListener("change",async event=>{if(!leaveEdits()){event.target.value=state.filter;return;}state.filter=event.target.value;try{await selectFirstResponse();}catch(error){toast(error.message,true);}});
$("#batch-transcribe").addEventListener("click",event=>run(event.currentTarget,()=>startJob("transcribe"),"Starting…"));
$("#batch-grade").addEventListener("click",event=>run(event.currentTarget,()=>startJob("grade"),"Starting…"));
$("#settings-open").addEventListener("click",()=>showSettings().catch(error=>toast(error.message,true)));
$("#refresh-ollama").addEventListener("click",event=>run(event.currentTarget,()=>refreshConnection(),"Checking…"));
$("#check-ollama").addEventListener("click",event=>busy(event.currentTarget,()=>checkModel(),"Testing local model…").catch(error=>{const node=$("#model-check-result");node.textContent=error.message;node.className="error-box";}));
$$("[data-close]").forEach(button=>button.addEventListener("click",()=>document.getElementById(button.dataset.close).close()));
$("#key-checked").addEventListener("change",event=>{$("#confirm-key-approval").disabled=!event.target.checked;});
$("#confirm-key-approval").addEventListener("click",event=>run(event.currentTarget,async()=>{await api(`/assessments/${endpoint(state.assessment.id)}/approve`,{method:"POST",body:{}});$("#approval-dialog").close();await refreshAssessment();toast("Answer key approved. Scoring rules are now fixed.");},"Approving…"));
$("#add-question").addEventListener("click",()=>{collectEditor();const ids=new Set(state.editor.questions.map(q=>String(q.id)));let id=state.editor.questions.length+1;while(ids.has(String(id)))id++;state.editor.questions.push(blankQuestion(id));renderQuestionEditor();$$(".editor-question").at(-1)?.scrollIntoView({block:"nearest"});});
$("#show-json").addEventListener("click",()=>{$("#key-json").value=JSON.stringify(editorPayload(),null,2);});
$("#apply-json").addEventListener("click",()=>{try{$("#editor-error").textContent="";applyJSON($("#key-json").value);}catch(error){$("#editor-error").textContent=error.message;$("#editor-error").scrollIntoView({block:"nearest"});}});
$("#json-key-upload").addEventListener("change",async event=>{const file=event.target.files[0];if(!file)return;try{$("#editor-error").textContent="";applyJSON(await file.text());}catch(error){$("#editor-error").textContent=error.message;$("#editor-error").scrollIntoView({block:"nearest"});}});
$("#extract-key").addEventListener("click",event=>busy(event.currentTarget,async()=>{
  $("#editor-error").textContent="";
  const file=$("#key-upload").files[0];if(!file)throw new Error("Choose a PDF, image, or text key first.");
  collectEditor();const form=new FormData();form.append("file",file);form.append("model",state.editor.model);
  const result=await api("/key/extract",{method:"POST",form});state.keySource=result.source_id;state.extractionWarnings=result.warnings || [];
  applyJSON(JSON.stringify(result.draft));const note=$("#extraction-notice");note.classList.remove("hidden");note.innerHTML=`<div><strong>Automatically extracted draft · teacher review required</strong><p>Check all questions, points, answers, and rubric criteria against the source.</p>${result.source_id?`<a href="/api/key/sources/${endpoint(result.source_id)}" target="_blank" rel="noopener">Open original key</a>`:""}${state.extractionWarnings.length?`<p>${state.extractionWarnings.map(e).join(" · ")}</p>`:""}</div>`;
},"Extracting locally…").catch(error=>{$("#editor-error").textContent=error.message;$("#editor-error").scrollIntoView({block:"nearest"});}));
$("#assessment-form").addEventListener("submit",event=>{event.preventDefault();const button=$("button[type=submit]",event.currentTarget);busy(button,async()=>{
  $("#editor-error").textContent="";const payload=editorPayload();if(!payload.questions.length)throw new Error("Add at least one question.");
  const result=await api(state.editorId?`/assessments/${endpoint(state.editorId)}`:"/assessments",{method:state.editorId?"PUT":"POST",body:payload});
  $("#editor-dialog").close();state.assessments=await api("/assessments");await selectAssessment(result.id,true);setTab("key");toast("Draft key saved. Review it, then approve to enable scoring.");
},"Saving…").catch(error=>{$("#editor-error").textContent=error.message;});});

document.addEventListener("keydown",event=>{
  if(!state.auth || state.tab!=="review" || !state.current || $("dialog[open]"))return;
  const typing=event.target.matches("input,textarea,select,[contenteditable=true]");
  if(event.ctrlKey&&event.key==="Enter"){event.preventDefault();const button=$("#approve-response");if(button&&!button.disabled)run(button,()=>approveCurrent(),"Approving…");return;}
  if(typing || event.ctrlKey || event.metaKey || event.altKey)return;
  const key=event.key.toLowerCase();
  if(key==="j"){event.preventDefault();navigateStudent(1);}else if(key==="k"){event.preventDefault();navigateStudent(-1);}else if(key==="t"){event.preventDefault();$("#transcription")?.focus();}else if(key==="s"){event.preventDefault();$("#score")?.focus();}else if(key==="["||key==="]"){event.preventDefault();navigateQuestion(key==="["?-1:1).catch(error=>toast(error.message,true));}
});
window.addEventListener("beforeunload",event=>{if(state.transcriptionDirty||state.scoreDirty||state.timer){event.preventDefault();event.returnValue="";}});
(async()=>{try{const session=await api("/session");if(session.authenticated)await enterWorkspace();else showLogin();}catch(error){showLogin();$("#login-error").textContent=error.message;}})();
