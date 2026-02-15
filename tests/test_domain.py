from app.models import compute_risk_score, validate_decision_transition
from app.models import User
from app.rbac import can_create_records, can_edit_owned_or_admin, can_manage_users, can_view_all


def test_decision_status_transitions():
    ok, _ = validate_decision_transition("PROPOSED", "DECIDED", "approved", "2026-02-10")
    assert ok
    ok, msg = validate_decision_transition("PROPOSED", "DECIDED", "", None)
    assert not ok and "requires" in msg
    ok, _ = validate_decision_transition("CANCELLED", "DECIDED", "x", "2026-02-10")
    assert not ok


def test_risk_score_computation():
    assert compute_risk_score(1, 1) == 1
    assert compute_risk_score(4, 5) == 20


def test_rbac_access_checks():
    admin = User(id=1, name="A", email="a@a", role="ADMIN")
    exec_user = User(id=2, name="E", email="e@e", role="EXEC")
    member = User(id=3, name="M", email="m@m", role="MEMBER")

    assert can_manage_users(admin)
    assert not can_manage_users(exec_user)
    assert can_view_all(exec_user)
    assert not can_view_all(member)
    assert can_edit_owned_or_admin(admin, owner_user_id=99)
    assert can_edit_owned_or_admin(member, owner_user_id=3)
    assert not can_edit_owned_or_admin(member, owner_user_id=2)
    assert can_create_records(member)
