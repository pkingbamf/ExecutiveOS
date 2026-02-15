import io
from urllib.parse import urlencode

import app.db as db
from app.auth import create_session, create_user
from app.main import app


def _call(path, method="GET", body=None, cookie=None, query=""):
    status_holder = {}

    def start_response(status, headers):
        status_holder["status"] = status
        status_holder["headers"] = headers

    raw = urlencode(body or {}).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    body_text = b"".join(app(environ, start_response)).decode()
    return status_holder["status"], dict(status_holder["headers"]), body_text


def _seed(tmp_path):
    db.DB_PATH = tmp_path / "db.sqlite"
    db.migrate()
    create_user("Admin", "admin@test.com", "pw", "ADMIN")
    create_user("Exec", "exec@test.com", "pw", "EXEC")
    create_user("Member", "member@test.com", "pw", "MEMBER")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects(title,owner_user_id) VALUES('P1',2)")
        conn.execute("INSERT INTO decisions(project_id,title,decision_type,status,owner_user_id) VALUES(1,'D1','STRATEGIC','PROPOSED',2)")
        conn.execute("INSERT INTO action_items(project_id,decision_id,title,owner_user_id,status) VALUES(1,1,'A1',3,'OPEN')")
        conn.execute("INSERT INTO risk_issues(project_id,type,title,probability,impact,status,owner_user_id) VALUES(1,'RISK','R1',3,4,'OPEN',2)")
        conn.execute("INSERT INTO stakeholders(name) VALUES('S1')")


def test_project_view_and_edit_permissions(tmp_path):
    _seed(tmp_path)
    exec_cookie = f"session={create_session(2)}"
    member_cookie = f"session={create_session(3)}"

    status, _, _ = _call("/projects/1", cookie=exec_cookie)
    assert status.startswith("200")
    status, _, _ = _call("/projects/1/edit", cookie=exec_cookie)
    assert status.startswith("200")
    status, headers, _ = _call("/projects/1/edit", method="POST", cookie=exec_cookie, body={"title": "P1-upd", "status": "IN_PROGRESS", "priority": "P1", "owner_user_id": "2"})
    assert status.startswith("302") and headers["Location"].startswith("/projects/1")

    status, _, _ = _call("/projects/1/edit", cookie=member_cookie)
    assert status.startswith("403")


def test_decision_view_edit_and_deleted_404(tmp_path):
    _seed(tmp_path)
    exec_cookie = f"session={create_session(2)}"

    status, _, _ = _call("/decisions/1", cookie=exec_cookie)
    assert status.startswith("200")
    status, _, _ = _call("/decisions/1/edit", cookie=exec_cookie)
    assert status.startswith("200")
    status, headers, _ = _call("/decisions/1/edit", method="POST", cookie=exec_cookie, body={"title": "D1-upd", "decision_type": "FINANCIAL", "owner_user_id": "2", "impact_level": "HIGH"})
    assert status.startswith("302") and headers["Location"].startswith("/decisions/1")

    with db.get_conn() as conn:
        conn.execute("UPDATE decisions SET deleted_at=CURRENT_TIMESTAMP WHERE id=1")
    status, _, _ = _call("/decisions/1", cookie=exec_cookie)
    assert status.startswith("404")


def test_action_view_edit_permissions(tmp_path):
    _seed(tmp_path)
    member_cookie = f"session={create_session(3)}"
    exec_cookie = f"session={create_session(2)}"

    status, _, _ = _call("/actions/1", cookie=member_cookie)
    assert status.startswith("200")
    status, _, _ = _call("/actions/1/edit", cookie=member_cookie)
    assert status.startswith("200")
    status, headers, _ = _call("/actions/1/edit", method="POST", cookie=member_cookie, body={"title": "A1-upd", "owner_user_id": "3", "status": "IN_PROGRESS"})
    assert status.startswith("302") and headers["Location"].startswith("/actions/1")

    status, _, _ = _call("/actions/1/edit", cookie=exec_cookie)
    assert status.startswith("403")


def test_risk_and_stakeholder_view_edit(tmp_path):
    _seed(tmp_path)
    exec_cookie = f"session={create_session(2)}"
    admin_cookie = f"session={create_session(1)}"
    member_cookie = f"session={create_session(3)}"

    status, _, _ = _call("/risks/1", cookie=exec_cookie)
    assert status.startswith("200")
    status, _, _ = _call("/risks/1/edit", cookie=exec_cookie)
    assert status.startswith("200")
    status, headers, _ = _call("/risks/1/edit", method="POST", cookie=exec_cookie, body={"project_id": "1", "type": "ISSUE", "title": "R1-upd", "probability": "5", "impact": "2", "status": "MITIGATED", "owner_user_id": "2"})
    assert status.startswith("302") and headers["Location"].startswith("/risks/1")

    status, _, _ = _call("/stakeholders/1", cookie=exec_cookie)
    assert status.startswith("200")
    status, _, _ = _call("/stakeholders/1/edit", cookie=exec_cookie)
    assert status.startswith("200")
    status, headers, _ = _call("/stakeholders/1/edit", method="POST", cookie=exec_cookie, body={"name": "S1-upd", "influence_level": "HIGH", "stance": "SUPPORTIVE"})
    assert status.startswith("302")

    status, _, _ = _call("/stakeholders/1", cookie=member_cookie)
    assert status.startswith("403")
    status, _, _ = _call("/stakeholders/1/edit", cookie=member_cookie)
    assert status.startswith("403")

    status, _, _ = _call("/stakeholders/1/delete", method="POST", cookie=admin_cookie)
    assert status.startswith("302")


def test_admin_user_view_edit_route(tmp_path):
    _seed(tmp_path)
    admin_cookie = f"session={create_session(1)}"
    status, _, _ = _call("/admin/users/2", cookie=admin_cookie)
    assert status.startswith("200")
    status, _, _ = _call("/admin/users/2/edit", cookie=admin_cookie)
    assert status.startswith("200")
    status, headers, _ = _call("/admin/users/2/edit", method="POST", cookie=admin_cookie, body={"name": "Exec Renamed", "role": "EXEC", "is_active": "1"})
    assert status.startswith("302") and headers["Location"].startswith("/admin/users/2")
