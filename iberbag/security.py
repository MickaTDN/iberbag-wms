"""Authentication helpers (no demo/shared default credentials)."""
import hashlib, hmac, secrets
from datetime import timedelta
from sqlalchemy import select, func, delete
from .database import User, LoginSession, FailedLogin, now_utc

ITERATIONS = 450_000
SESSION_HOURS = 8

def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), ITERATIONS).hex()

def set_password(user: User, password: str):
    if len(password) < 12 or len(password) > 200:
        raise ValueError('A palavra-passe deve ter entre 12 e 200 caracteres.')
    user.password_salt = secrets.token_hex(24)
    user.password_hash = password_hash(password, user.password_salt)

def verify_password(user: User | None, password: str) -> bool:
    if user is None:
        # Prevent basic username enumeration through large timing differences.
        password_hash(password, '00' * 24)
        return False
    return hmac.compare_digest(password_hash(password, user.password_salt), user.password_hash)

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def new_session(db, user: User):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.add(LoginSession(token_digest=token_hash(token), csrf_token=csrf,
                        user_id=user.id, expires_at=now_utc() + timedelta(hours=SESSION_HOURS)))
    return token

def get_session(db, token: str | None):
    if not token or len(token) > 150:
        return None
    found = db.scalar(select(LoginSession).where(LoginSession.token_digest == token_hash(token)))
    if found is None or found.expires_at <= now_utc() or not found.user.active:
        return None
    return found

def too_many_failures(db, username: str, remote_ip: str) -> bool:
    since = now_utc() - timedelta(minutes=15)
    # Limit attempts both by IP and username.
    n = db.scalar(select(func.count()).select_from(FailedLogin).where(FailedLogin.occurred_at >= since,
                      (FailedLogin.username == username) | (FailedLogin.remote_ip == remote_ip))) or 0
    return n >= 8

def log_failed_login(db, username: str, remote_ip: str):
    db.add(FailedLogin(username=username[:60], remote_ip=remote_ip[:64]))

def clear_old_attempts(db):
    db.execute(delete(FailedLogin).where(FailedLogin.occurred_at < now_utc() - timedelta(days=2)))
