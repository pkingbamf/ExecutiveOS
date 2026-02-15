from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from app.db import get_conn
from app.models import User


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith("pbkdf2_sha256$"):
        _, salt_hex, digest_hex = stored_hash.split("$", 2)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
        return hmac.compare_digest(actual, expected)
    # legacy fallback
    legacy = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(legacy, stored_hash)


def create_user(name: str, email: str, password: str, role: str = "MEMBER"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
            (name.strip(), email.lower().strip(), hash_password(password), role),
        )


def authenticate(email: str, password: str) -> User | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id,name,email,role,password_hash FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return User(id=row["id"], name=row["name"], email=row["email"], role=row["role"])


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with get_conn() as conn:
        conn.execute("INSERT INTO sessions(token,user_id) VALUES(?,?)", (token, user_id))
    return token


def get_user_by_session(token: str | None) -> User | None:
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.id,u.name,u.email,u.role
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
    if not row:
        return None
    return User(id=row["id"], name=row["name"], email=row["email"], role=row["role"])


def destroy_session(token: str | None):
    if not token:
        return
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
