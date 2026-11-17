"""One local worker; durable job status and optimistic answer revision checks."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading
from .db import dump, parse, now, response_row, Store
from .grading import grade_objective
from .models import Question
from .prompts import PROMPT_VERSION, PROMPT_HASH


class JobRunner:
    def __init__(self, store, client):
        self.store, self.client = store, client
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-grader')
        self.stopping = threading.Event()

    def close(self):
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def submit(self, job_id):
        return self.executor.submit(self.run, job_id)

    def pin(self, job, require_vision):
        meta = self.client.inspect(job['model'], require_vision=require_vision)
        with self.store.transaction() as con:
            assessment = con.execute('SELECT * FROM assessments WHERE id=?', (job['assessment_id'],)).fetchone()
            if assessment['model_digest'] and assessment['model_digest'] != meta['digest']:
                raise ValueError('The installed model digest changed. Restore the pinned model or create a new assessment; existing assessment settings are frozen.')
            if assessment['ollama_version'] and assessment['ollama_version'] != meta['ollama_version']:
                raise ValueError('Ollama runtime changed during this assessment. Create a new assessment for the new runtime.')
            con.execute('UPDATE assessments SET model_digest=?,ollama_version=? WHERE id=?',
                        (meta['digest'],meta['ollama_version'],assessment['id']))
            con.execute('UPDATE jobs SET model_digest=?,ollama_version=? WHERE id=?',
                        (meta['digest'],meta['ollama_version'],job['id']))
        return meta['digest']

    def run(self, job_id):
        with self.store.transaction() as con:
            job = dict(con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
            con.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        response_ids = parse(job['response_ids'])
        fatal = None
        try:
            with self.store.read() as con:
                a = con.execute('SELECT * FROM assessments WHERE id=?', (job['assessment_id'],)).fetchone()
                if a['status'] != 'approved' or a['version'] != job['rubric_version']:
                    raise ValueError('The answer key must be approved and unchanged.')
                if a['prompt_version'] != PROMPT_VERSION or a['prompt_hash'] != PROMPT_HASH:
                    raise ValueError('Prompt version changed. Create a new assessment or restore the previous app release.')
                assessment_data = parse(a['data'])
                questions = {q['id']: Question.model_validate(q) for q in assessment_data['questions']}
                assessment_instructions = assessment_data.get('instructions', '')
            digest = None
            for rid in response_ids:
                if self.stopping.is_set():
                    fatal = 'Application stopping. Retry unfinished answers after restart.'
                    break
                with self.store.read() as con:
                    row = response_row(con, rid)
                    if not row:
                        raise ValueError('A response no longer exists.')
                    snapshot = dict(row)
                error = None
                try:
                    if snapshot['state'] == 'approved':
                        raise ValueError('Approved response skipped; reopen it before regenerating a draft.')
                    question = questions[snapshot['question_id']]
                    if job['action'] == 'transcribe':
                        if not snapshot['crop_path']:
                            raise ValueError('Select an answer region before transcription.')
                        if snapshot['transcription_status'] == 'manual' and snapshot['transcription_confirmed']:
                            raise ValueError('Teacher transcription retained. Remap the answer region if you intend to replace it.')
                        digest = self.pin(job, True)
                        result = self.client.transcribe(job['model'], snapshot['crop_path'], expected_digest=digest, num_ctx=job['num_ctx'])
                        # The client validates literal text/status, including blank/failed separation.
                        if result['status'] == 'failed':
                            raise ValueError('Reading failed: '+ '; '.join(result.get('flags') or ['manual transcription needed']))
                        text = result.get('text', '')
                        flags = result.get('flags', [])
                        status = result['status']
                        changes = dict(transcription=text, transcription_status=status, transcription_flags=dump(flags),
                                       transcription_confirmed=0, suggestion=None, suggested_points=None, points=None,
                                       feedback='', state='needs_review' if status in ('blank','ambiguous') or flags else 'pending', error=None, approved_at=None)
                        kind = 'transcription_suggested'
                        details = dict(transcription=text, status=status, flags=flags, job_id=job_id,
                                       model_digest=digest, source_crop=snapshot['crop_path'], replaces_revision=snapshot['revision'])
                    else:
                        text = snapshot['transcription']
                        status = snapshot['transcription_status']
                        if text is None or status in ('pending','failed'):
                            raise ValueError('No usable transcription. Enter or transcribe the answer first; this is not a blank.')
                        if status == 'ambiguous' and not snapshot['transcription_confirmed']:
                            raise ValueError('Confirm or correct ambiguous handwriting before grading.')
                        if not text.strip():
                            if not snapshot['transcription_confirmed']:
                                raise ValueError('Confirm that the answer is genuinely blank before awarding zero.')
                            result = dict(suggested_points=0, criteria=[], feedback='Blank answer confirmed by teacher or explicit typed input.',
                                          review_reasons=[], method='blank')
                        elif question.type in ('choice','exact'):
                            result = grade_objective(question, text)
                        else:
                            digest = self.pin(job, False)
                            model_question = question.model_copy(update={'notes':
                                'Assessment instructions:\n'+assessment_instructions+'\n\nQuestion-specific policy:\n'+question.notes})
                            result = self.client.grade(job['model'], model_question, text, expected_digest=digest, num_ctx=job['num_ctx'])
                        # Every scan requires teacher confirmation at approval.
                        # That universal gate is not an issue-specific warning:
                        # otherwise every scan would count as "flagged" in a pilot.
                        if snapshot['transcription_flags']:
                            result['review_reasons'] = list(dict.fromkeys(result.get('review_reasons', []) + parse(snapshot['transcription_flags'], [])))
                        score = Decimal(str(result['suggested_points']))
                        if not score.is_finite() or not Decimal('0') <= score <= Decimal(str(question.max_points)):
                            raise ValueError('Invalid suggested score; no score was saved.')
                        changes = dict(suggestion=dump(result), suggested_points=float(score), points=float(score),
                                       feedback=result['feedback'], state='needs_review' if result.get('review_reasons') else 'pending', error=None, approved_at=None)
                        kind = 'grade_suggested'
                        details = dict(suggestion=result, transcription=text, job_id=job_id, model_digest=digest,
                                       replaces_revision=snapshot['revision'])
                    stale = False
                    with self.store.transaction() as con:
                        current = response_row(con, rid)
                        if current['revision'] != snapshot['revision'] or current['state'] == 'approved':
                            self.store.history(con, rid, 'stale_result_discarded', dict(job_id=job_id, result=details))
                            stale = True
                        else:
                            self.store.update_response(con, current, changes, kind, details)
                    if stale:
                        raise ValueError('Answer changed while processing. The newer teacher edit was preserved; draft result discarded.')
                except Exception as exc:
                    error = safe_error(exc)
                    with self.store.transaction() as con:
                        current = response_row(con, rid)
                        kind = 'transcription_failed' if job['action'] == 'transcribe' else 'grade_failed'
                        details = dict(error=error, job_id=job_id, attempted_revision=snapshot['revision'])
                        if current['revision'] == snapshot['revision'] and current['state'] != 'approved':
                            changes = dict(state='needs_review', error=error, points=None, suggestion=None, suggested_points=None, approved_at=None)
                            if job['action'] == 'transcribe':
                                changes.update(transcription=None, transcription_status='failed',transcription_confirmed=0)
                            self.store.update_response(con, current, changes, kind, details)
                        else:
                            self.store.history(con, rid, kind, dict(details, current_answer_preserved=True))
                with self.store.transaction() as con:
                    con.execute('UPDATE jobs SET done=done+1,errors=errors+? WHERE id=?', (1 if error else 0,job_id))
        except Exception as exc:
            fatal = safe_error(exc)
        finally:
            with self.store.transaction() as con:
                status = 'interrupted' if self.stopping.is_set() else ('failed' if fatal else 'completed')
                con.execute('UPDATE jobs SET status=?,error=?,finished_at=? WHERE id=?', (status,fatal,now(),job_id))


def safe_error(exc):
    # Do not expose tracebacks, environment secrets, or HTTP response bodies in UI.
    if isinstance(exc, (ValueError, RuntimeError)):
        return str(exc)[:900]
    return f'{type(exc).__name__}: processing did not finish. No score was approved. Check the local service and retry.'
