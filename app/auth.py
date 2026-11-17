import base64
from collections import defaultdict, deque
import hashlib
import hmac
import secrets
import time
from fastapi import HTTPException, Request

COOKIE = 'spanish_grader'
MAX_AGE = 12 * 60 * 60


class Auth:
    def __init__(self, settings):
        self.settings = settings
        self.key = hashlib.sha256((settings.session_secret + settings.password).encode()).digest()
        self.failures = defaultdict(deque)

    def issue(self):
        payload = f'{int(time.time())}:{secrets.token_hex(16)}'
        signature = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f'{payload}:{signature}'.encode()).decode()

    def valid(self, request: Request):
        try:
            raw = base64.urlsafe_b64decode(request.cookies.get(COOKIE, '').encode()).decode()
            payload, signature = raw.rsplit(':', 1)
            timestamp = int(payload.split(':', 1)[0])
            expected = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
            return 0 <= time.time()-timestamp <= MAX_AGE and hmac.compare_digest(signature, expected)
        except (ValueError, UnicodeError):
            return False

    def require(self, request: Request):
        if not self.valid(request):
            raise HTTPException(401, 'Sign in with your local teacher password.')

    def login(self, request: Request, password: str):
        host = request.client.host if request.client else 'local'
        entries = self.failures[host]
        cutoff = time.monotonic()-300
        while entries and entries[0] < cutoff:
            entries.popleft()
        if len(entries) >= 10:
            raise HTTPException(429, 'Too many failed sign-ins. Try again in five minutes.')
        if not secrets.compare_digest(password.encode(), self.settings.password.encode()):
            entries.append(time.monotonic())
            raise HTTPException(401, 'Incorrect local password.')
        entries.clear()
