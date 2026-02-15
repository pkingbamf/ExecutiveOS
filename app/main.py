from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parent.parent))

import html
import json
from datetime import date
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from app.auth import authenticate, create_session, create_user, destroy_session, get_user_by_session, update_password
from app.db import get_conn, migrate
from app.models import dashboard_open_decision_count, validate_decision_transition
from app.rbac import can_edit_owned_or_admin, can_manage_users, can_view_all

PROJECT_STATUS = {"NOT_STARTED", "IN_PROGRESS", "AT_RISK", "BLOCKED", "DONE"}
PROJECT_PRIORITY = {"P0", "P1", "P2", "P3"}
DECISION_TYPES = {"STRATEGIC", "FINANCIAL", "OPERATIONAL", "GOVERNANCE"}
DECISION_STATUS = {"PROPOSED", "DECIDED", "REVISIT", "CANCELLED"}
ACTION_STATUS = {"OPEN", "IN_PROGRESS", "DONE", "CANCELLED"}
RISK_TYPES = {"RISK", "ISSUE"}
RISK_STATUS = {"OPEN", "MITIGATED", "CLOSED"}
IMPACT_LEVELS = {"LOW", "MED", "HIGH"}
STANCE = {"SUPPORTIVE", "NEUTRAL", "RESISTANT"}


