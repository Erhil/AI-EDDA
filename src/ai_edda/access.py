import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path


class OwnerAuth:
    def __init__(self, config_path, *, clock=time.monotonic, session_ttl=3600):
        self.config_path = Path(config_path)
        self.clock = clock
        self.session_ttl = session_ttl
        self._sessions = {}
        self._lock = threading.Lock()
        self._config = json.loads(self.config_path.read_text(encoding="utf-8"))

    @staticmethod
    def setup(config_path, password):
        if not password:
            raise ValueError("Password must not be empty")
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as file:
            json.dump({"salt": salt.hex(), "hash": digest.hex()}, file)

    def login(self, password):
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(self._config["salt"]), n=16384, r=8, p=1
        )
        if not hmac.compare_digest(digest, bytes.fromhex(self._config["hash"])):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = self.clock() + self.session_ttl
        return token

    def authenticate(self, token):
        with self._lock:
            expiry = self._sessions.get(token, 0)
            if expiry <= self.clock():
                self._sessions.pop(token, None)
                return False
            return True

    def logout(self, token):
        with self._lock:
            return self._sessions.pop(token, None) is not None


class WriterLeases:
    def __init__(self, *, clock=time.monotonic, ttl=60, heartbeat_interval=10):
        self.clock = clock
        self.ttl = ttl
        self.heartbeat_interval = heartbeat_interval
        self._leases = {}
        self._lock = threading.Lock()

    def acquire(self, project_id, tab_id, *, takeover=False):
        if not project_id or not tab_id:
            raise ValueError("Project and tab IDs are required")
        with self._lock:
            current = self._leases.get(project_id)
            now = self.clock()
            if current and current[2] > now:
                if current[0] == tab_id:
                    return current[1]
                if not takeover:
                    return None
            token = secrets.token_urlsafe(32)
            self._leases[project_id] = (tab_id, token, now + self.ttl)
            return token

    def heartbeat(self, project_id, tab_id, token):
        with self._lock:
            if not self._valid(project_id, tab_id, token):
                return False
            self._leases[project_id] = (tab_id, token, self.clock() + self.ttl)
            return True

    def require(self, project_id, tab_id, token):
        with self._lock:
            return self._valid(project_id, tab_id, token)

    def _valid(self, project_id, tab_id, token):
        current = self._leases.get(project_id)
        if current and current[2] <= self.clock():
            self._leases.pop(project_id, None)
            return False
        return bool(current and current[0] == tab_id and current[1] == token)
