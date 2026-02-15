from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parent.parent))

import html
import json
from datetime import date
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from app.auth import authenticate, create_session, create_user, destroy_session, get_user_by_session
from app.db import get_conn, migrate
from app.models import validate_decision_transition
from app.rbac import can_edit_owned_or_admin, can_manage_users, can_view_all

PROJECT_STATUS = {"NOT_STARTED", "IN_PROGRESS", "AT_RISK", "BLOCKED", "DONE"}
PROJECT_PRIORITY = {"P0", "P1", "P2", "P3"}
DECISION_TYPES = {"STRATEGIC", "FINANCIAL", "OPERATIONAL", "GOVERNANCE"}
DECISION_STATUS = {"PROPOSED", "DECIDED", "REVISIT", "CANCELLED"}
ACTION_STATUS = {"OPEN", "IN_PROGRESS", "DONE", "CANCELLED"}
RISK_TYPES = {"RISK", "ISSUE"}
RISK_STATUS = {"OPEN", "MITIGATED", "CLOSED"}
IMPACT_LEVELS = {"LOW", "MED", "HIGH"}


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


def layout(user, title: str, body: str):
    nav = ""
    search = ""
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
          <a href='/war-room'>War Room Brief</a>
          <a href='/admin/users'>Users</a>
          <a href='/logout'>Logout</a>
        </aside>
        """
        search = f"""
        <header class='topbar'>
          <form action='/search' method='GET'>
            <input name='q' placeholder='Search projects, decisions, actions' />
            <button>Search</button>
          </form>
          <div class='pill'>{html.escape(user.name)} · {html.escape(user.role)}</div>
        </header>
        """

    return f"""<!doctype html><html><head><meta charset='utf-8'/><meta name='viewport' content='width=device-width,initial-scale=1'/><title>{html.escape(title)}</title>
    <style>
    :root {{ --bg:#f3f5f9; --card:#ffffff; --text:#111827; --muted:#6b7280; --line:#e5e7eb; --brand:#1d4ed8; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter,system-ui,-apple-system,sans-serif; color:var(--text); background:var(--bg); }}
    .app {{ display:flex; min-height:100vh; }}
    .sidebar {{ width:230px; background:#0f172a; color:#fff; padding:20px; display:flex; flex-direction:column; gap:8px; }}
    .sidebar a {{ color:#cbd5e1; text-decoration:none; padding:8px 10px; border-radius:8px; }}
    .sidebar a:hover {{ background:#1e293b; color:#fff; }}
    .main {{ flex:1; padding:20px; }}
    .topbar {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }}
    .topbar form {{ display:flex; gap:8px; flex:1; max-width:650px; }}
    .pill {{ background:#fff; border:1px solid var(--line); border-radius:999px; padding:8px 12px; color:var(--muted); }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:14px; box-shadow:0 1px 2px rgba(0,0,0,.05); }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; }}
    .kpi {{ background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:12px; font-size:14px; }}
    h1,h2,h3,h4 {{ margin:0 0 12px 0; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ border-bottom:1px solid var(--line); text-align:left; padding:10px 8px; font-size:14px; vertical-align:top; }}
    form.grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
    input,select,textarea,button {{ font:inherit; }}
    input,select,textarea {{ width:100%; padding:10px; border:1px solid var(--line); border-radius:8px; background:#fff; }}
    textarea {{ min-height:90px; }}
    button {{ background:var(--brand); color:#fff; border:none; border-radius:8px; padding:10px 14px; cursor:pointer; }}
    .muted {{ color:var(--muted); }}
    .row {{ display:flex; gap:12px; }}
    .row > .card {{ flex:1; }}
    .tag {{ padding:2px 8px; border-radius:999px; background:#eef2ff; color:#3730a3; font-size:12px; }}
    </style></head><body>
    <div class='app'>{nav}<main class='main'>{search}<h1>{html.escape(title)}</h1>{body}</main></div></body></html>"""


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    user = get_user_by_session(get_cookie(environ, "session"))

    if path == "/":
        return redirect(start_response, "/dashboard" if user else "/login")

    if path == "/signup":
        if method == "POST":
            form = parse_form(environ)
            if not form.get("name") or not form.get("email") or not form.get("password"):
                return respond(start_response, layout(user, "Sign up", "<div class='card'>All fields are required.</div>"), "400 Bad Request")
            create_user(form["name"], form["email"], form["password"], "MEMBER")
            return redirect(start_response, "/login")
        return respond(start_response, layout(user, "Sign up", "<div class='card'><form method='POST'><input name='name' placeholder='Name' required><input name='email' type='email' placeholder='Email' required><input name='password' type='password' placeholder='Password' required><button>Create account</button></form><p class='muted'>Already have an account? <a href='/login'>Login</a></p></div>"))

    if path == "/login":
        if method == "POST":
            form = parse_form(environ)
            auth_user = authenticate(form.get("email", ""), form.get("password", ""))
            if not auth_user:
                return respond(start_response, layout(user, "Login", "<div class='card'>Invalid credentials.</div>"), "401 Unauthorized")
            token = create_session(auth_user.id)
            start_response("302 Found", [("Location", "/dashboard"), ("Set-Cookie", f"session={token}; HttpOnly; Path=/")])
            return [b""]
        return respond(start_response, layout(user, "Login", "<div class='card'><form method='POST'><input name='email' type='email' placeholder='Email' required><input name='password' type='password' placeholder='Password' required><button>Login</button></form><p class='muted'>No account yet? <a href='/signup'>Create one</a></p></div>"))

    if path == "/logout":
        destroy_session(get_cookie(environ, "session"))
        start_response("302 Found", [("Location", "/login"), ("Set-Cookie", "session=; Max-Age=0; Path=/")])
        return [b""]

    if not user:
        return redirect(start_response, "/login")

    if path == "/dashboard":
        with get_conn() as conn:
            status_rows = conn.execute("SELECT status, COUNT(*) c FROM projects GROUP BY status").fetchall()
            decisions_open = conn.execute("SELECT COUNT(*) c FROM decisions WHERE status IN ('PROPOSED','REVISIT')").fetchone()["c"]
            overdue_actions = conn.execute("SELECT COUNT(*) c FROM action_items WHERE status != 'DONE' AND due_date < date('now')").fetchone()["c"]
            top_risks = conn.execute("SELECT title, probability*impact score, type FROM risk_issues ORDER BY score DESC LIMIT 5").fetchall()

        kpi_cards = ''.join([f"<div class='kpi'><strong>{r['status']}</strong><br>{r['c']} projects</div>" for r in status_rows])
        risks = ''.join([f"<tr><td>{html.escape(r['title'])}</td><td>{r['type']}</td><td>{r['score']}</td></tr>" for r in top_risks])
        body = f"""
        <div class='kpis'>{kpi_cards}<div class='kpi'><strong>Open decisions</strong><br>{decisions_open}</div><div class='kpi'><strong>Overdue tasks</strong><br>{overdue_actions}</div></div>
        <div class='row'>
          <div class='card'><h3>Top risks / issues</h3><table><tr><th>Title</th><th>Type</th><th>Score</th></tr>{risks}</table></div>
          <div class='card'><h3>Quick add</h3><p><a href='/projects'>New Project</a></p><p><a href='/decisions'>New Decision</a></p><p><a href='/actions'>New Action Item</a></p><p><a href='/risks'>New Risk/Issue</a></p></div>
        </div>
        """
        return respond(start_response, layout(user, "Executive Dashboard", body))

    if path == "/projects":
        if method == "POST":
            form = parse_form(environ)
            owner = int(form.get("owner_user_id", user.id))
            if user.role == "MEMBER":
                owner = user.id
            with get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO projects(title,short_description,status,priority,owner_user_id,sponsor_name,start_date,target_date,tags) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        form.get("title", "").strip(),
                        form.get("short_description", "").strip(),
                        safe_choice(form.get("status", "NOT_STARTED"), PROJECT_STATUS, "NOT_STARTED"),
                        safe_choice(form.get("priority", "P2"), PROJECT_PRIORITY, "P2"),
                        owner,
                        form.get("sponsor_name", "").strip(),
                        form.get("start_date") or None,
                        form.get("target_date") or None,
                        json.dumps([t.strip() for t in form.get("tags", "").split(",") if t.strip()]),
                    ),
                )
                conn.execute("INSERT INTO activity_logs(entity_type,entity_id,action,actor_user_id) VALUES('PROJECT',?,?,?)", (cur.lastrowid, "CREATE", user.id))

        qs = parse_qs(environ.get("QUERY_STRING", ""))
        where, args = [], []
        for field in ["status", "priority", "owner_user_id"]:
            if qs.get(field):
                where.append(f"p.{field} = ?")
                args.append(qs[field][0])
        if qs.get("tags"):
            where.append("p.tags LIKE ?")
            args.append(f"%{qs['tags'][0]}%")
        if not can_view_all(user):
            where.insert(0, "p.owner_user_id = ?")
            args.insert(0, user.id)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with get_conn() as conn:
            rows = conn.execute(f"SELECT p.*, u.name owner_name FROM projects p JOIN users u ON u.id=p.owner_user_id {clause} ORDER BY p.updated_at DESC", args).fetchall()
            users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()

        table = ''.join([f"<tr><td><a href='/projects/{p['id']}'>{html.escape(p['title'])}</a></td><td><span class='tag'>{p['status']}</span></td><td>{p['priority']}</td><td>{html.escape(p['owner_name'])}</td></tr>" for p in rows])
        owner_opts = ''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        body = f"""
        <div class='card'><h3>Filters</h3><form method='GET' class='grid'><input name='status' placeholder='Status'><input name='priority' placeholder='Priority'><input name='owner_user_id' placeholder='Owner ID'><input name='tags' placeholder='Tag'><button>Apply</button></form></div>
        <div class='card'><h3>Projects</h3><table><tr><th>Title</th><th>Status</th><th>Priority</th><th>Owner</th></tr>{table}</table></div>
        <div class='card'><h3>New project</h3><form method='POST' class='grid'>
          <input name='title' required placeholder='Project title'><input name='short_description' placeholder='Short description'>
          <select name='status'><option>NOT_STARTED</option><option>IN_PROGRESS</option><option>AT_RISK</option><option>BLOCKED</option><option>DONE</option></select>
          <select name='priority'><option>P0</option><option>P1</option><option selected>P2</option><option>P3</option></select>
          <select name='owner_user_id'>{owner_opts}</select><input name='sponsor_name' placeholder='Sponsor'>
          <input type='date' name='start_date'><input type='date' name='target_date'>
          <input name='tags' placeholder='comma-separated tags'><button>Create project</button>
        </form></div>
        """
        return respond(start_response, layout(user, "Projects", body))

    if path.startswith("/projects/"):
        pid = int(path.split("/")[-1])
        with get_conn() as conn:
            project = conn.execute("SELECT p.*,u.name owner_name FROM projects p JOIN users u ON u.id=p.owner_user_id WHERE p.id=?", (pid,)).fetchone()
            if not project:
                return respond(start_response, layout(user, "Not found", "<div class='card'>Project not found.</div>"), "404 Not Found")
            if not can_view_all(user) and project["owner_user_id"] != user.id:
                return respond(start_response, layout(user, "Forbidden", "<div class='card'>Access denied.</div>"), "403 Forbidden")
            decisions = conn.execute("SELECT title,status FROM decisions WHERE project_id=?", (pid,)).fetchall()
            actions = conn.execute("SELECT title,status,due_date FROM action_items WHERE project_id=?", (pid,)).fetchall()
            risks = conn.execute("SELECT title,status,probability*impact score FROM risk_issues WHERE project_id=? ORDER BY score DESC", (pid,)).fetchall()
            stakeholders = conn.execute("SELECT s.name,ps.relationship_notes FROM stakeholders s JOIN project_stakeholders ps ON s.id=ps.stakeholder_id WHERE ps.project_id=?", (pid,)).fetchall()
            logs = conn.execute("SELECT action,created_at FROM activity_logs WHERE entity_type='PROJECT' AND entity_id=? ORDER BY created_at DESC", (pid,)).fetchall()

        decisions_html = ''.join([f"<li>{html.escape(d['title'])} ({d['status']})</li>" for d in decisions])
        actions_html = ''.join([f"<li>{html.escape(a['title'])} [{a['status']}] due {a['due_date'] or '-'}</li>" for a in actions])
        risks_html = ''.join([f"<li>{html.escape(r['title'])} score {r['score']} ({r['status']})</li>" for r in risks])
        stakeholders_html = ''.join([f"<li>{html.escape(s['name'])} - {html.escape(s['relationship_notes'] or '')}</li>" for s in stakeholders])
        logs_html = ''.join([f"<li>{l['created_at']}: {l['action']}</li>" for l in logs])
        body = f"<div class='card'><p>{html.escape(project['short_description'])}</p><p>Owner: {html.escape(project['owner_name'])} · Status: {project['status']} · Priority: {project['priority']}</p><p>Timeline: {project['start_date'] or '-'} → {project['target_date'] or '-'}</p></div>"
        body += f"<div class='row'><div class='card'><h4>Decisions</h4><ul>{decisions_html}</ul></div><div class='card'><h4>Action items</h4><ul>{actions_html}</ul></div></div>"
        body += f"<div class='row'><div class='card'><h4>Risks</h4><ul>{risks_html}</ul></div><div class='card'><h4>Stakeholders</h4><ul>{stakeholders_html}</ul></div></div>"
        body += f"<div class='card'><h4>Activity log</h4><ul>{logs_html}</ul></div>"
        return respond(start_response, layout(user, f"Project: {project['title']}", body))

    if path == "/decisions":
        if method == "POST":
            form = parse_form(environ)
            owner = int(form.get("owner_user_id", user.id))
            if user.role == "MEMBER":
                owner = user.id
            with get_conn() as conn:
                cur = conn.execute("INSERT INTO decisions(project_id,title,decision_type,status,owner_user_id,approver,rationale,options_considered,due_date,impact_level) VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    form.get("project_id") or None,
                    form.get("title", "").strip(),
                    safe_choice(form.get("decision_type", "STRATEGIC"), DECISION_TYPES, "STRATEGIC"),
                    safe_choice(form.get("status", "PROPOSED"), DECISION_STATUS, "PROPOSED"),
                    owner,
                    form.get("approver", ""),
                    form.get("rationale", ""),
                    form.get("options_considered", ""),
                    form.get("due_date") or None,
                    safe_choice(form.get("impact_level", "MED"), IMPACT_LEVELS, "MED"),
                ))
                conn.execute("INSERT INTO activity_logs(entity_type,entity_id,action,actor_user_id) VALUES('DECISION',?,?,?)", (cur.lastrowid, "CREATE", user.id))

        with get_conn() as conn:
            rows = conn.execute("SELECT d.*,u.name owner_name,p.title project_title FROM decisions d JOIN users u ON u.id=d.owner_user_id LEFT JOIN projects p ON p.id=d.project_id ORDER BY d.updated_at DESC").fetchall()
            users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
            projects = conn.execute("SELECT id,title FROM projects ORDER BY title").fetchall()

        if user.role == "MEMBER":
            rows = [r for r in rows if r["owner_user_id"] == user.id]
        table = ''.join([f"<tr><td>{html.escape(d['title'])}</td><td>{d['decision_type']}</td><td>{d['status']}</td><td>{html.escape(d['owner_name'])}</td><td>{html.escape(d['project_title'] or 'Org-level')}</td><td><form method='POST' action='/decisions/{d['id']}/status'><select name='status'><option>PROPOSED</option><option>DECIDED</option><option>REVISIT</option><option>CANCELLED</option></select><input name='decision_outcome' placeholder='Outcome'><input type='date' name='decision_date'><button>Update</button></form></td></tr>" for d in rows])
        user_opts = ''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        project_opts = ''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects])
        body = f"""
        <div class='card'><h3>Decisions</h3><table><tr><th>Decision</th><th>Type</th><th>Status</th><th>Owner</th><th>Project</th><th>Workflow</th></tr>{table}</table></div>
        <div class='card'><h3>New decision</h3><form method='POST' class='grid'>
          <input name='title' required placeholder='Decision statement'>
          <select name='project_id'><option value=''>Org-level</option>{project_opts}</select>
          <select name='decision_type'><option>STRATEGIC</option><option>FINANCIAL</option><option>OPERATIONAL</option><option>GOVERNANCE</option></select>
          <select name='status'><option>PROPOSED</option><option>REVISIT</option></select>
          <select name='owner_user_id'>{user_opts}</select><input name='approver' placeholder='Approver'>
          <input type='date' name='due_date'><select name='impact_level'><option>LOW</option><option selected>MED</option><option>HIGH</option></select>
          <textarea name='rationale' placeholder='Rationale'></textarea><textarea name='options_considered' placeholder='Options considered'></textarea>
          <button>Create decision</button>
        </form></div>
        """
        return respond(start_response, layout(user, "Decisions", body))

    if path.startswith("/decisions/") and path.endswith("/status") and method == "POST":
        did = int(path.split("/")[2])
        form = parse_form(environ)
        target = safe_choice(form.get("status", ""), DECISION_STATUS, "PROPOSED")
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
            if not row:
                return respond(start_response, layout(user, "Not found", "<div class='card'>Decision not found.</div>"), "404 Not Found")
            if not can_edit_owned_or_admin(user, row["owner_user_id"]):
                return respond(start_response, layout(user, "Forbidden", "<div class='card'>You cannot edit this decision.</div>"), "403 Forbidden")
            ok, msg = validate_decision_transition(row["status"], target, form.get("decision_outcome", ""), form.get("decision_date"))
            if not ok:
                return respond(start_response, layout(user, "Invalid transition", f"<div class='card'>{html.escape(msg)}</div>"), "400 Bad Request")
            conn.execute("UPDATE decisions SET status=?, decision_outcome=?, decision_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (target, form.get("decision_outcome") or row["decision_outcome"], form.get("decision_date") or row["decision_date"], did))
            conn.execute("INSERT INTO activity_logs(entity_type,entity_id,action,actor_user_id) VALUES('DECISION',?,?,?)", (did, f"STATUS_{target}", user.id))
        return redirect(start_response, "/decisions")

    if path == "/actions":
        if method == "POST":
            form = parse_form(environ)
            owner = int(form.get("owner_user_id", user.id))
            if user.role == "MEMBER":
                owner = user.id
            with get_conn() as conn:
                conn.execute("INSERT INTO action_items(project_id,decision_id,title,owner_user_id,status,due_date,notes) VALUES(?,?,?,?,?,?,?)", (
                    form.get("project_id") or None,
                    form.get("decision_id") or None,
                    form.get("title", "").strip(),
                    owner,
                    safe_choice(form.get("status", "OPEN"), ACTION_STATUS, "OPEN"),
                    form.get("due_date") or None,
                    form.get("notes", ""),
                ))

        with get_conn() as conn:
            rows = conn.execute("SELECT a.*,u.name owner_name,p.title project_title FROM action_items a JOIN users u ON u.id=a.owner_user_id LEFT JOIN projects p ON p.id=a.project_id ORDER BY a.status,a.due_date").fetchall()
            users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
            projects = conn.execute("SELECT id,title FROM projects ORDER BY title").fetchall()
            decisions = conn.execute("SELECT id,title FROM decisions ORDER BY title").fetchall()
        if user.role == "MEMBER":
            rows = [r for r in rows if r["owner_user_id"] == user.id]

        table = ''.join([f"<tr><td>{html.escape(r['title'])}</td><td>{r['status']}</td><td>{html.escape(r['owner_name'])}</td><td>{r['due_date'] or '-'}</td><td>{html.escape(r['project_title'] or '-')}</td></tr>" for r in rows])
        user_opts = ''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        project_opts = ''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects])
        decision_opts = ''.join([f"<option value='{d['id']}'>{html.escape(d['title'])}</option>" for d in decisions])
        body = f"<div class='card'><h3>Action items</h3><table><tr><th>Title</th><th>Status</th><th>Owner</th><th>Due</th><th>Project</th></tr>{table}</table></div>"
        body += f"<div class='card'><h3>New action item</h3><form method='POST' class='grid'><input name='title' required placeholder='Action title'><select name='owner_user_id'>{user_opts}</select><select name='project_id'><option value=''>None</option>{project_opts}</select><select name='decision_id'><option value=''>None</option>{decision_opts}</select><select name='status'><option>OPEN</option><option>IN_PROGRESS</option><option>DONE</option><option>CANCELLED</option></select><input type='date' name='due_date'><textarea name='notes' placeholder='Notes'></textarea><button>Create action</button></form></div>"
        return respond(start_response, layout(user, "Action Items", body))

    if path == "/risks":
        if method == "POST":
            form = parse_form(environ)
            with get_conn() as conn:
                conn.execute("INSERT INTO risk_issues(project_id,type,title,description,probability,impact,status,owner_user_id,mitigation_plan,due_date) VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    int(form.get("project_id")),
                    safe_choice(form.get("type", "RISK"), RISK_TYPES, "RISK"),
                    form.get("title", "").strip(),
                    form.get("description", ""),
                    max(1, min(5, int(form.get("probability", "3")))),
                    max(1, min(5, int(form.get("impact", "3")))),
                    safe_choice(form.get("status", "OPEN"), RISK_STATUS, "OPEN"),
                    int(form.get("owner_user_id", user.id)),
                    form.get("mitigation_plan", ""),
                    form.get("due_date") or None,
                ))

        with get_conn() as conn:
            rows = conn.execute("SELECT r.*,u.name owner_name,p.title project_title,(probability*impact) score FROM risk_issues r JOIN users u ON u.id=r.owner_user_id JOIN projects p ON p.id=r.project_id ORDER BY score DESC").fetchall()
            users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
            projects = conn.execute("SELECT id,title FROM projects ORDER BY title").fetchall()

        table = ''.join([f"<tr><td>{html.escape(r['title'])}</td><td>{r['type']}</td><td>{r['score']}</td><td>{r['status']}</td><td>{html.escape(r['project_title'])}</td></tr>" for r in rows])
        user_opts = ''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        project_opts = ''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects])
        body = f"<div class='card'><h3>Risk & issue register</h3><table><tr><th>Title</th><th>Type</th><th>Score</th><th>Status</th><th>Project</th></tr>{table}</table></div>"
        body += f"<div class='card'><h3>New risk/issue</h3><form method='POST' class='grid'><input name='title' required placeholder='Title'><select name='type'><option>RISK</option><option>ISSUE</option></select><select name='project_id'>{project_opts}</select><select name='owner_user_id'>{user_opts}</select><input type='number' min='1' max='5' name='probability' value='3'><input type='number' min='1' max='5' name='impact' value='3'><input type='date' name='due_date'><select name='status'><option>OPEN</option><option>MITIGATED</option><option>CLOSED</option></select><textarea name='description' placeholder='Description'></textarea><textarea name='mitigation_plan' placeholder='Mitigation plan'></textarea><button>Create risk/issue</button></form></div>"
        return respond(start_response, layout(user, "Risks & Issues", body))

    if path == "/stakeholders":
        if method == "POST":
            form = parse_form(environ)
            with get_conn() as conn:
                conn.execute("INSERT INTO stakeholders(name,title,org_unit,contact,influence_level,stance,notes) VALUES(?,?,?,?,?,?,?)", (
                    form.get("name", "").strip(),
                    form.get("title", ""),
                    form.get("org_unit", ""),
                    form.get("contact") or None,
                    safe_choice(form.get("influence_level", "MED"), IMPACT_LEVELS, "MED"),
                    safe_choice(form.get("stance", "NEUTRAL"), {"SUPPORTIVE", "NEUTRAL", "RESISTANT"}, "NEUTRAL"),
                    form.get("notes", ""),
                ))
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM stakeholders ORDER BY created_at DESC").fetchall()
        table = ''.join([f"<tr><td>{html.escape(s['name'])}</td><td>{html.escape(s['title'])}</td><td>{html.escape(s['org_unit'])}</td><td>{s['influence_level']}</td><td>{s['stance']}</td></tr>" for s in rows])
        body = f"<div class='card'><h3>Directory</h3><table><tr><th>Name</th><th>Title</th><th>Org Unit</th><th>Influence</th><th>Stance</th></tr>{table}</table></div>"
        body += "<div class='card'><h3>New stakeholder</h3><form method='POST' class='grid'><input name='name' required placeholder='Name'><input name='title' placeholder='Title'><input name='org_unit' placeholder='Org unit'><input name='contact' placeholder='Contact'><select name='influence_level'><option>LOW</option><option selected>MED</option><option>HIGH</option></select><select name='stance'><option>SUPPORTIVE</option><option selected>NEUTRAL</option><option>RESISTANT</option></select><textarea name='notes' placeholder='Notes'></textarea><button>Create stakeholder</button></form></div>"
        return respond(start_response, layout(user, "Stakeholders", body))

    if path == "/war-room":
        with get_conn() as conn:
            p0_p1 = conn.execute("SELECT title,status,short_description FROM projects WHERE priority IN ('P0','P1') ORDER BY priority,status").fetchall()
            decisions = conn.execute("SELECT title,status,due_date FROM decisions WHERE status IN ('PROPOSED','REVISIT') ORDER BY due_date LIMIT 10").fetchall()
            overdue = conn.execute("SELECT title,due_date FROM action_items WHERE status != 'DONE' AND due_date < date('now') ORDER BY due_date LIMIT 5").fetchall()
            risks = conn.execute("SELECT title, probability*impact score FROM risk_issues ORDER BY score DESC LIMIT 5").fetchall()

        lines = ["WAR ROOM BRIEF", f"Week of {date.today()}", "", "P0/P1 Projects:"]
        lines += [f"- {x['title']} [{x['status']}] blocker: {x['short_description']}" for x in p0_p1]
        lines += ["", "Decisions needed this week:"] + [f"- {d['title']} ({d['status']}) due {d['due_date'] or 'n/a'}" for d in decisions]
        lines += ["", "Top 5 overdue action items:"] + [f"- {a['title']} due {a['due_date']}" for a in overdue]
        lines += ["", "Top 5 risks/issues:"] + [f"- {r['title']} score {r['score']}" for r in risks]
        brief = "\n".join(lines)
        body = f"<div class='card'><h3>Weekly executive summary</h3><textarea id='brief' rows='18'>{html.escape(brief)}</textarea><p><button onclick=\"navigator.clipboard.writeText(document.getElementById('brief').value);this.innerText='Copied!';\">Copy formatted brief</button></p></div>"
        return respond(start_response, layout(user, "War Room Brief", body))

    if path == "/search":
        q = parse_qs(environ.get("QUERY_STRING", "")).get("q", [""])[0].strip()
        like = f"%{q}%"
        with get_conn() as conn:
            projects = conn.execute("SELECT title FROM projects WHERE title LIKE ? LIMIT 10", (like,)).fetchall()
            decisions = conn.execute("SELECT title FROM decisions WHERE title LIKE ? LIMIT 10", (like,)).fetchall()
            actions = conn.execute("SELECT title FROM action_items WHERE title LIKE ? LIMIT 10", (like,)).fetchall()
        items = ''.join([f"<li>Project: {html.escape(r['title'])}</li>" for r in projects])
        items += ''.join([f"<li>Decision: {html.escape(r['title'])}</li>" for r in decisions])
        items += ''.join([f"<li>Action: {html.escape(r['title'])}</li>" for r in actions])
        body = f"<div class='card'><h3>Search results</h3><p class='muted'>Query: {html.escape(q)}</p><ul>{items or '<li>No matches.</li>'}</ul></div>"
        return respond(start_response, layout(user, "Search", body))

    if path == "/admin/users":
        if not can_manage_users(user):
            return respond(start_response, layout(user, "Forbidden", "<div class='card'>Admins only.</div>"), "403 Forbidden")
        if method == "POST":
            form = parse_form(environ)
            create_user(form.get("name", ""), form.get("email", ""), form.get("password", ""), form.get("role", "MEMBER"))
        with get_conn() as conn:
            users = conn.execute("SELECT name,email,role,created_at FROM users ORDER BY created_at DESC").fetchall()
        table = ''.join([f"<tr><td>{html.escape(u['name'])}</td><td>{html.escape(u['email'])}</td><td>{u['role']}</td><td>{u['created_at']}</td></tr>" for u in users])
        body = f"<div class='card'><h3>Users</h3><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Created</th></tr>{table}</table></div>"
        body += "<div class='card'><h3>Create user</h3><form method='POST' class='grid'><input name='name' required><input name='email' type='email' required><input name='password' required><select name='role'><option>ADMIN</option><option>EXEC</option><option selected>MEMBER</option></select><button>Create user</button></form></div>"
        return respond(start_response, layout(user, "User Administration", body))

    return respond(start_response, layout(user, "Not found", "<div class='card'>Route not found.</div>"), "404 Not Found")


if __name__ == "__main__":
    migrate()
    print("Starting on http://localhost:8000")
    with make_server("0.0.0.0", 8000, app) as server:
        server.serve_forever()
