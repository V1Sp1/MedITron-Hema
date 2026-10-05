"""Local organization-issued doctor accounts and revocable opaque sessions."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from threading import RLock
from uuid import uuid4

COOKIE = 'hema_doctor_session'
SESSION_SECONDS = 8 * 60 * 60
WINDOW = 15 * 60
API_SCOPES = frozenset({"predict", "reports", "pdf"})


def login_name(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.@-]{2,63}', value):
        raise ValueError('Логин: 3–64 символа, латинские буквы, цифры, точки, @, дефис или подчёркивание.')
    return value


def hash_password(password):
    if not 15 <= len(password) <= 128:
        raise ValueError('Пароль должен содержать от 15 до 128 символов.')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256*1024*1024)
    return 'scrypt$131072$8$1$' + salt.hex() + '$' + digest.hex()


def verify_password(password, encoded):
    try:
        method, n, r, p, salt, expected = encoded.split('$')
        if (method, n, r, p) != ('scrypt', '131072', '8', '1'):
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**17, r=8, p=1, maxmem=256*1024*1024)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


class LoginFailure(Exception):
    def __init__(self, throttled=False):
        self.throttled = throttled


class AuthStore:
    def __init__(self, root: Path):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        (root / '.hema-runtime').touch(mode=0o600, exist_ok=True)
        path = root / 'auth.sqlite3'
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        self.lock = RLock()
        # Serializing KDF calls also bounds concurrent memory use of scrypt.
        self.login_lock = RLock()
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS organizations (id TEXT PRIMARY KEY, name TEXT NOT NULL, contact TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, display_name TEXT NOT NULL, active INTEGER NOT NULL,
            created_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS attempts (username TEXT NOT NULL, remote TEXT NOT NULL, attempted_at REAL NOT NULL);
          CREATE INDEX IF NOT EXISTS attempts_time ON attempts(attempted_at);
          CREATE TABLE IF NOT EXISTS api_keys (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
            organization_id TEXT NOT NULL, label TEXT NOT NULL, active INTEGER NOT NULL, expires_at REAL NOT NULL);
        ''')
        columns = {row['name'] for row in self.db.execute('PRAGMA table_info(users)')}
        if 'organization_id' not in columns:
            self.db.execute("ALTER TABLE users ADD COLUMN organization_id TEXT NOT NULL DEFAULT 'local-research'")
        key_columns = {row['name'] for row in self.db.execute('PRAGMA table_info(api_keys)')}
        if 'scopes' not in key_columns:
            self.db.execute("ALTER TABLE api_keys ADD COLUMN scopes TEXT NOT NULL DEFAULT '[\"predict\"]'")
        self.db.execute("INSERT OR IGNORE INTO organizations VALUES (?,?,?)",
                        ('local-research', 'Локальная исследовательская демонстрация', 'Не определён для реальных пациентов'))
        self.db.commit()
        path.chmod(0o600)
        self.dummy = hash_password(secrets.token_urlsafe(24))

    @staticmethod
    def profile(row):
        return {'id': row['id'], 'username': row['username'], 'displayName': row['display_name'],
                'organizationId': row['organization_id']}

    def create_user(self, username, password, display_name='', organization_id='local-research'):
        username = login_name(username)
        encoded = hash_password(password)
        display_name = display_name.strip() or username
        if len(display_name) > 120:
            raise ValueError('Имя для отображения: не более 120 символов.')
        with self.lock, self.db:
            if not self.db.execute('SELECT 1 FROM organizations WHERE id=?', (organization_id,)).fetchone():
                raise ValueError('Сначала создайте организацию.')
            try:
                self.db.execute('INSERT INTO users (id,username,password_hash,display_name,active,created_at,organization_id) VALUES (?,?,?,?,1,?,?)',
                                (str(uuid4()), username, encoded, display_name, time.time(), organization_id))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Учётная запись с таким логином уже существует.') from exc

    def list_users(self):
        with self.lock:
            return [{**self.profile(row), 'active': bool(row['active'])}
                    for row in self.db.execute('SELECT * FROM users ORDER BY username')]

    def change_user(self, username, *, password=None, active=None):
        username = login_name(username)
        encoded = hash_password(password) if password is not None else None
        with self.lock, self.db:
            row = self.db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if not row:
                raise ValueError('Учётная запись не найдена.')
            if encoded is not None:
                self.db.execute('UPDATE users SET password_hash=? WHERE id=?', (encoded, row['id']))
            if active is not None:
                self.db.execute('UPDATE users SET active=? WHERE id=?', (int(active), row['id']))
            self.db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
            self.db.execute('DELETE FROM attempts WHERE username=?', (username,))

    def login(self, username, password, remote, old_token=None):
        username = username.strip().lower()
        now = time.time()
        with self.login_lock, self.lock, self.db:
            self.db.execute('DELETE FROM attempts WHERE attempted_at<?', (now-WINDOW,))
            self.db.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
            account_count = self.db.execute('SELECT count(*) FROM attempts WHERE username=?', (username,)).fetchone()[0]
            remote_count = self.db.execute('SELECT count(*) FROM attempts WHERE remote=?', (remote,)).fetchone()[0]
            if account_count >= 5 or remote_count >= 30:
                raise LoginFailure(throttled=True)
            row = self.db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            valid = verify_password(password, row['password_hash'] if row else self.dummy)
            if not valid or row is None or not row['active']:
                self.db.execute('INSERT INTO attempts VALUES (?,?,?)', (username, remote, now))
                # Commit failed attempts before raising; context-manager rollback
                # must not erase rate limits.
                self.db.commit()
                raise LoginFailure()
            self.db.execute('DELETE FROM attempts WHERE username=?', (username,))
            if old_token:
                self.db.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(old_token),))
            token = secrets.token_urlsafe(32)
            expires = time.time() + SESSION_SECONDS
            self.db.execute('INSERT INTO sessions VALUES (?,?,?)', (self.token_hash(token), row['id'], expires))
            return token, {**self.profile(row), 'expiresAt': expires}

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def current(self, token):
        if not token or len(token) > 128:
            return None
        with self.lock:
            row = self.db.execute('''SELECT users.*, sessions.expires_at FROM sessions JOIN users
                ON users.id=sessions.user_id WHERE token_hash=? AND expires_at>? AND active=1''',
                (self.token_hash(token), time.time())).fetchone()
            return {**self.profile(row), 'expiresAt': row['expires_at']} if row else None

    def logout(self, token):
        if token:
            with self.lock, self.db:
                self.db.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(token),))

    def create_organization(self, identifier, name, contact):
        if (not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,63}', identifier)
                or not name.strip() or len(name) > 200 or not contact.strip() or len(contact) > 256):
            raise ValueError('Укажите ID организации (3–64 латинских символа), название и контакт.')
        with self.lock, self.db:
            try:
                self.db.execute('INSERT INTO organizations VALUES (?,?,?)', (identifier, name.strip(), contact.strip()))
            except sqlite3.IntegrityError as exc:
                raise ValueError('ID организации уже существует.') from exc

    def create_api_key(self, organization_id, label, *, lifetime_days=30, scopes=None):
        if not label.strip() or len(label) > 120 or not 1 <= lifetime_days <= 90:
            raise ValueError('Укажите название ключа и срок от 1 до 90 дней.')
        scopes = ['predict'] if scopes is None else sorted(set(scopes))
        if not scopes or not set(scopes) <= API_SCOPES:
            raise ValueError('Права ключа: predict, reports и/или pdf; список не должен быть пустым.')
        with self.lock, self.db:
            if not self.db.execute('SELECT 1 FROM organizations WHERE id=?', (organization_id,)).fetchone():
                raise ValueError('Сначала создайте организацию.')
            token, identifier = 'hema_lab_' + secrets.token_urlsafe(32), str(uuid4())
            self.db.execute('INSERT INTO api_keys (id,token_hash,organization_id,label,active,expires_at,scopes) VALUES (?,?,?,?,1,?,?)',
                            (identifier, self.token_hash(token), organization_id, label.strip(),
                             time.time() + lifetime_days * 86400, json.dumps(scopes)))
            return identifier, token

    def api_key(self, token):
        if not token or len(token) > 128:
            return None
        with self.lock:
            row = self.db.execute('SELECT id,organization_id,expires_at,scopes FROM api_keys WHERE token_hash=? AND active=1 AND expires_at>?',
                                  (self.token_hash(token), time.time())).fetchone()
            if row is None:
                return None
            try:
                scopes = json.loads(row['scopes'])
                if not isinstance(scopes, list) or not scopes or any(scope not in API_SCOPES for scope in scopes):
                    return None
            except (ValueError, TypeError):
                return None
            return {**dict(row), 'scopes': scopes}

    def revoke_api_key(self, identifier):
        with self.lock, self.db:
            if not self.db.execute('UPDATE api_keys SET active=0 WHERE id=?', (identifier,)).rowcount:
                raise ValueError('Ключ не найден.')

    def list_api_keys(self):
        with self.lock:
            return [dict(row) for row in self.db.execute('SELECT id,organization_id,label,active,expires_at,scopes FROM api_keys')]

    def close(self):
        with self.lock:
            self.db.close()
