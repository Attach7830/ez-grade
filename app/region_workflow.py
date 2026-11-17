"""Durable, teacher-reviewed local layout proposals, separate from transcription.

Detection cannot change an answer. Applying reviewed boxes is atomic, checks the
original response revisions, and only fills unmapped answers without saved text
or scores. Previous manual mappings and approved results remain protected.
"""
from hashlib import sha256
from pathlib import Path
from threading import Semaphore
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.concurrency import run_in_threadpool

from .db import dump, now, parse, response_row, serialize_response, serialize_submission
from .files import make_crop
from .regions import DETECTOR_VERSION, RegionDetectionError, detect_regions


class RegionBox(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    page: int = Field(ge=0, strict=True)
    x: float = Field(ge=0, lt=1, strict=True)
    y: float = Field(ge=0, lt=1, strict=True)
    width: float = Field(gt=0, le=1, strict=True)
    height: float = Field(gt=0, le=1, strict=True)

    @model_validator(mode='after')
    def bounds(self):
        if self.x+self.width > 1.000001 or self.y+self.height > 1.000001:
            raise ValueError('Answer region must fit inside the page.')
        return self


class Proposal(RegionBox):
    question_id: str = Field(min_length=1, max_length=80)
    reason: str = Field(max_length=2000)
    flags: list[str] = Field(default_factory=list, max_length=30)


class DetectionResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    detector_version: str = Field(min_length=1, max_length=100)
    engine_version: str = Field(min_length=1, max_length=300)
    proposals: list[Proposal] = Field(max_length=100)
    missing_question_ids: list[str] = Field(max_length=100)
    warnings: list[str] = Field(max_length=200)


class ReviewedRegion(RegionBox):
    question_id: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=0, strict=True)


class ApplyRegions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmed: bool = Field(strict=True)
    regions: list[ReviewedRegion] = Field(min_length=1, max_length=100)


def file_hash(path):
    digest = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def block_reason(row, revision):
    if row is None:
        return 'This response no longer exists.'
    if row['state'] == 'approved':
        return 'This answer is approved. Reopen it and use manual region editing to change its region.'
    if row['region'] is not None or row['crop_path'] is not None:
        return 'This answer already has a saved region. Use its manual region editor to change it.'
    if (row['transcription'] is not None or row['transcription_confirmed'] or
            row['transcription_status'] not in ('pending', 'failed') or
            row['suggestion'] is not None or row['suggested_points'] is not None or
            row['points'] is not None or row['feedback']):
        return 'This answer has saved text or scoring work. Use its manual region editor to change it.'
    if row['revision'] != revision:
        return 'This answer changed after detection. Detect again before mapping it.'
    return None


def detection_view(con, row):
    result = parse(row['result'])
    snapshot = parse(row['snapshot'])
    sub = con.execute('SELECT * FROM submissions WHERE id=?', (row['submission_id'],)).fetchone()
    current = {r['question_id']:r for r in con.execute('SELECT * FROM responses WHERE submission_id=?', (row['submission_id'],))}
    eligibility = {}
    for question in snapshot['questions']:
        reason = block_reason(current.get(question['question_id']), question['revision'])
        if row['status'] != 'ready':
            reason = 'Detection did not finish. Retry detection or use manual region selection.'
        eligibility[question['question_id']] = dict(question, can_apply=reason is None, blocked_reason=reason)
    # The database result remains immutable; a view adds current eligibility.
    proposals = [dict(proposal, **eligibility[proposal['question_id']]) for proposal in result['proposals']]
    return dict(result, id=row['id'], submission_id=row['submission_id'], assessment_id=row['assessment_id'],
                key_version=row['key_version'], status=row['status'], error=row['error'], created_at=row['created_at'],
                proposals=proposals, questions=list(eligibility.values()),
                pages=serialize_submission(sub)['pages'], source_sha256=snapshot.get('source_sha256'),
                page_hashes=snapshot.get('page_hashes', []))


