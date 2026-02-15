from __future__ import annotations

import hashlib
import secrets

from app.db import get_conn
from app.models import User


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def create_user(name: str, email: str, password: str, role: str = "MEMBER"):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)",
            (name, email.lower().strip(), hash_password(password), role),
        )


def authenticate(email: str, password: str) -> User | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id,name,email,role,password_hash FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    if not row or row["password_hash"] != hash_password(password):
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
