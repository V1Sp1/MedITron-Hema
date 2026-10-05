"""Bounded volatile medical storage. Never open the historical hema.sqlite3."""
import json
import sqlite3
import time
from pathlib import Path
from threading import RLock

RETENTION_SECONDS = 60 * 60
MAX_STORAGE_BYTES = 128 * 1024 * 1024


class StorageFull(RuntimeError):
    pass


class Store:
    def __init__(self, root: Path, *, clock=time.time, max_bytes=MAX_STORAGE_BYTES):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / ".hema-runtime").touch(mode=0o600, exist_ok=True)
        self.clock, self.max_bytes = clock, max_bytes
        self.lock = RLock()
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.executescript("""
          PRAGMA secure_delete=ON;
          CREATE TABLE objects (kind TEXT, id TEXT, revision INTEGER, expires REAL, payload TEXT,
            PRIMARY KEY(kind,id));
          CREATE TABLE documents (observation_id TEXT, id TEXT, payload BLOB,
            PRIMARY KEY(observation_id,id));
        """)

    def _cleanup(self):
        self.db.execute("DELETE FROM objects WHERE expires<=?", (self.clock(),))
        self.db.execute("DELETE FROM documents WHERE observation_id NOT IN (SELECT id FROM objects WHERE kind='observation')")

    def cleanup(self):
        with self.lock, self.db:
            self._cleanup()

    def put(self, kind: str, payload: dict, documents: dict[str, bytes] | None = None):
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        documents = documents or {}
        with self.lock, self.db:
            self._cleanup()
            used = self.db.execute("SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM objects").fetchone()[0]
            used += self.db.execute("SELECT COALESCE(SUM(length(payload)),0) FROM documents").fetchone()[0]
            count = self.db.execute("SELECT count(*) FROM objects").fetchone()[0]
            if used + len(text.encode()) + sum(map(len, documents.values())) > self.max_bytes or count >= 1000:
                raise StorageFull("Временное хранилище заполнено. Удалите данные сеанса или дождитесь их очистки.")
            expires = min(payload.get("_access", {}).get("expiresAt", self.clock() + RETENTION_SECONDS),
                          self.clock() + RETENTION_SECONDS)
            self.db.execute("INSERT INTO objects VALUES (?,?,?,?,?)",
                            (kind, payload["id"], payload.get("revision", 1), expires, text))
            for identifier, content in documents.items():
                self.db.execute("INSERT INTO documents VALUES (?,?,?)", (payload["id"], identifier, content))

    def get(self, kind: str, key: str) -> dict | None:
        with self.lock, self.db:
            self._cleanup()
            row = self.db.execute("SELECT payload FROM objects WHERE kind=? AND id=?", (kind, key)).fetchone()
            return json.loads(row[0]) if row else None

    def get_document(self, observation_id, identifier):
        with self.lock, self.db:
            self._cleanup()
            row = self.db.execute("SELECT payload FROM documents WHERE observation_id=? AND id=?",
                                  (observation_id, identifier)).fetchone()
            return row[0] if row else None

    def update(self, kind: str, payload: dict, previous_revision: int) -> bool:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self.lock, self.db:
            self._cleanup()
            current = self.db.execute("SELECT length(CAST(payload AS BLOB)) FROM objects WHERE kind=? AND id=? AND revision=?",
                                      (kind, payload['id'], previous_revision)).fetchone()
            if current is None:
                return False
            used = self.db.execute("SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM objects").fetchone()[0]
            used += self.db.execute("SELECT COALESCE(SUM(length(payload)),0) FROM documents").fetchone()[0]
            if used - current[0] + len(text.encode()) > self.max_bytes:
                raise StorageFull("Временное хранилище заполнено. Удалите данные сеанса или дождитесь их очистки.")
            result = self.db.execute("UPDATE objects SET payload=?,revision=? WHERE kind=? AND id=? AND revision=?",
                                    (text, payload["revision"], kind, payload["id"], previous_revision))
            return result.rowcount == 1

    def delete(self, kind, key):
        with self.lock, self.db:
            if kind == "observation":
                self.db.execute("DELETE FROM objects WHERE kind='report' AND json_extract(payload,'$.observationId')=?", (key,))
                self.db.execute("DELETE FROM documents WHERE observation_id=?", (key,))
            self.db.execute("DELETE FROM objects WHERE kind=? AND id=?", (kind, key))

    def delete_owner(self, grant_id):
        with self.lock, self.db:
            self.db.execute("DELETE FROM objects WHERE json_extract(payload,'$._access.grantId')=?", (grant_id,))
            self._cleanup()

    def close(self):
        with self.lock:
            self.db.close()
