from dataclasses import dataclass
import os
from pathlib import Path


def load_env(path: Path = Path('.env')) -> None:
    """Load this application's simple KEY=value file without executing shell code."""
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if sep and key.replace('_', '').isalnum():
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    password: str
    session_secret: str
    ollama_url: str = 'http://127.0.0.1:11434'
    model: str = 'qwen3-vl:4b-instruct-q4_K_M'
    num_ctx: int = 4096
    host: str = '127.0.0.1'
    port: int = 8765
    cookie_secure: bool = False
    max_upload_mb: int = 20

    def __post_init__(self):
        if len(self.password) < 12 or len(self.session_secret) < 32:
            raise ValueError('Set a GRADER_PASSWORD of at least 12 characters and a GRADER_SESSION_SECRET of at least 32 characters. Run python3 scripts/init_config.py first.')
        if not 2048 <= self.num_ctx <= 8192:
            raise ValueError('OLLAMA_NUM_CTX must be between 2048 and 8192.')
        if not 1 <= self.max_upload_mb <= 100:
            raise ValueError('GRADER_MAX_UPLOAD_MB must be between 1 and 100.')

    @classmethod
    def from_env(cls):
        load_env()
        return cls(
            data_dir=Path(os.getenv('GRADER_DATA_DIR', './data')).resolve(),
            password=os.getenv('GRADER_PASSWORD', ''),
            session_secret=os.getenv('GRADER_SESSION_SECRET', ''),
            ollama_url=os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434'),
            model=os.getenv('OLLAMA_MODEL', 'qwen3-vl:4b-instruct-q4_K_M'),
            num_ctx=int(os.getenv('OLLAMA_NUM_CTX', '4096')),
            host=os.getenv('GRADER_HOST', '127.0.0.1'),
            port=int(os.getenv('GRADER_PORT', '8765')),
            cookie_secure=os.getenv('GRADER_COOKIE_SECURE', 'false').lower() == 'true',
            max_upload_mb=int(os.getenv('GRADER_MAX_UPLOAD_MB', '20')),
        )
