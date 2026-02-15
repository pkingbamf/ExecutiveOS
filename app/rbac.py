from __future__ import annotations

from app.models import User


def can_view_all(user: User) -> bool:
    return user.role in {"ADMIN", "EXEC"}


def can_edit_owned_or_admin(user: User, owner_user_id: int) -> bool:
    return user.role == "ADMIN" or user.id == owner_user_id


def can_manage_users(user: User) -> bool:
    return user.role == "ADMIN"


def can_create_records(user: User) -> bool:
    return user.role in {"ADMIN", "EXEC", "MEMBER"}
