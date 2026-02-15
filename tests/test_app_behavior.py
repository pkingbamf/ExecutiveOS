import io
from urllib.parse import urlencode

import app.db as db
from app.auth import create_session, create_user
from app.main import app
from app.models import dashboard_open_decision_count


def _call(path, method="GET", body=None, cookie=None):
    status_holder = {}

    def start_response(status, headers):
        status_holder["status"] = status
        status_holder["headers"] = headers

    raw = urlencode(body or {}).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    result = b"".join(app(environ, start_response)).decode()
    return status_holder["status"], dict(status_holder["headers"]), result


def test_decision_mark_decided_requires_fields(tmp_path):
    db.DB_PATH = tmp_path / "t.db"
    db.migrate()
    create_user("Admin", "admin@test.com", "pw", "ADMIN")
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO decisions(title,decision_type,status,owner_user_id) VALUES('D1','STRATEGIC','PROPOSED',1)"
        )
    token = create_session(1)
    status, _, body = _call(
        "/decisions/1/status",
        method="POST",
        body={"status": "DECIDED", "decision_outcome": "", "decision_date": ""},
        cookie=f"session={token}",
    )
    assert status.startswith("400")
    assert "requires decision_outcome and decision_date" in body


def test_dashboard_open_decisions_excludes_deleted_and_closed(tmp_path):
    db.DB_PATH = tmp_path / "t2.db"
    db.migrate()
    create_user("Admin", "admin2@test.com", "pw", "ADMIN")
    with db.get_conn() as conn:
        conn.execute("INSERT INTO decisions(title,decision_type,status,owner_user_id) VALUES('a','STRATEGIC','PROPOSED',1)")
        conn.execute("INSERT INTO decisions(title,decision_type,status,owner_user_id) VALUES('b','STRATEGIC','REVISIT',1)")
        conn.execute("INSERT INTO decisions(title,decision_type,status,owner_user_id) VALUES('c','STRATEGIC','DECIDED',1)")
        conn.execute("INSERT INTO decisions(title,decision_type,status,owner_user_id) VALUES('d','STRATEGIC','CANCELLED',1)")
        conn.execute("INSERT INTO decisions(title,decision_type,status,owner_user_id,deleted_at) VALUES('e','STRATEGIC','PROPOSED',1,CURRENT_TIMESTAMP)")
        assert dashboard_open_decision_count(conn) == 2
