"""Small, explicit SQLite store. All answer edits use revision checks."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def parse(value, fallback=None):
    return json.loads(value) if value is not None else fallback


SCHEMA = '''
CREATE TABLE IF NOT EXISTS assessments (
 id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
 version TEXT NOT NULL, model TEXT NOT NULL, num_ctx INTEGER NOT NULL,
 model_digest TEXT, ollama_version TEXT, prompt_version TEXT NOT NULL,
 prompt_hash TEXT NOT NULL, approved_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id),
 student_id TEXT NOT NULL, source_kind TEXT NOT NULL, original_text TEXT,
 original_path TEXT, original_name TEXT, pages TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
 UNIQUE(assessment_id,student_id)
);
CREATE TABLE IF NOT EXISTS responses (
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id),
 submission_id TEXT NOT NULL REFERENCES submissions(id), question_id TEXT NOT NULL,
 original_text TEXT, transcription TEXT, transcription_status TEXT NOT NULL DEFAULT 'pending',
 transcription_flags TEXT NOT NULL DEFAULT '[]', transcription_confirmed INTEGER NOT NULL DEFAULT 0,
 region TEXT, crop_path TEXT, suggestion TEXT, suggested_points REAL, points REAL,
 feedback TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'pending', error TEXT,
 revision INTEGER NOT NULL DEFAULT 0, approved_at TEXT, updated_at TEXT NOT NULL,
 UNIQUE(submission_id,question_id)
);
CREATE TABLE IF NOT EXISTS history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, response_id TEXT NOT NULL REFERENCES responses(id),
 kind TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id),
 action TEXT NOT NULL, status TEXT NOT NULL, total INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0,
 errors INTEGER NOT NULL DEFAULT 0, response_ids TEXT NOT NULL, model TEXT NOT NULL,
 model_digest TEXT, ollama_version TEXT, num_ctx INTEGER NOT NULL,
 rubric_version TEXT NOT NULL, prompt_version TEXT NOT NULL, prompt_hash TEXT NOT NULL,
 prompt_hashes TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(assessment_id)
 WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS time_entries (
 id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES assessments(id),
 phase TEXT NOT NULL, seconds REAL NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS key_sources (
 id TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scoring_policies (
 assessment_id TEXT PRIMARY KEY REFERENCES assessments(id), revision INTEGER NOT NULL,
 sections TEXT NOT NULL, note TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scoring_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, assessment_id TEXT NOT NULL REFERENCES assessments(id),
 revision INTEGER NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(assessment_id, revision)
);
CREATE INDEX IF NOT EXISTS responses_assessment ON responses(assessment_id);
CREATE INDEX IF NOT EXISTS history_response ON history(response_id,id);
'''


class Store:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'grader.sqlite3'
        with self.connect() as con:
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError(f'Unsupported database schema {version}; use the matching application release.')
            con.execute('PRAGMA journal_mode=WAL')
            con.executescript(SCHEMA)
            # Version 2 adds independent, audited section-scaling policies.
            # Existing questions, response scores, and grading prompts are unchanged.
            con.execute('PRAGMA user_version=2')
            # A process stopped during work never produces inferred final scores.
            con.execute("UPDATE jobs SET status='interrupted',error='Application stopped during processing. Review and retry unfinished answers.',finished_at=? WHERE status IN ('queued','running')", (now(),))
        self.path.chmod(0o600)

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA synchronous=FULL')
        return con

    @contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def read(self):
        con = self.connect()
        try:
            yield con
        finally:
            con.close()

    @staticmethod
    def history(con, response_id, kind, details):
        con.execute('INSERT INTO history(response_id,kind,details,created_at) VALUES(?,?,?,?)',
                    (response_id, kind, dump(details), now()))

    @staticmethod
    def update_response(con, row, changes, kind, details):
        allowed = {'transcription','transcription_status','transcription_flags','transcription_confirmed',
                   'region','crop_path','suggestion','suggested_points','points','feedback','state','error','approved_at'}
        if not set(changes) <= allowed:
            raise ValueError('Invalid response fields')
        payload = dict(changes, revision=row['revision']+1, updated_at=now())
        clause = ','.join(f'{key}=?' for key in payload)
        result = con.execute(f'UPDATE responses SET {clause} WHERE id=? AND revision=?',
                             (*payload.values(), row['id'], row['revision']))
        if result.rowcount != 1:
            raise RuntimeError('Response changed; refresh before editing.')
        Store.history(con, row['id'], kind, details)


def response_row(con, response_id):
    return con.execute('SELECT r.*,s.student_id,s.source_kind FROM responses r JOIN submissions s ON s.id=r.submission_id WHERE r.id=?', (response_id,)).fetchone()


def serialize_response(con, row, include_history=True):
    result = dict(row)
    for name, default in [('region', None), ('suggestion', None), ('transcription_flags', [])]:
        result[name] = parse(result[name], default)
    result['transcription_confirmed'] = bool(result['transcription_confirmed'])
    result['crop_url'] = f"/api/responses/{row['id']}/crop?v={row['revision']}" if row['crop_path'] else None
    result.pop('crop_path', None)
    if include_history:
        result['history'] = [dict(id=h['id'], kind=h['kind'], created_at=h['created_at'], details=parse(h['details']))
                             for h in con.execute('SELECT * FROM history WHERE response_id=? ORDER BY id', (row['id'],))]
    return result


def serialize_submission(row):
    result = dict(row)
    pages = parse(result.pop('pages'), [])
    result['pages'] = [dict(index=i, url=f"/api/submissions/{row['id']}/pages/{i}", width=p['width'], height=p['height']) for i,p in enumerate(pages)]
    result['original_url'] = f"/api/submissions/{row['id']}/original" if row['original_path'] else None
    result.pop('original_path', None)
    return result