def parse_form(environ):
    try:
        size = int(environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        size = 0
    body = environ["wsgi.input"].read(size).decode("utf-8")
    return {k: v[0] for k, v in parse_qs(body).items()}


def get_cookie(environ, key):
    for part in environ.get("HTTP_COOKIE", "").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k == key:
                return v
    return None


def redirect(start_response, location):
    start_response("302 Found", [("Location", location)])
    return [b""]


def respond(start_response, content: str, status="200 OK", headers=None):
    h = [("Content-Type", "text/html; charset=utf-8")]
    if headers:
        h.extend(headers)
    start_response(status, h)
    return [content.encode("utf-8")]


def safe_choice(value: str, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def can_delete(user, owner_user_id=None) -> bool:
    if user.role == "ADMIN":
        return True
    return user.role == "EXEC" and owner_user_id is not None and user.id == owner_user_id


def row_action_delete(path: str, typed: bool = False):
    if typed:
        return f"<form method='POST' action='{path}' onsubmit=\"var v=prompt('Type DELETE to confirm');if(v!=='DELETE')return false;\"><button>Delete</button></form>"
    return f"<form method='POST' action='{path}' onsubmit=\"return confirm('Delete this item?')\"><button>Delete</button></form>"


def toast_message(environ):
    t = parse_qs(environ.get("QUERY_STRING", "")).get("toast", [""])[0]
    return f"<div class='toast'>{html.escape(t)}</div>" if t else ""


def layout(user, title: str, body: str, toast: str = ""):
    nav = ""
    if user:
        nav = """
        <aside class='sidebar'>
          <h2>ExecutiveOS</h2>
          <a href='/dashboard'>Dashboard</a>
          <a href='/projects'>Projects</a>
          <a href='/decisions'>Decisions</a>
          <a href='/actions'>Action Items</a>
          <a href='/risks'>Risks & Issues</a>
          <a href='/stakeholders'>Stakeholders</a>
          <a href='/admin/users'>Users</a>
          <a href='/logout'>Logout</a>
        </aside>
        """
    search = ""
    if user:
        search = f"<header class='topbar'><form action='/search' method='GET'><input name='q' placeholder='Search projects, decisions, actions'><button>Search</button></form><div class='pill'>{html.escape(user.name)} · {html.escape(user.role)}</div></header>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>
    body{{margin:0;font-family:Inter,Arial;background:#f3f5f9;color:#111827}}.app{{display:flex;min-height:100vh}}.sidebar{{width:220px;background:#0f172a;padding:18px;display:flex;flex-direction:column;gap:8px}}
    .sidebar a{{color:#cbd5e1;text-decoration:none;padding:8px;border-radius:8px}}.sidebar a:hover{{background:#1e293b}}.main{{flex:1;padding:18px}}.topbar{{display:flex;justify-content:space-between;gap:12px;margin-bottom:12px}}
    .topbar form{{display:flex;gap:8px;flex:1}}.card{{background:#fff;padding:14px;border:1px solid #e5e7eb;border-radius:12px;margin-bottom:12px}} table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid #e5e7eb;padding:8px;text-align:left}}
    input,select,textarea{{width:100%;padding:9px;border:1px solid #e5e7eb;border-radius:8px}}button{{padding:8px 12px;border:none;background:#1d4ed8;color:#fff;border-radius:8px;cursor:pointer}}form.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}
    .kpis{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.kpi{{background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:10px}} .pill{{background:#fff;border:1px solid #e5e7eb;padding:8px 10px;border-radius:999px}}
    .toast{{background:#dcfce7;border:1px solid #86efac;padding:10px;border-radius:8px;margin-bottom:10px}}
    </style></head><body><div class='app'>{nav}<main class='main'>{search}{toast}<h1>{html.escape(title)}</h1>{body}</main></div></body></html>"""


def must_change_password(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT must_change_password FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row and row["must_change_password"] == 1)


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    user = get_user_by_session(get_cookie(environ, "session"))

    if path == "/":
        return redirect(start_response, "/dashboard" if user else "/login")

    if path == "/signup":
        if method == "POST":
            form = parse_form(environ)
            create_user(form.get("name", ""), form.get("email", ""), form.get("password", ""), "MEMBER")
            return redirect(start_response, "/login")
        return respond(start_response, layout(user, "Sign up", "<div class='card'><form method='POST'><input name='name' required><input type='email' name='email' required><input type='password' name='password' required><button>Create account</button></form></div>"))

    if path == "/login":
        if method == "POST":
            form = parse_form(environ)
            auth_user = authenticate(form.get("email", ""), form.get("password", ""))
            if not auth_user:
                return respond(start_response, layout(user, "Login", "<div class='card'>Invalid credentials or inactive account.</div>"), "401 Unauthorized")
            token = create_session(auth_user.id)
            target = "/change-password" if must_change_password(auth_user.id) else "/dashboard"
            start_response("302 Found", [("Location", target), ("Set-Cookie", f"session={token}; HttpOnly; Path=/")])
            return [b""]
        return respond(start_response, layout(user, "Login", "<div class='card'><form method='POST'><input type='email' name='email' required><input type='password' name='password' required><button>Login</button></form><a href='/signup'>Sign up</a></div>"))

    if path == "/logout":
        destroy_session(get_cookie(environ, "session"))
        start_response("302 Found", [("Location", "/login"), ("Set-Cookie", "session=; Max-Age=0; Path=/")])
        return [b""]

    if not user:
        return redirect(start_response, "/login")

    if must_change_password(user.id) and path != "/change-password":
        return redirect(start_response, "/change-password")

    if path == "/change-password":
        if method == "POST":
            form = parse_form(environ)
            if not form.get("new_password"):
                return respond(start_response, layout(user, "Change password", "<div class='card'>Password required.</div>"), "400 Bad Request")
            update_password(user.id, form["new_password"], must_change_password=False)
            return redirect(start_response, "/dashboard?toast=Password%20updated")
        return respond(start_response, layout(user, "Change password", "<div class='card'><p>You must change your password.</p><form method='POST'><input type='password' name='new_password' required><button>Update password</button></form></div>"))

    if path == "/dashboard":
        with get_conn() as conn:
            statuses = conn.execute("SELECT status,COUNT(*) c FROM projects WHERE deleted_at IS NULL GROUP BY status").fetchall()
            open_decisions = dashboard_open_decision_count(conn)
            overdue = conn.execute("SELECT COUNT(*) c FROM action_items WHERE deleted_at IS NULL AND status!='DONE' AND due_date < date('now')").fetchone()["c"]
            top_risks = conn.execute("SELECT title,type,probability*impact score FROM risk_issues WHERE deleted_at IS NULL ORDER BY score DESC LIMIT 5").fetchall()
            decided_7 = conn.execute("SELECT COUNT(*) c FROM decisions WHERE deleted_at IS NULL AND status='DECIDED' AND decision_date >= date('now','-7 day')").fetchone()["c"]
            decided_30 = conn.execute("SELECT COUNT(*) c FROM decisions WHERE deleted_at IS NULL AND status='DECIDED' AND decision_date >= date('now','-30 day')").fetchone()["c"]
            recent_decided = conn.execute("SELECT d.title,d.decision_date,COALESCE(p.title,'Org-level') project_title FROM decisions d LEFT JOIN projects p ON p.id=d.project_id WHERE d.deleted_at IS NULL AND d.status='DECIDED' ORDER BY d.decision_date DESC LIMIT 5").fetchall()
        kpi = ''.join([f"<div class='kpi'><b>{r['status']}</b><br>{r['c']} projects</div>" for r in statuses])
        kpi += f"<div class='kpi'><b>Open decisions</b><br>{open_decisions}</div><div class='kpi'><b>Overdue actions</b><br>{overdue}</div><div class='kpi'><b>Decided (7d / 30d)</b><br>{decided_7} / {decided_30}</div>"
        risks = ''.join([f"<tr><td>{html.escape(r['title'])}</td><td>{r['type']}</td><td>{r['score']}</td></tr>" for r in top_risks]) or "<tr><td colspan='3'>No risks or issues.</td></tr>"
        decided_rows = ''.join([f"<li>{html.escape(d['title'])} · {html.escape(d['project_title'])} · {d['decision_date']}</li>" for d in recent_decided]) or "<li>No recent decided decisions.</li>"
        body = f"<div class='kpis'>{kpi}</div><div class='card'><h3>Top risks/issues</h3><table><tr><th>Title</th><th>Type</th><th>Score</th></tr>{risks}</table></div><div class='card'><h3>Recent decisions made</h3><ul>{decided_rows}</ul></div>"
        return respond(start_response, layout(user, "Executive Dashboard", body, toast_message(environ)))

    if path == "/projects":
        if method == "POST":
            form = parse_form(environ)
            owner = user.id if user.role == "MEMBER" else int(form.get("owner_user_id", user.id))
            with get_conn() as conn:
                conn.execute("INSERT INTO projects(title,short_description,status,priority,owner_user_id,sponsor_name,start_date,target_date,tags) VALUES(?,?,?,?,?,?,?,?,?)", (
                    form.get("title", ""), form.get("short_description", ""), safe_choice(form.get("status", "NOT_STARTED"), PROJECT_STATUS, "NOT_STARTED"), safe_choice(form.get("priority", "P2"), PROJECT_PRIORITY, "P2"), owner, form.get("sponsor_name", ""), form.get("start_date") or None, form.get("target_date") or None, json.dumps([t.strip() for t in form.get("tags", "").split(",") if t.strip()]),
                ))
        with get_conn() as conn:
            rows = conn.execute("SELECT p.*,u.name owner_name FROM projects p JOIN users u ON u.id=p.owner_user_id WHERE p.deleted_at IS NULL ORDER BY p.updated_at DESC").fetchall()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1 ORDER BY name").fetchall()
        if not can_view_all(user):
            rows = [r for r in rows if r["owner_user_id"] == user.id]
        project_rows = []
        for r in rows:
            action = row_action_delete(f"/projects/{r['id']}/delete", True) if can_delete(user, r['owner_user_id']) else '-'
            project_rows.append(f"<tr><td><a href='/projects/{r['id']}'>{html.escape(r['title'])}</a></td><td>{r['status']}</td><td>{r['priority']}</td><td>{html.escape(r['owner_name'])}</td><td>{action}</td></tr>")
        table = ''.join(project_rows) or "<tr><td colspan='5'>No projects found.</td></tr>"
        owners = ''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        body = f"<div class='card'><table><tr><th>Title</th><th>Status</th><th>Priority</th><th>Owner</th><th>Actions</th></tr>{table}</table></div><div class='card'><h3>New project</h3><form method='POST' class='grid'><input name='title' required><input name='short_description'><select name='status'><option>NOT_STARTED</option><option>IN_PROGRESS</option><option>AT_RISK</option><option>BLOCKED</option><option>DONE</option></select><select name='priority'><option>P0</option><option>P1</option><option>P2</option><option>P3</option></select><select name='owner_user_id'>{owners}</select><input name='sponsor_name'><input type='date' name='start_date'><input type='date' name='target_date'><input name='tags' placeholder='comma tags'><button>Create</button></form></div>"
        return respond(start_response, layout(user, "Projects", body, toast_message(environ)))

    if path.startswith("/projects/") and path.endswith("/delete") and method == "POST":
        pid = int(path.split("/")[2])
        with get_conn() as conn:
            p = conn.execute("SELECT id,owner_user_id FROM projects WHERE id=? AND deleted_at IS NULL", (pid,)).fetchone()
            if not p or not can_delete(user, p["owner_user_id"]):
                return respond(start_response, layout(user, "Forbidden", "<div class='card'>Not allowed.</div>"), "403 Forbidden")
            conn.execute("UPDATE projects SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (pid,))
            conn.execute("UPDATE decisions SET deleted_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
            conn.execute("UPDATE action_items SET deleted_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
            conn.execute("UPDATE risk_issues SET deleted_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
            conn.execute("UPDATE project_stakeholders SET deleted_at=CURRENT_TIMESTAMP WHERE project_id=?", (pid,))
        return redirect(start_response, "/projects?toast=Project%20deleted")

    if path.startswith("/projects/"):
        pid = int(path.split("/")[-1])
        with get_conn() as conn:
            p = conn.execute("SELECT p.*,u.name owner_name FROM projects p JOIN users u ON u.id=p.owner_user_id WHERE p.id=? AND p.deleted_at IS NULL", (pid,)).fetchone()
            if not p:
                return respond(start_response, layout(user, "Not found", "<div class='card'>This item was deleted or not found.</div>"), "404 Not Found")
            if not can_view_all(user) and p["owner_user_id"] != user.id:
                return respond(start_response, layout(user, "Forbidden", "<div class='card'>Forbidden.</div>"), "403 Forbidden")
            decisions = conn.execute("SELECT title,status FROM decisions WHERE project_id=? AND deleted_at IS NULL", (pid,)).fetchall()
            actions = conn.execute("SELECT title,status,due_date FROM action_items WHERE project_id=? AND deleted_at IS NULL", (pid,)).fetchall()
            risks = conn.execute("SELECT title,probability*impact score,status FROM risk_issues WHERE project_id=? AND deleted_at IS NULL ORDER BY score DESC", (pid,)).fetchall()
        delete_ui = row_action_delete(f"/projects/{pid}/delete", True) if can_delete(user, p["owner_user_id"]) else ""
        body = f"<div class='card'><p>{html.escape(p['short_description'])}</p><p>Status {p['status']} · Priority {p['priority']} · Owner {html.escape(p['owner_name'])}</p>{delete_ui}</div>"
        decisions_html = ''.join([f"<li>{html.escape(d['title'])} ({d['status']})</li>" for d in decisions]) or '<li>No linked decisions.</li>'
        actions_html = ''.join([f"<li>{html.escape(a['title'])} [{a['status']}] {a['due_date'] or ''}</li>" for a in actions]) or '<li>No linked actions.</li>'
        risks_html = ''.join([f"<li>{html.escape(r['title'])} score {r['score']}</li>" for r in risks]) or '<li>No linked risks/issues.</li>'
        body += f"<div class='card'><h4>Decisions</h4><ul>{decisions_html}</ul></div>"
        body += f"<div class='card'><h4>Actions</h4><ul>{actions_html}</ul></div>"
        body += f"<div class='card'><h4>Risks</h4><ul>{risks_html}</ul></div>"
        return respond(start_response, layout(user, f"Project: {p['title']}", body, toast_message(environ)))

    if path == "/decisions":
        if method == "POST":
            form = parse_form(environ)
            owner = user.id if user.role == "MEMBER" else int(form.get("owner_user_id", user.id))
            with get_conn() as conn:
                conn.execute("INSERT INTO decisions(project_id,title,decision_type,status,owner_user_id,approver,rationale,options_considered,due_date,impact_level) VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    form.get("project_id") or None, form.get("title", ""), safe_choice(form.get("decision_type", "STRATEGIC"), DECISION_TYPES, "STRATEGIC"), safe_choice(form.get("status", "PROPOSED"), DECISION_STATUS, "PROPOSED"), owner, form.get("approver", ""), form.get("rationale", ""), form.get("options_considered", ""), form.get("due_date") or None, safe_choice(form.get("impact_level", "MED"), IMPACT_LEVELS, "MED"),
                ))
        with get_conn() as conn:
            rows = conn.execute("SELECT d.*,u.name owner_name,COALESCE(p.title,'Org-level') project_title FROM decisions d JOIN users u ON u.id=d.owner_user_id LEFT JOIN projects p ON p.id=d.project_id WHERE d.deleted_at IS NULL ORDER BY d.updated_at DESC").fetchall()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1 ORDER BY name").fetchall(); projects = conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL ORDER BY title").fetchall()
        if user.role == "MEMBER":
            rows = [r for r in rows if r["owner_user_id"] == user.id]
        def close_button(d):
            if not can_edit_owned_or_admin(user, d['owner_user_id']):
                return ""
            return f"<form method='POST' action='/decisions/{d['id']}/status' onsubmit=\"var o=prompt('Decision outcome (required)');if(!o)return false;var dt=prompt('Decision date YYYY-MM-DD','{date.today()}');if(!dt)return false;this.decision_outcome.value=o;this.decision_date.value=dt;return true;\"><input type='hidden' name='status' value='DECIDED'><input type='hidden' name='decision_outcome' value=''><input type='hidden' name='decision_date' value=''><button>Mark DECIDED</button></form>"
        decision_rows=[]
        for d in rows:
            del_action = row_action_delete(f"/decisions/{d['id']}/delete", True) if can_delete(user, d['owner_user_id']) else ''
            decision_rows.append(f"<tr><td>{html.escape(d['title'])}</td><td>{d['status']}</td><td>{html.escape(d['owner_name'])}</td><td>{html.escape(d['project_title'])}</td><td>{close_button(d)} {del_action}</td></tr>")
        table=''.join(decision_rows) or "<tr><td colspan='5'>No decisions found.</td></tr>"
        uopts=''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users]); popts=''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects])
        body = f"<div class='card'><table><tr><th>Decision</th><th>Status</th><th>Owner</th><th>Project</th><th>Actions</th></tr>{table}</table></div><div class='card'><h3>New decision</h3><form method='POST' class='grid'><input name='title' required><select name='project_id'><option value=''>Org-level</option>{popts}</select><select name='decision_type'><option>STRATEGIC</option><option>FINANCIAL</option><option>OPERATIONAL</option><option>GOVERNANCE</option></select><select name='status'><option>PROPOSED</option><option>REVISIT</option></select><select name='owner_user_id'>{uopts}</select><input name='approver'><input type='date' name='due_date'><select name='impact_level'><option>LOW</option><option>MED</option><option>HIGH</option></select><textarea name='rationale'></textarea><textarea name='options_considered'></textarea><button>Create</button></form></div>"
        return respond(start_response, layout(user, "Decisions", body, toast_message(environ)))

    if path.startswith('/decisions/') and path.endswith('/status') and method == 'POST':
        did = int(path.split('/')[2]); form=parse_form(environ)
        with get_conn() as conn:
            d=conn.execute("SELECT * FROM decisions WHERE id=? AND deleted_at IS NULL", (did,)).fetchone()
            if not d or not can_edit_owned_or_admin(user,d['owner_user_id']):
                return respond(start_response, layout(user, "Forbidden", "<div class='card'>Not allowed.</div>"), "403 Forbidden")
            ok,msg = validate_decision_transition(d['status'], form.get('status',''), form.get('decision_outcome',''), form.get('decision_date'))
            if not ok:
                return respond(start_response, layout(user, "Invalid", f"<div class='card'>{html.escape(msg)}</div>"), "400 Bad Request")
            conn.execute("UPDATE decisions SET status=?,decision_date=?,decision_outcome=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (form.get('status'), form.get('decision_date') or d['decision_date'], form.get('decision_outcome') or d['decision_outcome'], did))
        return redirect(start_response, "/decisions?toast=Decision%20marked%20as%20DECIDED")

    if path.startswith('/decisions/') and path.endswith('/delete') and method == 'POST':
        did = int(path.split('/')[2])
        with get_conn() as conn:
            d = conn.execute("SELECT owner_user_id FROM decisions WHERE id=? AND deleted_at IS NULL", (did,)).fetchone()
            if not d or not can_delete(user, d['owner_user_id']):
                return respond(start_response, layout(user, "Forbidden", "<div class='card'>Not allowed.</div>"), "403 Forbidden")
            conn.execute("UPDATE decisions SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (did,))
            conn.execute("UPDATE action_items SET deleted_at=CURRENT_TIMESTAMP WHERE decision_id=?", (did,))
        return redirect(start_response, "/decisions?toast=Decision%20deleted")

    if path == '/actions':
        if method == 'POST':
            form=parse_form(environ)
            owner=user.id if user.role=='MEMBER' else int(form.get('owner_user_id',user.id))
            with get_conn() as conn:
                conn.execute("INSERT INTO action_items(project_id,decision_id,title,owner_user_id,status,due_date,notes) VALUES(?,?,?,?,?,?,?)", (form.get('project_id') or None, form.get('decision_id') or None, form.get('title',''), owner, safe_choice(form.get('status','OPEN'),ACTION_STATUS,'OPEN'), form.get('due_date') or None, form.get('notes','')))
        with get_conn() as conn:
            rows=conn.execute("SELECT a.*,u.name owner_name,COALESCE(p.title,'-') project_title FROM action_items a JOIN users u ON u.id=a.owner_user_id LEFT JOIN projects p ON p.id=a.project_id WHERE a.deleted_at IS NULL ORDER BY a.status,a.due_date").fetchall(); users=conn.execute("SELECT id,name FROM users WHERE is_active=1").fetchall(); projects=conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL").fetchall(); decisions=conn.execute("SELECT id,title FROM decisions WHERE deleted_at IS NULL").fetchall()
        if user.role=='MEMBER': rows=[r for r in rows if r['owner_user_id']==user.id]
        action_rows=[]
        for r in rows:
            del_action = row_action_delete(f"/actions/{r['id']}/delete") if can_delete(user, r['owner_user_id']) else '-'
            action_rows.append(f"<tr><td>{html.escape(r['title'])}</td><td>{r['status']}</td><td>{html.escape(r['owner_name'])}</td><td>{r['due_date'] or '-'}</td><td>{html.escape(r['project_title'])}</td><td>{del_action}</td></tr>")
        table=''.join(action_rows) or "<tr><td colspan='6'>No action items found.</td></tr>"
        uopts=''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users]); popts=''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects]); dopts=''.join([f"<option value='{d['id']}'>{html.escape(d['title'])}</option>" for d in decisions])
        body=f"<div class='card'><table><tr><th>Title</th><th>Status</th><th>Owner</th><th>Due</th><th>Project</th><th>Actions</th></tr>{table}</table></div><div class='card'><h3>New action</h3><form method='POST' class='grid'><input name='title' required><select name='owner_user_id'>{uopts}</select><select name='project_id'><option value=''>None</option>{popts}</select><select name='decision_id'><option value=''>None</option>{dopts}</select><select name='status'><option>OPEN</option><option>IN_PROGRESS</option><option>DONE</option><option>CANCELLED</option></select><input type='date' name='due_date'><textarea name='notes'></textarea><button>Create</button></form></div>"
        return respond(start_response, layout(user,'Action Items',body,toast_message(environ)))

    if path.startswith('/actions/') and path.endswith('/delete') and method=='POST':
        aid=int(path.split('/')[2])
        with get_conn() as conn:
            a=conn.execute("SELECT owner_user_id FROM action_items WHERE id=? AND deleted_at IS NULL", (aid,)).fetchone()
            if not a or not can_delete(user,a['owner_user_id']):
                return respond(start_response, layout(user,'Forbidden',"<div class='card'>Not allowed.</div>"),'403 Forbidden')
            conn.execute("UPDATE action_items SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (aid,))
        return redirect(start_response, '/actions?toast=Action%20item%20deleted')

    if path == '/risks':
        if method=='POST':
            form=parse_form(environ)
            with get_conn() as conn:
                conn.execute("INSERT INTO risk_issues(project_id,type,title,description,probability,impact,status,owner_user_id,mitigation_plan,due_date) VALUES(?,?,?,?,?,?,?,?,?,?)", (int(form.get('project_id')), safe_choice(form.get('type','RISK'),RISK_TYPES,'RISK'), form.get('title',''), form.get('description',''), max(1,min(5,int(form.get('probability','3')))), max(1,min(5,int(form.get('impact','3')))), safe_choice(form.get('status','OPEN'),RISK_STATUS,'OPEN'), int(form.get('owner_user_id',user.id)), form.get('mitigation_plan',''), form.get('due_date') or None))
        with get_conn() as conn:
            rows=conn.execute("SELECT r.*,u.name owner_name,p.title project_title,(probability*impact) score FROM risk_issues r JOIN users u ON u.id=r.owner_user_id JOIN projects p ON p.id=r.project_id WHERE r.deleted_at IS NULL AND p.deleted_at IS NULL ORDER BY score DESC").fetchall(); users=conn.execute("SELECT id,name FROM users WHERE is_active=1").fetchall(); projects=conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL").fetchall()
        risk_rows=[]
        for r in rows:
            del_action = row_action_delete(f"/risks/{r['id']}/delete") if can_delete(user, r['owner_user_id']) else '-'
            risk_rows.append(f"<tr><td>{html.escape(r['title'])}</td><td>{r['type']}</td><td>{r['score']}</td><td>{r['status']}</td><td>{html.escape(r['project_title'])}</td><td>{del_action}</td></tr>")
        table=''.join(risk_rows) or "<tr><td colspan='6'>No risks/issues found.</td></tr>"
        uopts=''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users]); popts=''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects])
        body=f"<div class='card'><table><tr><th>Title</th><th>Type</th><th>Score</th><th>Status</th><th>Project</th><th>Actions</th></tr>{table}</table></div><div class='card'><h3>New risk/issue</h3><form method='POST' class='grid'><input name='title' required><select name='type'><option>RISK</option><option>ISSUE</option></select><select name='project_id'>{popts}</select><select name='owner_user_id'>{uopts}</select><input type='number' min='1' max='5' name='probability' value='3'><input type='number' min='1' max='5' name='impact' value='3'><input type='date' name='due_date'><select name='status'><option>OPEN</option><option>MITIGATED</option><option>CLOSED</option></select><textarea name='description'></textarea><textarea name='mitigation_plan'></textarea><button>Create</button></form></div>"
        return respond(start_response, layout(user,'Risks & Issues',body,toast_message(environ)))

    if path.startswith('/risks/') and path.endswith('/delete') and method=='POST':
        rid=int(path.split('/')[2])
        with get_conn() as conn:
            r=conn.execute("SELECT owner_user_id FROM risk_issues WHERE id=? AND deleted_at IS NULL", (rid,)).fetchone()
            if not r or not can_delete(user, r['owner_user_id']):
                return respond(start_response, layout(user,'Forbidden',"<div class='card'>Not allowed.</div>"),'403 Forbidden')
            conn.execute("UPDATE risk_issues SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (rid,))
        return redirect(start_response, '/risks?toast=Risk%2FIssue%20deleted')

    if path == '/stakeholders':
        if method=='POST':
            form=parse_form(environ)
            with get_conn() as conn:
                conn.execute("INSERT INTO stakeholders(name,title,org_unit,contact,influence_level,stance,notes) VALUES(?,?,?,?,?,?,?)", (form.get('name',''), form.get('title',''), form.get('org_unit',''), form.get('contact') or None, safe_choice(form.get('influence_level','MED'),IMPACT_LEVELS,'MED'), safe_choice(form.get('stance','NEUTRAL'),STANCE,'NEUTRAL'), form.get('notes','')))
        with get_conn() as conn:
            rows=conn.execute("SELECT * FROM stakeholders WHERE deleted_at IS NULL ORDER BY created_at DESC").fetchall()
        stakeholder_rows=[]
        for s in rows:
            del_action = row_action_delete(f"/stakeholders/{s['id']}/delete") if user.role=='ADMIN' else '-'
            stakeholder_rows.append(f"<tr><td>{html.escape(s['name'])}</td><td>{html.escape(s['title'])}</td><td>{html.escape(s['org_unit'])}</td><td>{s['influence_level']}</td><td>{s['stance']}</td><td>{del_action}</td></tr>")
        table=''.join(stakeholder_rows) or "<tr><td colspan='6'>No stakeholders found.</td></tr>"
        body=f"<div class='card'><table><tr><th>Name</th><th>Title</th><th>Org Unit</th><th>Influence</th><th>Stance</th><th>Actions</th></tr>{table}</table></div><div class='card'><h3>New stakeholder</h3><form method='POST' class='grid'><input name='name' required><input name='title'><input name='org_unit'><input name='contact'><select name='influence_level'><option>LOW</option><option>MED</option><option>HIGH</option></select><select name='stance'><option>SUPPORTIVE</option><option>NEUTRAL</option><option>RESISTANT</option></select><textarea name='notes'></textarea><button>Create</button></form></div>"
        return respond(start_response, layout(user,'Stakeholders',body,toast_message(environ)))

    if path.startswith('/stakeholders/') and path.endswith('/delete') and method=='POST':
        sid=int(path.split('/')[2])
        if user.role != 'ADMIN':
            return respond(start_response, layout(user,'Forbidden',"<div class='card'>Admins only.</div>"),'403 Forbidden')
        with get_conn() as conn:
            conn.execute("UPDATE stakeholders SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (sid,))
            conn.execute("UPDATE project_stakeholders SET deleted_at=CURRENT_TIMESTAMP WHERE stakeholder_id=?", (sid,))
        return redirect(start_response, '/stakeholders?toast=Stakeholder%20deleted')

    if path == '/search':
        q=parse_qs(environ.get('QUERY_STRING','')).get('q',[''])[0]
        like=f"%{q}%"
        with get_conn() as conn:
            p=conn.execute("SELECT title FROM projects WHERE deleted_at IS NULL AND title LIKE ? LIMIT 10", (like,)).fetchall(); d=conn.execute("SELECT title FROM decisions WHERE deleted_at IS NULL AND title LIKE ? LIMIT 10", (like,)).fetchall(); a=conn.execute("SELECT title FROM action_items WHERE deleted_at IS NULL AND title LIKE ? LIMIT 10", (like,)).fetchall()
        items=''.join([f"<li>Project: {html.escape(x['title'])}</li>" for x in p])+''.join([f"<li>Decision: {html.escape(x['title'])}</li>" for x in d])+''.join([f"<li>Action: {html.escape(x['title'])}</li>" for x in a])
        return respond(start_response, layout(user,'Search',f"<div class='card'><ul>{items or '<li>No matches.</li>'}</ul></div>"))

    if path == '/admin/users':
        if not can_manage_users(user):
            return respond(start_response, layout(user,'Forbidden',"<div class='card'>Admins only.</div>"),'403 Forbidden')
        if method == 'POST':
            form = parse_form(environ)
            action = form.get('action')
            with get_conn() as conn:
                if action == 'create':
                    create_user(form.get('name',''), form.get('email',''), form.get('temp_password',''), form.get('role','MEMBER'), must_change_password=True)
                elif action == 'edit':
                    uid = int(form['user_id'])
                    if uid == user.id and form.get('role') != 'ADMIN':
                        return respond(start_response, layout(user,'Invalid',"<div class='card'>You cannot demote yourself.</div>"), '400 Bad Request')
                    if form.get('role') != 'ADMIN':
                        admins = conn.execute("SELECT COUNT(*) c FROM users WHERE role='ADMIN' AND is_active=1").fetchone()['c']
                        current = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
                        if current and current['role'] == 'ADMIN' and admins <= 1:
                            return respond(start_response, layout(user,'Invalid',"<div class='card'>Cannot demote the last ADMIN.</div>"), '400 Bad Request')
                    conn.execute("UPDATE users SET name=?, role=?, is_active=? WHERE id=?", (form.get('name',''), safe_choice(form.get('role','MEMBER'),{'ADMIN','EXEC','MEMBER'},'MEMBER'), 1 if form.get('is_active') == '1' else 0, uid))
                elif action == 'reset_password':
                    uid = int(form['user_id'])
                    update_password(uid, form.get('temp_password',''), must_change_password=True)
        q = parse_qs(environ.get('QUERY_STRING','')).get('q',[''])[0]
        with get_conn() as conn:
            users = conn.execute("SELECT * FROM users WHERE name LIKE ? OR email LIKE ? ORDER BY created_at DESC", (f"%{q}%", f"%{q}%")).fetchall()
        rows=[]
        for u in users:
            edit = f"<form method='POST' class='grid'><input type='hidden' name='action' value='edit'><input type='hidden' name='user_id' value='{u['id']}'><input name='name' value='{html.escape(u['name'])}'><select name='role'><option {'selected' if u['role']=='ADMIN' else ''}>ADMIN</option><option {'selected' if u['role']=='EXEC' else ''}>EXEC</option><option {'selected' if u['role']=='MEMBER' else ''}>MEMBER</option></select><select name='is_active'><option value='1' {'selected' if u['is_active']==1 else ''}>active</option><option value='0' {'selected' if u['is_active']==0 else ''}>disabled</option></select><button>Save</button></form>"
            reset = f"<form method='POST'><input type='hidden' name='action' value='reset_password'><input type='hidden' name='user_id' value='{u['id']}'><input name='temp_password' placeholder='New temp password' required><button>Reset password</button></form>"
            rows.append(f"<tr><td>{html.escape(u['name'])}</td><td>{html.escape(u['email'])}</td><td>{u['role']}</td><td>{u['created_at']}</td><td>{edit}{reset}</td></tr>")
        table=''.join(rows) or "<tr><td colspan='5'>No users found.</td></tr>"
        body=f"<div class='card'><form method='GET'><input name='q' value='{html.escape(q)}' placeholder='Search name/email'><button>Search</button></form><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Created</th><th>Manage</th></tr>{table}</table></div><div class='card'><h3>Create user</h3><form method='POST' class='grid'><input type='hidden' name='action' value='create'><input name='name' required><input type='email' name='email' required><select name='role'><option>ADMIN</option><option>EXEC</option><option>MEMBER</option></select><input name='temp_password' required placeholder='Temporary password'><button>Create user</button></form></div>"
        return respond(start_response, layout(user,'User Administration',body,toast_message(environ)))

    return respond(start_response, layout(user, 'Not found', "<div class='card'>Route not found.</div>"), '404 Not Found')


if __name__ == '__main__':
    migrate()
    print('Starting on http://localhost:8000')
    with make_server('0.0.0.0', 8000, app) as server:
        server.serve_forever()