def register_region_routes(api, store):
    # Bound expensive OCR subprocesses while allowing other review requests.
    detection_lock = Semaphore(1)

    def required(con, table, identifier):
        if table not in {'region_detections', 'submissions', 'assessments'}:
            raise ValueError('Invalid table')
        row = con.execute(f'SELECT * FROM {table} WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Record not found.')
        return row

    @api.post('/submissions/{sid}/region-detections')
    async def detect_submission(sid: str):
        if not detection_lock.acquire(blocking=False):
            raise HTTPException(409, 'Another region detection is running. Wait for it to finish and try again.')
        try:
            with store.read() as con:
                con.execute('BEGIN')
                sub = dict(required(con, 'submissions', sid))
                if sub['source_kind'] != 'scan':
                    raise HTTPException(422, 'Automatic regions are available for scanned submissions only.')
                assessment = dict(required(con, 'assessments', sub['assessment_id']))
                if assessment['status'] != 'approved':
                    raise HTTPException(409, 'Approve the answer key before detecting its question regions.')
                # Reading/layout receives no answer key, accepted alternatives,
                # normalization policy, rubric, or student score.
                questions = [{k:q[k] for k in ('id', 'type', 'prompt')} for q in parse(assessment['data'])['questions']]
                answers = {r['question_id']:r for r in con.execute('SELECT * FROM responses WHERE submission_id=?', (sid,))}
                snapshot = {'questions':[dict(question_id=q['id'], response_id=answers[q['id']]['id'],
                                            revision=answers[q['id']]['revision']) for q in questions]}
                pages = [dict(p, index=i) for i,p in enumerate(parse(sub['pages']))]
            did, created_at = uuid4().hex, now()
            result = dict(detector_version=DETECTOR_VERSION, engine_version='unavailable', proposals=[],
                          missing_question_ids=[q['id'] for q in questions], warnings=[])
            status, error = 'ready', None

            def run_detection():
                snapshot['source_sha256'] = file_hash(sub['original_path'])
                snapshot['page_hashes'] = [dict(index=p['index'], sha256=file_hash(p['path'])) for p in pages]
                output = DetectionResult.model_validate(detect_regions(pages, questions)).model_dump()
                ids = [p['question_id'] for p in output['proposals']]
                expected = {q['id'] for q in questions}
                if len(ids) != len(set(ids)) or not set(ids) <= expected:
                    raise ValueError('Detection returned duplicated or unknown question numbers.')
                if any(p['page'] >= len(pages) for p in output['proposals']):
                    raise ValueError('Detection returned a page outside this submission.')
                # The authoritative missing list is derived from approved IDs.
                output['missing_question_ids'] = [q['id'] for q in questions if q['id'] not in ids]
                return output

            try:
                result = await run_in_threadpool(run_detection)
            except RegionDetectionError as exc:
                status, error = 'failed', str(exc)[:900]
            except (ValidationError, ValueError):
                status, error = 'failed', 'Detection returned invalid regions. No answers were changed. Retry or map the questions manually.'
            except Exception:
                status, error = 'failed', 'Local region detection did not finish. No answers were changed. Check the source file and local Tesseract installation, then retry.'
            with store.transaction() as con:
                con.execute('INSERT INTO region_detections(id,submission_id,assessment_id,key_version,status,result,snapshot,error,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                            (did,sid,assessment['id'],assessment['version'],status,dump(result),dump(snapshot),error,created_at))
                return detection_view(con, required(con, 'region_detections', did))
        finally:
            detection_lock.release()

    @api.get('/submissions/{sid}/region-detections/latest')
    def latest_detection(sid: str):
        with store.read() as con:
            con.execute('BEGIN')
            required(con, 'submissions', sid)
            row = con.execute('SELECT * FROM region_detections WHERE submission_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (sid,)).fetchone()
            return detection_view(con, row) if row else None

    @api.get('/region-detections/{did}')
    def get_detection(did: str):
        with store.read() as con:
            con.execute('BEGIN')
            return detection_view(con, required(con, 'region_detections', did))

    @api.post('/region-detections/{did}/apply')
    def apply_regions(did: str, body: ApplyRegions):
        if not body.confirmed:
            raise HTTPException(422, 'Check the question numbers and answer boxes on the original pages, then confirm them before applying.')
        identifiers = [r.question_id for r in body.regions]
        if len(identifiers) != len(set(identifiers)):
            raise HTTPException(422, 'Select only one region for each question.')
        created_crops = []
        try:
            with store.transaction() as con:
                detection = required(con, 'region_detections', did)
                if detection['status'] != 'ready':
                    raise HTTPException(409, 'This detection failed. Retry it or use manual region selection.')
                assessment = required(con, 'assessments', detection['assessment_id'])
                if assessment['status'] != 'approved' or assessment['version'] != detection['key_version']:
                    raise HTTPException(409, 'The approved answer key changed. Detect regions again.')
                sub = required(con, 'submissions', detection['submission_id'])
                pages = parse(sub['pages'])
                snapshot = parse(detection['snapshot'])
                expected = {q['question_id']:q for q in snapshot['questions']}
                result = parse(detection['result'])
                original = {p['question_id']:p for p in result['proposals']}
                prepared = []
                # Validate every requested change before making any crop or edit.
                for item in body.regions:
                    question = expected.get(item.question_id)
                    if question is None:
                        raise HTTPException(422, 'A selected question is not part of this detection and approved answer key.')
                    row = response_row(con, question['response_id'])
                    if item.revision != question['revision']:
                        raise HTTPException(409, 'Use the original detection revision. Detect again if an answer changed.')
                    reason = block_reason(row, item.revision)
                    if reason:
                        raise HTTPException(409, f'Q{item.question_id}: {reason} No regions were applied.')
                    if item.page >= len(pages):
                        raise HTTPException(422, 'Page not found in this student submission.')
                    box = item.model_dump(exclude={'question_id', 'revision'})
                    prepared.append((row, box))
                # Source hashes ensure a manually replaced/missing page cannot
                # silently turn an inspected box into a crop of another image.
                hashes = {p['index']:p['sha256'] for p in snapshot.get('page_hashes', [])}
                for page in {box['page'] for _,box in prepared}:
                    if hashes.get(page) != file_hash(pages[page]['path']):
                        raise HTTPException(409, 'The source page changed after detection. Detect its regions again.')
                for row, box in prepared:
                    dest = make_crop(pages[box['page']], box, Path(sub['original_path']).parent)
                    created_crops.append(dest)
                    changes = dict(region=dump(box), crop_path=str(dest), transcription=None, transcription_status='pending',
                                   transcription_flags='[]', transcription_confirmed=0, suggestion=None, suggested_points=None,
                                   points=None, feedback='', state='pending', error=None, approved_at=None)
                    proposed = original.get(row['question_id'])
                    proposed_box = {k:proposed[k] for k in ('page','x','y','width','height')} if proposed else None
                    store.update_response(con, row, changes, 'detected_region_applied',
                                          dict(detection_id=did, detector_version=result['detector_version'], engine_version=result['engine_version'],
                                               original_proposal=proposed, region=box, teacher_adjusted=box!=proposed_box,
                                               teacher_confirmed=True, crop=str(dest), previous_error=row['error'],
                                               previous_transcription_status=row['transcription_status']))
                responses = [serialize_response(con, response_row(con, row['id'])) for row,_ in prepared]
                return dict(detection_id=did, applied=len(responses), responses=responses)
        except BaseException as exc:
            for path in created_crops:
                path.unlink(missing_ok=True)
            if isinstance(exc, ValueError):
                raise HTTPException(422, str(exc)) from exc
            if isinstance(exc, OSError):
                raise HTTPException(422, 'Could not read or crop a source page. No regions were applied; check the submission and retry.') from exc
            raise
