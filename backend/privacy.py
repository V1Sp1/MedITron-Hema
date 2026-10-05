"""Research acknowledgement, not legal consent for identifiable medical data."""
import hashlib
import secrets
import time
import os
import json
import shutil
from collections import deque
from threading import RLock
from uuid import uuid4
from pathlib import Path

from .store import RETENTION_SECONDS

COOKIE = "hema_research_session"
POLICY_VERSION = "research-privacy-2026-10-04-v1"
USE_NOTICE = "Исследовательский прототип: только синтетические или действительно обезличенные данные. Не использовать для диагностики, назначения лечения или исключения заболевания."


class IntegrationLimiter:
    """Bound inference requests per key and per process; no input values logged."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, RLock()
        self.requests = deque(maxlen=240)

    def allow(self, key_id):
        with self.lock:
            now = self.clock()
            while self.requests and self.requests[0][0] <= now - 60:
                self.requests.popleft()
            if len(self.requests) >= 240 or sum(k == key_id for _, k in self.requests) >= 60:
                return False
            self.requests.append((now, key_id))
            return True


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return True
    if os.name == 'nt':
        import ctypes as c
        kernel = c.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [c.c_uint32, c.c_int, c.c_uint32]; kernel.OpenProcess.restype = c.c_void_p
        kernel.GetExitCodeProcess.argtypes = [c.c_void_p, c.POINTER(c.c_uint32)]; kernel.GetExitCodeProcess.restype = c.c_int
        kernel.CloseHandle.argtypes = [c.c_void_p]; kernel.CloseHandle.restype = c.c_int
        handle = kernel.OpenProcess(0x1000, 0, pid)
        if not handle:
            return c.get_last_error() != 87
        try:
            exit_code = c.c_uint32()
            return not kernel.GetExitCodeProcess(handle, c.byref(exit_code)) or exit_code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def cleanup_abandoned(root):
    """Delete only marked staging directories of dead processes; keep live peers."""
    for folder in Path(root).glob('hema-live-*'):
        marker = folder / '.hema-ephemeral-owner.json'
        if folder.is_symlink() or not folder.is_dir() or not marker.is_file() or marker.stat().st_size > 1024:
            continue
        try:
            owner = json.loads(marker.read_text('utf-8'))
            if owner.get('version') == 'hema-ephemeral-v1' and not process_alive(owner.get('pid')):
                shutil.rmtree(folder)
        except (ValueError, OSError, AttributeError):
            continue


class ResearchSessions:
    def __init__(self, clock=time.time):
        self.clock, self.lock = clock, RLock()
        self.sessions = {}
        self.events = deque(maxlen=2000)

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def cleanup(self):
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items()
                             if value["expiresAt"] > self.clock()}
            while self.events and self.events[0]["at"] <= self.clock() - RETENTION_SECONDS:
                self.events.popleft()

    def create(self, data_kind):
        self.cleanup()
        with self.lock:
            if len(self.sessions) >= 1000:
                raise ValueError("Слишком много активных сеансов. Повторите позже.")
            token = secrets.token_urlsafe(32)
            grant = {"id": str(uuid4()), "dataKind": data_kind, "policyVersion": POLICY_VERSION,
                     "acknowledgedAt": self.clock(), "expiresAt": self.clock() + RETENTION_SECONDS}
            self.sessions[self.digest(token)] = grant
            return token, dict(grant)

    def current(self, token):
        if not token or len(token) > 128:
            return None
        self.cleanup()
        with self.lock:
            grant = self.sessions.get(self.digest(token))
            return dict(grant) if grant else None

    def revoke(self, token):
        if token and len(token) <= 128:
            with self.lock:
                return self.sessions.pop(self.digest(token), None)

    def event(self, action, *, grant_id=None, object_id=None, result="allowed"):
        self.cleanup()
        with self.lock:
            self.events.append({"at": self.clock(), "action": action, "sessionId": grant_id,
                                "objectId": object_id, "result": result})


def minimized_observation(observation):
    """Remove identity and arbitrary source text after mismatch checks/normalization."""
    for index, document in enumerate(observation["documents"], 1):
        document["filename"] = f"analysis-{index}.pdf"
        document["metadata"] = {k: v for k, v in document.get("metadata", {}).items()
                                if k in {"collection_dates", "report_dates"}}
        for page in document["pages"]:
            page["rows"] = []
    for measurement in observation["measurements"]:
        for key in ("raw_name", "raw_value", "raw_unit", "reference", "specimen"):
            measurement[key] = None
        for key in ("text", "context_text"):
            measurement["source"][key] = None
    observation["privacy"] = {"rawTextRetained": False, "identityMetadataRetained": False,
                               "storage": "volatile", "retentionSeconds": RETENTION_SECONDS}
    return observation
