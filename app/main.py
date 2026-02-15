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


def e(text) -> str:
    return html.escape(str(text or ""))


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
    return user.role == "ADMIN" or (user.role == "EXEC" and owner_user_id is not None and user.id == owner_user_id)


def badge_tone(text: str) -> str:
    status_tones = {
        "NOT_STARTED": "muted", "IN_PROGRESS": "blue", "AT_RISK": "amber", "BLOCKED": "red", "DONE": "green",
        "PROPOSED": "blue", "REVISIT": "amber", "DECIDED": "green", "CANCELLED": "muted",
        "OPEN": "blue", "MITIGATED": "amber", "CLOSED": "green",
        "P0": "red", "P1": "amber", "P2": "blue", "P3": "muted",
    }
    return status_tones.get(text, "muted")


def render_badge(text: str, tone: str | None = None) -> str:
    return f"<span class='badge {tone or badge_tone(text)}'>{e(text)}</span>"


def render_toast(message: str, type_: str = "success") -> str:
    if not message:
        return ""
    return f"<div class='toast {e(type_)}'>{e(message)}</div>"


def render_page_header(title: str, subtitle: str = "", back_link: str = "") -> str:
    back = f"<a class='btn btn-ghost' href='{e(back_link)}'>← Back</a>" if back_link else ""
    sub = f"<p class='subtitle'>{e(subtitle)}</p>" if subtitle else ""
    return f"<div class='page-header'>{back}<div><h1>{e(title)}</h1>{sub}</div></div>"


def render_card(title: str, content: str, actions: str = "") -> str:
    head = f"<div class='card-head'><h3>{e(title)}</h3><div>{actions}</div></div>" if title else ""
    return f"<section class='card'>{head}{content}</section>"


def render_kpi_grid(items):
    blocks = "".join([f"<div class='kpi'><div class='kpi-label'>{e(i['label'])}</div><div class='kpi-value'>{e(i['value'])}</div></div>" for i in items])
    return f"<section class='kpi-grid'>{blocks}</section>"


def render_table(columns, rows_html: str, empty_text="No records found."):
    thead = "".join([f"<th>{e(c)}</th>" for c in columns])
    tbody = rows_html if rows_html else f"<tr><td colspan='{len(columns)}' class='empty'>{e(empty_text)}</td></tr>"
    return f"<div class='table-wrap'><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>"


def render_empty_state(title: str, subtitle: str, cta_html: str = "") -> str:
    return f"<div class='empty-state'><h4>{e(title)}</h4><p>{e(subtitle)}</p>{cta_html}</div>"


def render_nav(user, active_path: str):
    links = [
        ("/dashboard", "Dashboard"), ("/projects", "Projects"), ("/decisions", "Decisions"),
        ("/actions", "Action Items"), ("/risks", "Risks & Issues"), ("/stakeholders", "Stakeholders"),
    ]
    if can_manage_users(user):
        links.append(("/admin/users", "Users"))
    links.append(("/logout", "Logout"))
    items = []
    for href, label in links:
        active = "active" if (active_path == href or (href != "/logout" and active_path.startswith(href))) else ""
        items.append(f"<a class='{active}' href='{href}'>{e(label)}</a>")
    return "".join(items)


def render_container(content: str):
    return f"<div class='container'>{content}</div>"


def toast_message(environ):
    return parse_qs(environ.get("QUERY_STRING", "")).get("toast", [""])[0]


def layout(user, title: str, body_html: str, toast_html: str = "", active_path: str = ""):
    nav = ""
    top_user = ""
    if user:
        nav = f"<nav class='sidebar'>{render_nav(user, active_path)}</nav>"
        top_user = f"<div class='user-pill'>{e(user.name)} · {e(user.role)}</div>"
    css = """
:root{--bg:#f6f7fb;--surface:#fff;--text:#0f172a;--muted:#64748b;--line:#e2e8f0;--shadow:0 1px 2px rgba(15,23,42,.06),0 10px 20px rgba(15,23,42,.05);--brand:#2563eb;--danger:#b91c1c;--radius:12px}
@media(prefers-color-scheme:dark){:root{--bg:#0b1220;--surface:#0f172a;--text:#e2e8f0;--muted:#94a3b8;--line:#1e293b;--shadow:none;--brand:#3b82f6;--danger:#ef4444}}
*{box-sizing:border-box} body{margin:0;font-family:Inter,system-ui,Arial;background:var(--bg);color:var(--text)}
.app{display:grid;grid-template-columns:240px 1fr;min-height:100vh} .sidebar{padding:18px;border-right:1px solid var(--line);background:var(--surface);display:flex;flex-direction:column;gap:6px}
.sidebar a{text-decoration:none;color:var(--muted);padding:10px 12px;border-radius:10px} .sidebar a:hover{background:#eef2ff;color:var(--text)} .sidebar a.active{background:#dbeafe;color:#1e40af;font-weight:600}
.main{padding:20px} .topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px} .brand{font-weight:700;letter-spacing:.2px} .user-pill{border:1px solid var(--line);padding:8px 12px;border-radius:999px;background:var(--surface);color:var(--muted)}
.container{max-width:1200px;margin:0 auto} .page-header{display:flex;gap:12px;align-items:flex-start;margin-bottom:14px} .subtitle{margin:4px 0 0;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow);margin-bottom:14px} .card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.kpi-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px} .kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px} .kpi-label{color:var(--muted);font-size:13px} .kpi-value{font-size:24px;font-weight:700;margin-top:4px}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px} table{width:100%;border-collapse:collapse;background:var(--surface)} th,td{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top} tbody tr:nth-child(even){background:rgba(148,163,184,.06)} tbody tr:hover{background:rgba(37,99,235,.06)} .empty{text-align:center;color:var(--muted)}
label{font-weight:600;font-size:13px;display:block;margin-bottom:5px} .form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px} .field{display:flex;flex-direction:column}
input,select,textarea{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--text)} textarea{min-height:90px}
.btn{display:inline-block;border:none;border-radius:10px;padding:9px 12px;background:var(--brand);color:#fff;text-decoration:none;cursor:pointer} .btn-ghost{background:transparent;color:var(--muted);border:1px solid var(--line)} .btn-danger{background:var(--danger)} .btn-sm{padding:7px 10px;font-size:12px}
.actions{display:flex;gap:6px;flex-wrap:wrap} .badge{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;font-weight:600} .badge.muted{background:#e2e8f0;color:#334155} .badge.blue{background:#dbeafe;color:#1e40af} .badge.amber{background:#fef3c7;color:#92400e} .badge.red{background:#fee2e2;color:#991b1b} .badge.green{background:#dcfce7;color:#166534}
.toast{padding:10px 12px;border-radius:10px;margin-bottom:12px} .toast.success{background:#dcfce7;border:1px solid #86efac;color:#14532d} .toast.error{background:#fee2e2;border:1px solid #fca5a5;color:#7f1d1d}
.empty-state{padding:28px 10px;text-align:center;color:var(--muted)} .toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:end}
@media(max-width:900px){.app{grid-template-columns:1fr} .sidebar{position:sticky;top:0;z-index:5;border-right:none;border-bottom:1px solid var(--line);flex-direction:row;overflow:auto} .form-grid,.kpi-grid{grid-template-columns:1fr}}
"""
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            + f"<title>{e(title)} · ExecutiveOS</title><style>{css}</style></head><body>"
            + f"<div class='app'>{nav}<main class='main'><div class='topbar'><div class='brand'>Executive Project & Decision Command Center</div>{top_user}</div>{render_container(render_toast(toast_html) + body_html)}</main></div></body></html>")



def must_change_password(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT must_change_password FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row and row["must_change_password"] == 1)


def delete_action(entity: str, entity_id: int, typed: bool) -> str:
    if typed:
        return f"<a class='btn btn-sm btn-danger' href='/confirm-delete?entity={entity}&id={entity_id}'>Delete</a>"
    return f"<form method='POST' action='/{entity}/{entity_id}/delete' onsubmit=\"return confirm('Delete this item?')\"><button class='btn btn-sm btn-danger'>Delete</button></form>"


def can_view_owned_or_admin_exec(user, owner_user_id: int | None = None) -> bool:
    if user.role in {"ADMIN", "EXEC"}:
        return True
    return owner_user_id is not None and user.id == owner_user_id


def can_edit_entity(user, entity: str, owner_user_id: int | None) -> bool:
    if user.role == "ADMIN":
        return True
    if user.role == "EXEC":
        return owner_user_id is not None and user.id == owner_user_id
    if user.role == "MEMBER":
        return entity == "actions" and owner_user_id is not None and user.id == owner_user_id
    return False


def action_links(view_href: str, edit_href: str | None, delete_html: str = "", extra_html: str = "") -> str:
    parts = [f"<a class='btn btn-sm btn-ghost' href='{view_href}'>View</a>"]
    if edit_href:
        parts.append(f"<a class='btn btn-sm' href='{edit_href}'>Edit</a>")
    if extra_html:
        parts.append(extra_html)
    if delete_html:
        parts.append(delete_html)
    return "".join(parts)


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    user = get_user_by_session(get_cookie(environ, "session"))

    if path == "/":
        return redirect(start_response, "/dashboard" if user else "/login")

    if path == "/signup":
        if method == "POST":
            f = parse_form(environ)
            create_user(f.get("name", ""), f.get("email", ""), f.get("password", ""), "MEMBER")
            return redirect(start_response, "/login")
        body = render_page_header("Create account", "Self-signup for MVP") + render_card("Sign up", "<form method='POST' class='form-grid'><div class='field'><label>Name</label><input name='name' required></div><div class='field'><label>Email</label><input type='email' name='email' required></div><div class='field'><label>Password</label><input type='password' name='password' required></div><div class='field'><label>&nbsp;</label><button class='btn'>Create account</button></div></form><p>Already have an account? <a href='/login'>Login</a></p>")
        return respond(start_response, layout(user, "Sign up", body, active_path=path))

    if path == "/login":
        if method == "POST":
            f = parse_form(environ)
            auth_user = authenticate(f.get("email", ""), f.get("password", ""))
            if not auth_user:
                body = render_page_header("Login") + render_card("Sign in", "<p>Invalid credentials or inactive account.</p>")
                return respond(start_response, layout(user, "Login", body, "", active_path=path), "401 Unauthorized")
            token = create_session(auth_user.id)
            target = "/change-password" if must_change_password(auth_user.id) else "/dashboard"
            start_response("302 Found", [("Location", target), ("Set-Cookie", f"session={token}; HttpOnly; Path=/")])
            return [b""]
        body = render_page_header("Login", "Access your command center") + render_card("Sign in", "<form method='POST' class='form-grid'><div class='field'><label>Email</label><input type='email' name='email' required></div><div class='field'><label>Password</label><input type='password' name='password' required></div><div class='field'><label>&nbsp;</label><button class='btn'>Login</button></div></form><p>No account? <a href='/signup'>Sign up</a></p>")
        return respond(start_response, layout(user, "Login", body, active_path=path))

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
            f = parse_form(environ)
            if not f.get("new_password"):
                body = render_page_header("Change password") + render_card("Required", "<p>Password required.</p>")
                return respond(start_response, layout(user, "Change password", body, active_path=path), "400 Bad Request")
            update_password(user.id, f["new_password"], must_change_password=False)
            return redirect(start_response, "/dashboard?toast=Password%20updated")
        body = render_page_header("Change password", "Required on first login") + render_card("Security update", "<form method='POST'><div class='field'><label>New password</label><input type='password' name='new_password' required></div><button class='btn'>Update password</button></form>")
        return respond(start_response, layout(user, "Change password", body, active_path=path))

    # typed confirmation page
    if path == "/confirm-delete":
        q = parse_qs(environ.get("QUERY_STRING", ""))
        entity = q.get("entity", [""])[0]
        item_id = int(q.get("id", ["0"])[0])
        if entity not in {"projects", "decisions"}:
            return respond(start_response, layout(user, "Invalid", render_card("Invalid request", ""), active_path=path), "400 Bad Request")
        if method == "POST":
            f = parse_form(environ)
            if f.get("confirm_text") != "DELETE":
                return respond(start_response, layout(user, "Confirm delete", render_card("Confirmation", "<p>Type DELETE to confirm.</p>"), active_path=path), "400 Bad Request")
            return redirect(start_response, f"/{entity}/{item_id}/delete?confirmed=1")
        body = render_page_header("Confirm delete", f"You are deleting a {entity[:-1]}", f"/{entity}") + render_card(
            "Danger zone",
            f"<p>This action cannot be undone. Type <b>DELETE</b> to continue.</p><form method='POST'><div class='field'><label>Confirmation</label><input name='confirm_text' placeholder='DELETE' required></div><button class='btn btn-danger'>Confirm delete</button></form>",
        )
        return respond(start_response, layout(user, "Confirm delete", body, active_path=path))

    if path == "/dashboard":
        with get_conn() as conn:
            statuses = conn.execute("SELECT status,COUNT(*) c FROM projects WHERE deleted_at IS NULL GROUP BY status").fetchall()
            open_decisions = dashboard_open_decision_count(conn)
            overdue = conn.execute("SELECT COUNT(*) c FROM action_items WHERE deleted_at IS NULL AND status!='DONE' AND due_date < date('now')").fetchone()["c"]
            top_risks = conn.execute("SELECT title,type,probability*impact score FROM risk_issues WHERE deleted_at IS NULL ORDER BY score DESC LIMIT 5").fetchall()
            decided_7 = conn.execute("SELECT COUNT(*) c FROM decisions WHERE deleted_at IS NULL AND status='DECIDED' AND decision_date >= date('now','-7 day')").fetchone()["c"]
            decided_30 = conn.execute("SELECT COUNT(*) c FROM decisions WHERE deleted_at IS NULL AND status='DECIDED' AND decision_date >= date('now','-30 day')").fetchone()["c"]
            recent = conn.execute("SELECT d.title,d.decision_date,COALESCE(p.title,'Org-level') project_title FROM decisions d LEFT JOIN projects p ON p.id=d.project_id WHERE d.deleted_at IS NULL AND d.status='DECIDED' ORDER BY d.decision_date DESC LIMIT 5").fetchall()
        kpis = [{"label": s["status"], "value": s["c"]} for s in statuses]
        kpis += [{"label": "Open decisions", "value": open_decisions}, {"label": "Overdue actions", "value": overdue}, {"label": "Decided (7d/30d)", "value": f"{decided_7} / {decided_30}"}]
        risk_rows = "".join([f"<tr><td>{e(r['title'])}</td><td>{render_badge(r['type'])}</td><td>{r['score']}</td></tr>" for r in top_risks])
        recent_items = "".join([f"<li>{e(x['title'])} · {render_badge(x['project_title'],'muted')} · {e(x['decision_date'])}</li>" for x in recent]) or "<li class='empty'>No recent decided decisions.</li>"
        body = render_page_header("Dashboard", "At-a-glance executive status") + render_kpi_grid(kpis)
        body += render_card("Top risks/issues", render_table(["Title", "Type", "Score"], risk_rows, "No risks or issues."))
        body += render_card("Recent decisions made", f"<ul>{recent_items}</ul>")
        return respond(start_response, layout(user, "Dashboard", body, toast_message(environ), path))

    if path == "/projects":
        if method == "POST":
            f = parse_form(environ)
            owner = user.id if user.role == "MEMBER" else int(f.get("owner_user_id", user.id))
            with get_conn() as conn:
                conn.execute("INSERT INTO projects(title,short_description,status,priority,owner_user_id,sponsor_name,start_date,target_date,tags) VALUES(?,?,?,?,?,?,?,?,?)", (f.get("title", ""), f.get("short_description", ""), safe_choice(f.get("status", "NOT_STARTED"), PROJECT_STATUS, "NOT_STARTED"), safe_choice(f.get("priority", "P2"), PROJECT_PRIORITY, "P2"), owner, f.get("sponsor_name", ""), f.get("start_date") or None, f.get("target_date") or None, json.dumps([t.strip() for t in f.get("tags", "").split(",") if t.strip()])))
        with get_conn() as conn:
            rows = conn.execute("SELECT p.*,u.name owner_name FROM projects p JOIN users u ON u.id=p.owner_user_id WHERE p.deleted_at IS NULL ORDER BY p.updated_at DESC").fetchall()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1 ORDER BY name").fetchall()
        if not can_view_all(user):
            rows = [r for r in rows if r["owner_user_id"] == user.id]
        project_rows = []
        for r in rows:
            links = action_links(f"/projects/{r['id']}", f"/projects/{r['id']}/edit" if can_edit_entity(user,'projects', r['owner_user_id']) else None, delete_action('projects', r['id'], True) if can_delete(user, r['owner_user_id']) else '')
            project_rows.append(f"<tr><td><a href='/projects/{r['id']}'>{e(r['title'])}</a></td><td>{render_badge(r['status'])}</td><td>{render_badge(r['priority'])}</td><td>{e(r['owner_name'])}</td><td class='actions'>{links}</td></tr>")
        rows_html = ''.join(project_rows)
        table = render_table(["Title", "Status", "Priority", "Owner", "Actions"], rows_html, "No projects found.")
        owner_opts = "".join([f"<option value='{u['id']}'>{e(u['name'])}</option>" for u in users])
        form = "<form method='POST' class='form-grid'>" \
            "<div class='field'><label>Title</label><input name='title' required></div><div class='field'><label>Short description</label><input name='short_description'></div>" \
            "<div class='field'><label>Status</label><select name='status'><option>NOT_STARTED</option><option>IN_PROGRESS</option><option>AT_RISK</option><option>BLOCKED</option><option>DONE</option></select></div>" \
            "<div class='field'><label>Priority</label><select name='priority'><option>P0</option><option>P1</option><option>P2</option><option>P3</option></select></div>" \
            f"<div class='field'><label>Owner</label><select name='owner_user_id'>{owner_opts}</select></div><div class='field'><label>Sponsor</label><input name='sponsor_name'></div>" \
            "<div class='field'><label>Start date</label><input type='date' name='start_date'></div><div class='field'><label>Target date</label><input type='date' name='target_date'></div>" \
            "<div class='field'><label>Tags</label><input name='tags' placeholder='comma-separated'></div><div class='field'><label>&nbsp;</label><button class='btn'>Create project</button></div></form>"
        body = render_page_header("Projects", "Track strategic initiatives") + render_card("Project list", table) + render_card("New project", form)
        return respond(start_response, layout(user, "Projects", body, toast_message(environ), path))


    if path.startswith("/projects/") and path.endswith("/edit"):
        pid = int(path.split("/")[2])
        with get_conn() as conn:
            proj = conn.execute("SELECT * FROM projects WHERE id=? AND deleted_at IS NULL", (pid,)).fetchone()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1 ORDER BY name").fetchall()
        if not proj:
            return respond(start_response, layout(user, "Not found", render_card("Missing", "Project not found."), active_path=path), "404 Not Found")
        if not can_edit_entity(user, 'projects', proj['owner_user_id']):
            return respond(start_response, layout(user, "Forbidden", render_card("Not allowed", ""), active_path=path), "403 Forbidden")
        if method == 'POST':
            f = parse_form(environ)
            owner = proj['owner_user_id'] if user.role == 'MEMBER' else int(f.get('owner_user_id', proj['owner_user_id']))
            with get_conn() as conn:
                conn.execute("UPDATE projects SET title=?,short_description=?,status=?,priority=?,owner_user_id=?,sponsor_name=?,start_date=?,target_date=?,tags=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (
                    f.get('title',''), f.get('short_description',''), safe_choice(f.get('status','NOT_STARTED'), PROJECT_STATUS, 'NOT_STARTED'), safe_choice(f.get('priority','P2'), PROJECT_PRIORITY, 'P2'), owner, f.get('sponsor_name',''), f.get('start_date') or None, f.get('target_date') or None, json.dumps([t.strip() for t in f.get('tags','').split(',') if t.strip()]), pid
                ))
            return redirect(start_response, f"/projects/{pid}?toast=Saved")
        owner_opts=''.join([f"<option value='{u['id']}' {'selected' if u['id']==proj['owner_user_id'] else ''}>{e(u['name'])}</option>" for u in users])
        tags = ', '.join(json.loads(proj['tags'] or '[]')) if proj['tags'] else ''
        form=f"<form method='POST' class='form-grid'><div class='field'><label>Title</label><input name='title' value='{e(proj['title'])}' required></div><div class='field'><label>Short description</label><input name='short_description' value='{e(proj['short_description'])}'></div><div class='field'><label>Status</label><select name='status'>" + ''.join([f"<option {'selected' if proj['status']==x else ''}>{x}</option>" for x in ['NOT_STARTED','IN_PROGRESS','AT_RISK','BLOCKED','DONE']]) + "</select></div><div class='field'><label>Priority</label><select name='priority'>" + ''.join([f"<option {'selected' if proj['priority']==x else ''}>{x}</option>" for x in ['P0','P1','P2','P3']]) + f"</select></div><div class='field'><label>Owner</label><select name='owner_user_id'>{owner_opts}</select></div><div class='field'><label>Sponsor</label><input name='sponsor_name' value='{e(proj['sponsor_name'])}'></div><div class='field'><label>Start date</label><input type='date' name='start_date' value='{e(proj['start_date'])}'></div><div class='field'><label>Target date</label><input type='date' name='target_date' value='{e(proj['target_date'])}'></div><div class='field'><label>Tags</label><input name='tags' value='{e(tags)}'></div><div class='field'><label>&nbsp;</label><button class='btn'>Save</button></div></form>"
        body = render_page_header(f"Edit Project: {proj['title']}", "Update project record", f"/projects/{pid}") + render_card("Edit project", form)
        return respond(start_response, layout(user, "Edit Project", body, active_path='/projects'))

    if path.startswith("/projects/") and path.endswith("/delete"):
        if parse_qs(environ.get("QUERY_STRING", "")).get("confirmed", [""])[0] != "1":
            pid = int(path.split("/")[2]); return redirect(start_response, f"/confirm-delete?entity=projects&id={pid}")
        pid = int(path.split("/")[2])
        with get_conn() as conn:
            p = conn.execute("SELECT id,owner_user_id FROM projects WHERE id=? AND deleted_at IS NULL", (pid,)).fetchone()
            if not p or not can_delete(user, p["owner_user_id"]):
                return respond(start_response, layout(user, "Forbidden", render_card("Not allowed", ""), active_path=path), "403 Forbidden")
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
                return respond(start_response, layout(user, "Not found", render_card("Missing", "This item was deleted or not found."), active_path=path), "404 Not Found")
            if not can_view_all(user) and p["owner_user_id"] != user.id:
                return respond(start_response, layout(user, "Forbidden", render_card("Forbidden", ""), active_path=path), "403 Forbidden")
            decisions = conn.execute("SELECT id,title,status FROM decisions WHERE project_id=? AND deleted_at IS NULL", (pid,)).fetchall()
            actions = conn.execute("SELECT id,title,status,due_date FROM action_items WHERE project_id=? AND deleted_at IS NULL", (pid,)).fetchall()
            risks = conn.execute("SELECT id,title,probability*impact score,status FROM risk_issues WHERE project_id=? AND deleted_at IS NULL ORDER BY score DESC", (pid,)).fetchall()
        meta = f"{render_badge(p['status'])} {render_badge(p['priority'])} {render_badge('Owner: '+p['owner_name'],'muted')}"
        actions_html = delete_action("projects", pid, True) if can_delete(user, p["owner_user_id"]) else ""
        body = render_page_header(f"Project: {p['title']}", "Project details and linked execution", "/projects")
        body += render_card("Summary", f"<p>{e(p['short_description'])}</p><p>{meta}</p><p>Timeline: {e(p['start_date'])} → {e(p['target_date'])}</p>", actions_html)
        body += render_card("Linked decisions", "<ul>" + ("".join([f"<li>{e(d['title'])} {render_badge(d['status'])}</li>" for d in decisions]) or "<li class='empty'>No linked decisions.</li>") + "</ul>")
        body += render_card("Action items", "<ul>" + ("".join([f"<li>{e(a['title'])} {render_badge(a['status'])} due {e(a['due_date'])}</li>" for a in actions]) or "<li class='empty'>No linked actions.</li>") + "</ul>")
        body += render_card("Risks & issues", "<ul>" + ("".join([f"<li>{e(r['title'])} score {r['score']} {render_badge(r['status'])}</li>" for r in risks]) or "<li class='empty'>No linked risks/issues.</li>") + "</ul>")
        return respond(start_response, layout(user, f"Project: {p['title']}", body, toast_message(environ), "/projects"))

    if path == "/decisions":
        if method == "POST":
            f = parse_form(environ)
            owner = user.id if user.role == "MEMBER" else int(f.get("owner_user_id", user.id))
            with get_conn() as conn:
                conn.execute("INSERT INTO decisions(project_id,title,decision_type,status,owner_user_id,approver,rationale,options_considered,due_date,impact_level) VALUES(?,?,?,?,?,?,?,?,?,?)", (f.get("project_id") or None, f.get("title", ""), safe_choice(f.get("decision_type", "STRATEGIC"), DECISION_TYPES, "STRATEGIC"), safe_choice(f.get("status", "PROPOSED"), DECISION_STATUS, "PROPOSED"), owner, f.get("approver", ""), f.get("rationale", ""), f.get("options_considered", ""), f.get("due_date") or None, safe_choice(f.get("impact_level", "MED"), IMPACT_LEVELS, "MED")))
        with get_conn() as conn:
            rows = conn.execute("SELECT d.*,u.name owner_name,COALESCE(p.title,'Org-level') project_title FROM decisions d JOIN users u ON u.id=d.owner_user_id LEFT JOIN projects p ON p.id=d.project_id WHERE d.deleted_at IS NULL ORDER BY d.updated_at DESC").fetchall()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1 ORDER BY name").fetchall()
            projects = conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL ORDER BY title").fetchall()
        if user.role == "MEMBER":
            rows = [r for r in rows if r["owner_user_id"] == user.id]
        decision_rows = []
        for d in rows:
            close = ""
            if can_edit_owned_or_admin(user, d["owner_user_id"]):
                close = f"<form method='POST' action='/decisions/{d['id']}/status' onsubmit=\"var o=prompt('Decision outcome (required)');if(!o)return false;var dt=prompt('Decision date YYYY-MM-DD','{date.today()}');if(!dt)return false;this.decision_outcome.value=o;this.decision_date.value=dt;return true;\"><input type='hidden' name='status' value='DECIDED'><input type='hidden' name='decision_outcome'><input type='hidden' name='decision_date'><button class='btn btn-sm'>Mark DECIDED</button></form>"
            ddel = delete_action("decisions", d["id"], True) if can_delete(user, d["owner_user_id"]) else ""
            links = action_links(f"/decisions/{d['id']}", f"/decisions/{d['id']}/edit" if can_edit_entity(user,'decisions', d['owner_user_id']) else None, ddel, close)
            decision_rows.append(f"<tr><td><a href='/decisions/{d['id']}'>{e(d['title'])}</a></td><td>{render_badge(d['status'])}</td><td>{e(d['owner_name'])}</td><td>{render_badge(d['project_title'],'muted')}</td><td class='actions'>{links}</td></tr>")
        table = render_table(["Decision", "Status", "Owner", "Project", "Actions"], "".join(decision_rows), "No decisions found.")
        uopts = "".join([f"<option value='{u['id']}'>{e(u['name'])}</option>" for u in users])
        popts = "".join([f"<option value='{p['id']}'>{e(p['title'])}</option>" for p in projects])
        form = f"<form method='POST' class='form-grid'><div class='field'><label>Decision statement</label><input name='title' required></div><div class='field'><label>Project</label><select name='project_id'><option value=''>Org-level</option>{popts}</select></div><div class='field'><label>Type</label><select name='decision_type'><option>STRATEGIC</option><option>FINANCIAL</option><option>OPERATIONAL</option><option>GOVERNANCE</option></select></div><div class='field'><label>Status</label><select name='status'><option>PROPOSED</option><option>REVISIT</option></select></div><div class='field'><label>Owner</label><select name='owner_user_id'>{uopts}</select></div><div class='field'><label>Approver</label><input name='approver'></div><div class='field'><label>Due date</label><input type='date' name='due_date'></div><div class='field'><label>Impact level</label><select name='impact_level'><option>LOW</option><option>MED</option><option>HIGH</option></select></div><div class='field'><label>Rationale</label><textarea name='rationale'></textarea></div><div class='field'><label>Options considered</label><textarea name='options_considered'></textarea></div><div class='field'><label>&nbsp;</label><button class='btn'>Create decision</button></div></form>"
        body = render_page_header("Decisions", "Track executive decision lifecycle") + render_card("Decision register", table) + render_card("New decision", form)
        return respond(start_response, layout(user, "Decisions", body, toast_message(environ), path))


    if path.startswith('/decisions/') and path.endswith('/edit'):
        did = int(path.split('/')[2])
        with get_conn() as conn:
            d = conn.execute("SELECT * FROM decisions WHERE id=? AND deleted_at IS NULL", (did,)).fetchone()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1 ORDER BY name").fetchall()
            projects = conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL ORDER BY title").fetchall()
        if not d:
            return respond(start_response, layout(user, 'Not found', render_card('Missing', 'Decision not found.'), active_path=path), '404 Not Found')
        if not can_edit_entity(user, 'decisions', d['owner_user_id']):
            return respond(start_response, layout(user, 'Forbidden', render_card('Not allowed', ''), active_path=path), '403 Forbidden')
        if method == 'POST':
            f = parse_form(environ)
            owner = d['owner_user_id'] if user.role == 'MEMBER' else int(f.get('owner_user_id', d['owner_user_id']))
            with get_conn() as conn:
                conn.execute("UPDATE decisions SET project_id=?,title=?,decision_type=?,owner_user_id=?,approver=?,rationale=?,options_considered=?,due_date=?,impact_level=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (
                    f.get('project_id') or None, f.get('title',''), safe_choice(f.get('decision_type','STRATEGIC'), DECISION_TYPES,'STRATEGIC'), owner, f.get('approver',''), f.get('rationale',''), f.get('options_considered',''), f.get('due_date') or None, safe_choice(f.get('impact_level','MED'), IMPACT_LEVELS,'MED'), did
                ))
            return redirect(start_response, f"/decisions/{did}?toast=Saved")
        uopts=''.join([f"<option value='{u['id']}' {'selected' if u['id']==d['owner_user_id'] else ''}>{e(u['name'])}</option>" for u in users]); popts=''.join([f"<option value='{p['id']}' {'selected' if d['project_id']==p['id'] else ''}>{e(p['title'])}</option>" for p in projects])
        ro = "<p><b>Decision outcome:</b> " + e(d['decision_outcome']) + "</p><p><b>Decision date:</b> " + e(d['decision_date']) + "</p>" if d['status']=='DECIDED' and user.role!='ADMIN' else ''
        form=f"<form method='POST' class='form-grid'><div class='field'><label>Project</label><select name='project_id'><option value=''>Org-level</option>{popts}</select></div><div class='field'><label>Title</label><input name='title' value='{e(d['title'])}' required></div><div class='field'><label>Type</label><select name='decision_type'>" + ''.join([f"<option {'selected' if d['decision_type']==x else ''}>{x}</option>" for x in ['STRATEGIC','FINANCIAL','OPERATIONAL','GOVERNANCE']]) + f"</select></div><div class='field'><label>Owner</label><select name='owner_user_id'>{uopts}</select></div><div class='field'><label>Approver</label><input name='approver' value='{e(d['approver'])}'></div><div class='field'><label>Due date</label><input type='date' name='due_date' value='{e(d['due_date'])}'></div><div class='field'><label>Impact</label><select name='impact_level'>" + ''.join([f"<option {'selected' if d['impact_level']==x else ''}>{x}</option>" for x in ['LOW','MED','HIGH']]) + f"</select></div><div class='field'><label>Rationale</label><textarea name='rationale'>{e(d['rationale'])}</textarea></div><div class='field'><label>Options considered</label><textarea name='options_considered'>{e(d['options_considered'])}</textarea></div><div class='field'><label>&nbsp;</label><button class='btn'>Save</button></div></form>" + ro
        body = render_page_header(f"Edit Decision: {d['title']}", "Update decision record", f"/decisions/{did}") + render_card('Edit decision', form)
        return respond(start_response, layout(user, 'Edit Decision', body, active_path='/decisions'))

    if path.startswith('/decisions/') and method == 'GET' and path.count('/') == 2:
        did = int(path.split('/')[2])
        with get_conn() as conn:
            d = conn.execute("SELECT d.*,u.name owner_name,COALESCE(p.title,'Org-level') project_title FROM decisions d JOIN users u ON u.id=d.owner_user_id LEFT JOIN projects p ON p.id=d.project_id WHERE d.id=? AND d.deleted_at IS NULL", (did,)).fetchone()
            linked_actions = conn.execute("SELECT id,title,status,due_date FROM action_items WHERE decision_id=? AND deleted_at IS NULL", (did,)).fetchall()
        if not d:
            return respond(start_response, layout(user, 'Not found', render_card('Missing', 'Decision not found.'), active_path=path), '404 Not Found')
        if not can_view_owned_or_admin_exec(user, d['owner_user_id']):
            return respond(start_response, layout(user, 'Forbidden', render_card('Not allowed', ''), active_path=path), '403 Forbidden')
        actions = ''
        if can_edit_entity(user, 'decisions', d['owner_user_id']):
            actions += f"<a class='btn btn-sm' href='/decisions/{did}/edit'>Edit</a>"
        if can_delete(user, d['owner_user_id']):
            actions += delete_action('decisions', did, True)
        body = render_page_header(f"Decision: {d['title']}", 'Decision detail', '/decisions')
        body += render_card('Summary', f"<p>{render_badge(d['status'])} {render_badge(d['decision_type'])} {render_badge(d['project_title'],'muted')}</p><p><b>Owner:</b> {e(d['owner_name'])}</p><p><b>Approver:</b> {e(d['approver'])}</p><p><b>Rationale:</b> {e(d['rationale'])}</p><p><b>Options:</b> {e(d['options_considered'])}</p><p><b>Outcome:</b> {e(d['decision_outcome'])}</p><p><b>Decision date:</b> {e(d['decision_date'])}</p>", actions)
        body += render_card('Linked action items', '<ul>' + (''.join([f"<li><a href='/actions/{a['id']}'>{e(a['title'])}</a> {render_badge(a['status'])} due {e(a['due_date'])}</li>" for a in linked_actions]) or "<li class='empty'>No linked actions.</li>") + '</ul>')
        return respond(start_response, layout(user, 'Decision Detail', body, toast_message(environ), '/decisions'))

    if path.startswith('/decisions/') and path.endswith('/status') and method == 'POST':
        did = int(path.split('/')[2]); f = parse_form(environ)
        with get_conn() as conn:
            d = conn.execute("SELECT * FROM decisions WHERE id=? AND deleted_at IS NULL", (did,)).fetchone()
            if not d or not can_edit_owned_or_admin(user, d['owner_user_id']):
                return respond(start_response, layout(user, "Forbidden", render_card("Not allowed", ""), active_path=path), "403 Forbidden")
            ok, msg = validate_decision_transition(d['status'], f.get('status', ''), f.get('decision_outcome', ''), f.get('decision_date'))
            if not ok:
                return respond(start_response, layout(user, "Invalid", render_card("Transition blocked", e(msg)), active_path=path), "400 Bad Request")
            conn.execute("UPDATE decisions SET status=?,decision_date=?,decision_outcome=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (f.get('status'), f.get('decision_date') or d['decision_date'], f.get('decision_outcome') or d['decision_outcome'], did))
        return redirect(start_response, "/decisions?toast=Decision%20marked%20as%20DECIDED")

    if path.startswith('/decisions/') and path.endswith('/delete'):
        if parse_qs(environ.get("QUERY_STRING", "")).get("confirmed", [""])[0] != "1":
            did = int(path.split('/')[2]); return redirect(start_response, f"/confirm-delete?entity=decisions&id={did}")
        did = int(path.split('/')[2])
        with get_conn() as conn:
            d = conn.execute("SELECT owner_user_id FROM decisions WHERE id=? AND deleted_at IS NULL", (did,)).fetchone()
            if not d or not can_delete(user, d['owner_user_id']):
                return respond(start_response, layout(user, "Forbidden", render_card("Not allowed", ""), active_path=path), "403 Forbidden")
            conn.execute("UPDATE decisions SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (did,))
            conn.execute("UPDATE action_items SET deleted_at=CURRENT_TIMESTAMP WHERE decision_id=?", (did,))
        return redirect(start_response, "/decisions?toast=Decision%20deleted")

    if path == '/actions':
        if method == 'POST':
            f = parse_form(environ)
            owner = user.id if user.role == 'MEMBER' else int(f.get('owner_user_id', user.id))
            with get_conn() as conn:
                conn.execute("INSERT INTO action_items(project_id,decision_id,title,owner_user_id,status,due_date,notes) VALUES(?,?,?,?,?,?,?)", (f.get('project_id') or None, f.get('decision_id') or None, f.get('title', ''), owner, safe_choice(f.get('status', 'OPEN'), ACTION_STATUS, 'OPEN'), f.get('due_date') or None, f.get('notes', '')))
        with get_conn() as conn:
            rows = conn.execute("SELECT a.*,u.name owner_name,COALESCE(p.title,'-') project_title FROM action_items a JOIN users u ON u.id=a.owner_user_id LEFT JOIN projects p ON p.id=a.project_id WHERE a.deleted_at IS NULL ORDER BY a.status,a.due_date").fetchall()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1").fetchall(); projects = conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL").fetchall(); decisions = conn.execute("SELECT id,title FROM decisions WHERE deleted_at IS NULL").fetchall()
        if user.role == 'MEMBER': rows = [r for r in rows if r['owner_user_id'] == user.id]
        action_rows = []
        for r in rows:
            links = action_links(f"/actions/{r['id']}", f"/actions/{r['id']}/edit" if can_edit_entity(user,'actions', r['owner_user_id']) else None, delete_action('actions', r['id'], False) if can_delete(user, r['owner_user_id']) else '')
            action_rows.append(f"<tr><td><a href='/actions/{r['id']}'>{e(r['title'])}</a></td><td>{render_badge(r['status'])}</td><td>{e(r['owner_name'])}</td><td>{e(r['due_date'])}</td><td>{e(r['project_title'])}</td><td class='actions'>{links}</td></tr>")
        rows_html = ''.join(action_rows)
        table = render_table(["Title", "Status", "Owner", "Due", "Project", "Actions"], rows_html, "No action items found.")
        uopts=''.join([f"<option value='{u['id']}'>{e(u['name'])}</option>" for u in users]); popts=''.join([f"<option value='{p['id']}'>{e(p['title'])}</option>" for p in projects]); dopts=''.join([f"<option value='{d['id']}'>{e(d['title'])}</option>" for d in decisions])
        form = f"<form method='POST' class='form-grid'><div class='field'><label>Title</label><input name='title' required></div><div class='field'><label>Owner</label><select name='owner_user_id'>{uopts}</select></div><div class='field'><label>Project</label><select name='project_id'><option value=''>None</option>{popts}</select></div><div class='field'><label>Decision</label><select name='decision_id'><option value=''>None</option>{dopts}</select></div><div class='field'><label>Status</label><select name='status'><option>OPEN</option><option>IN_PROGRESS</option><option>DONE</option><option>CANCELLED</option></select></div><div class='field'><label>Due date</label><input type='date' name='due_date'></div><div class='field'><label>Notes</label><textarea name='notes'></textarea></div><div class='field'><label>&nbsp;</label><button class='btn'>Create action</button></div></form>"
        body = render_page_header("Action Items", "Operational execution queue") + render_card("Action list", table) + render_card("New action item", form)
        return respond(start_response, layout(user, 'Action Items', body, toast_message(environ), path))


    if path.startswith('/actions/') and path.endswith('/edit'):
        aid = int(path.split('/')[2])
        with get_conn() as conn:
            a = conn.execute("SELECT * FROM action_items WHERE id=? AND deleted_at IS NULL", (aid,)).fetchone()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1 ORDER BY name").fetchall(); projects = conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL ORDER BY title").fetchall(); decisions = conn.execute("SELECT id,title FROM decisions WHERE deleted_at IS NULL ORDER BY title").fetchall()
        if not a:
            return respond(start_response, layout(user,'Not found',render_card('Missing','Action not found.'),active_path=path),'404 Not Found')
        if not can_edit_entity(user,'actions', a['owner_user_id']):
            return respond(start_response, layout(user,'Forbidden',render_card('Not allowed',''),active_path=path),'403 Forbidden')
        if method == 'POST':
            f=parse_form(environ)
            owner = a['owner_user_id'] if user.role=='MEMBER' else int(f.get('owner_user_id', a['owner_user_id']))
            with get_conn() as conn:
                conn.execute("UPDATE action_items SET project_id=?,decision_id=?,title=?,owner_user_id=?,status=?,due_date=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (f.get('project_id') or None, f.get('decision_id') or None, f.get('title',''), owner, safe_choice(f.get('status','OPEN'), ACTION_STATUS,'OPEN'), f.get('due_date') or None, f.get('notes',''), aid))
            return redirect(start_response, f"/actions/{aid}?toast=Saved")
        uopts=''.join([f"<option value='{u['id']}' {'selected' if u['id']==a['owner_user_id'] else ''}>{e(u['name'])}</option>" for u in users]); popts=''.join([f"<option value='{p['id']}' {'selected' if a['project_id']==p['id'] else ''}>{e(p['title'])}</option>" for p in projects]); dopts=''.join([f"<option value='{d['id']}' {'selected' if a['decision_id']==d['id'] else ''}>{e(d['title'])}</option>" for d in decisions])
        form=f"<form method='POST' class='form-grid'><div class='field'><label>Title</label><input name='title' value='{e(a['title'])}' required></div><div class='field'><label>Owner</label><select name='owner_user_id'>{uopts}</select></div><div class='field'><label>Project</label><select name='project_id'><option value=''>None</option>{popts}</select></div><div class='field'><label>Decision</label><select name='decision_id'><option value=''>None</option>{dopts}</select></div><div class='field'><label>Status</label><select name='status'>" + ''.join([f"<option {'selected' if a['status']==x else ''}>{x}</option>" for x in ['OPEN','IN_PROGRESS','DONE','CANCELLED']]) + f"</select></div><div class='field'><label>Due date</label><input type='date' name='due_date' value='{e(a['due_date'])}'></div><div class='field'><label>Notes</label><textarea name='notes'>{e(a['notes'])}</textarea></div><div class='field'><label>&nbsp;</label><button class='btn'>Save</button></div></form>"
        body = render_page_header(f"Edit Action: {a['title']}", 'Update action item', f"/actions/{aid}") + render_card('Edit action', form)
        return respond(start_response, layout(user,'Edit Action',body,active_path='/actions'))

    if path.startswith('/actions/') and method == 'GET' and path.count('/') == 2:
        aid = int(path.split('/')[2])
        with get_conn() as conn:
            a = conn.execute("SELECT a.*,u.name owner_name,COALESCE(p.title,'-') project_title,COALESCE(d.title,'-') decision_title FROM action_items a JOIN users u ON u.id=a.owner_user_id LEFT JOIN projects p ON p.id=a.project_id LEFT JOIN decisions d ON d.id=a.decision_id WHERE a.id=? AND a.deleted_at IS NULL", (aid,)).fetchone()
        if not a:
            return respond(start_response, layout(user,'Not found',render_card('Missing','Action not found.'),active_path=path),'404 Not Found')
        if not can_view_owned_or_admin_exec(user, a['owner_user_id']):
            return respond(start_response, layout(user,'Forbidden',render_card('Not allowed',''),active_path=path),'403 Forbidden')
        actions = (f"<a class='btn btn-sm' href='/actions/{aid}/edit'>Edit</a>" if can_edit_entity(user,'actions',a['owner_user_id']) else '') + (delete_action('actions', aid, False) if can_delete(user,a['owner_user_id']) else '')
        body = render_page_header(f"Action: {a['title']}", 'Action item details', '/actions') + render_card('Summary', f"<p>{render_badge(a['status'])}</p><p><b>Owner:</b> {e(a['owner_name'])}</p><p><b>Project:</b> {e(a['project_title'])}</p><p><b>Decision:</b> {e(a['decision_title'])}</p><p><b>Due:</b> {e(a['due_date'])}</p><p><b>Notes:</b> {e(a['notes'])}</p>", actions)
        return respond(start_response, layout(user,'Action Detail',body,toast_message(environ),'/actions'))

    if path.startswith('/actions/') and path.endswith('/delete') and method == 'POST':
        aid = int(path.split('/')[2])
        with get_conn() as conn:
            a = conn.execute("SELECT owner_user_id FROM action_items WHERE id=? AND deleted_at IS NULL", (aid,)).fetchone()
            if not a or not can_delete(user, a['owner_user_id']):
                return respond(start_response, layout(user, "Forbidden", render_card("Not allowed", ""), active_path=path), "403 Forbidden")
            conn.execute("UPDATE action_items SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (aid,))
        return redirect(start_response, '/actions?toast=Action%20item%20deleted')

    if path == '/risks':
        if method == 'POST':
            f = parse_form(environ)
            with get_conn() as conn:
                conn.execute("INSERT INTO risk_issues(project_id,type,title,description,probability,impact,status,owner_user_id,mitigation_plan,due_date) VALUES(?,?,?,?,?,?,?,?,?,?)", (int(f.get('project_id')), safe_choice(f.get('type', 'RISK'), RISK_TYPES, 'RISK'), f.get('title', ''), f.get('description', ''), max(1, min(5, int(f.get('probability', '3')))), max(1, min(5, int(f.get('impact', '3')))), safe_choice(f.get('status', 'OPEN'), RISK_STATUS, 'OPEN'), int(f.get('owner_user_id', user.id)), f.get('mitigation_plan', ''), f.get('due_date') or None))
        with get_conn() as conn:
            rows = conn.execute("SELECT r.*,u.name owner_name,p.title project_title,(probability*impact) score FROM risk_issues r JOIN users u ON u.id=r.owner_user_id JOIN projects p ON p.id=r.project_id WHERE r.deleted_at IS NULL AND p.deleted_at IS NULL ORDER BY score DESC").fetchall(); users = conn.execute("SELECT id,name FROM users WHERE is_active=1").fetchall(); projects = conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL").fetchall()
        risk_rows = []
        for r in rows:
            links = action_links(f"/risks/{r['id']}", f"/risks/{r['id']}/edit" if can_edit_entity(user,'risks', r['owner_user_id']) else None, delete_action('risks', r['id'], False) if can_delete(user, r['owner_user_id']) else '')
            risk_rows.append(f"<tr><td><a href='/risks/{r['id']}'>{e(r['title'])}</a></td><td>{render_badge(r['type'])}</td><td>{r['score']}</td><td>{render_badge(r['status'])}</td><td>{e(r['project_title'])}</td><td class='actions'>{links}</td></tr>")
        rows_html = ''.join(risk_rows)
        table = render_table(["Title", "Type", "Score", "Status", "Project", "Actions"], rows_html, "No risks/issues found.")
        uopts=''.join([f"<option value='{u['id']}'>{e(u['name'])}</option>" for u in users]); popts=''.join([f"<option value='{p['id']}'>{e(p['title'])}</option>" for p in projects])
        form = f"<form method='POST' class='form-grid'><div class='field'><label>Title</label><input name='title' required></div><div class='field'><label>Type</label><select name='type'><option>RISK</option><option>ISSUE</option></select></div><div class='field'><label>Project</label><select name='project_id'>{popts}</select></div><div class='field'><label>Owner</label><select name='owner_user_id'>{uopts}</select></div><div class='field'><label>Probability (1-5)</label><input type='number' min='1' max='5' name='probability' value='3'></div><div class='field'><label>Impact (1-5)</label><input type='number' min='1' max='5' name='impact' value='3'></div><div class='field'><label>Due date</label><input type='date' name='due_date'></div><div class='field'><label>Status</label><select name='status'><option>OPEN</option><option>MITIGATED</option><option>CLOSED</option></select></div><div class='field'><label>Description</label><textarea name='description'></textarea></div><div class='field'><label>Mitigation plan</label><textarea name='mitigation_plan'></textarea></div><div class='field'><label>&nbsp;</label><button class='btn'>Create risk/issue</button></div></form>"
        body = render_page_header("Risks & Issues", "Monitor delivery exposure and blockers") + render_card("Risk register", table) + render_card("New risk/issue", form)
        return respond(start_response, layout(user, 'Risks & Issues', body, toast_message(environ), path))


    if path.startswith('/risks/') and path.endswith('/edit'):
        rid = int(path.split('/')[2])
        with get_conn() as conn:
            r = conn.execute("SELECT * FROM risk_issues WHERE id=? AND deleted_at IS NULL", (rid,)).fetchone()
            users = conn.execute("SELECT id,name FROM users WHERE is_active=1 ORDER BY name").fetchall(); projects = conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL ORDER BY title").fetchall()
        if not r:
            return respond(start_response, layout(user,'Not found',render_card('Missing','Risk/Issue not found.'),active_path=path),'404 Not Found')
        if not can_edit_entity(user,'risks', r['owner_user_id']):
            return respond(start_response, layout(user,'Forbidden',render_card('Not allowed',''),active_path=path),'403 Forbidden')
        if method == 'POST':
            f=parse_form(environ)
            owner = r['owner_user_id'] if user.role=='MEMBER' else int(f.get('owner_user_id', r['owner_user_id']))
            with get_conn() as conn:
                conn.execute("UPDATE risk_issues SET project_id=?,type=?,title=?,description=?,probability=?,impact=?,status=?,owner_user_id=?,mitigation_plan=?,due_date=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(f.get('project_id')), safe_choice(f.get('type','RISK'),RISK_TYPES,'RISK'), f.get('title',''), f.get('description',''), max(1,min(5,int(f.get('probability','3')))), max(1,min(5,int(f.get('impact','3')))), safe_choice(f.get('status','OPEN'),RISK_STATUS,'OPEN'), owner, f.get('mitigation_plan',''), f.get('due_date') or None, rid))
            return redirect(start_response, f"/risks/{rid}?toast=Saved")
        uopts=''.join([f"<option value='{u['id']}' {'selected' if u['id']==r['owner_user_id'] else ''}>{e(u['name'])}</option>" for u in users]); popts=''.join([f"<option value='{p['id']}' {'selected' if r['project_id']==p['id'] else ''}>{e(p['title'])}</option>" for p in projects])
        form=f"<form method='POST' class='form-grid'><div class='field'><label>Title</label><input name='title' value='{e(r['title'])}' required></div><div class='field'><label>Type</label><select name='type'>" + ''.join([f"<option {'selected' if r['type']==x else ''}>{x}</option>" for x in ['RISK','ISSUE']]) + f"</select></div><div class='field'><label>Project</label><select name='project_id'>{popts}</select></div><div class='field'><label>Owner</label><select name='owner_user_id'>{uopts}</select></div><div class='field'><label>Probability</label><input type='number' min='1' max='5' name='probability' value='{r['probability']}'></div><div class='field'><label>Impact</label><input type='number' min='1' max='5' name='impact' value='{r['impact']}'></div><div class='field'><label>Status</label><select name='status'>" + ''.join([f"<option {'selected' if r['status']==x else ''}>{x}</option>" for x in ['OPEN','MITIGATED','CLOSED']]) + f"</select></div><div class='field'><label>Due date</label><input type='date' name='due_date' value='{e(r['due_date'])}'></div><div class='field'><label>Description</label><textarea name='description'>{e(r['description'])}</textarea></div><div class='field'><label>Mitigation</label><textarea name='mitigation_plan'>{e(r['mitigation_plan'])}</textarea></div><div class='field'><label>&nbsp;</label><button class='btn'>Save</button></div></form>"
        body = render_page_header(f"Edit Risk/Issue: {r['title']}", 'Update risk register item', f"/risks/{rid}") + render_card('Edit risk/issue', form)
        return respond(start_response, layout(user,'Edit Risk',body,active_path='/risks'))

    if path.startswith('/risks/') and method == 'GET' and path.count('/') == 2:
        rid = int(path.split('/')[2])
        with get_conn() as conn:
            r = conn.execute("SELECT r.*,u.name owner_name,p.title project_title,(r.probability*r.impact) score FROM risk_issues r JOIN users u ON u.id=r.owner_user_id JOIN projects p ON p.id=r.project_id WHERE r.id=? AND r.deleted_at IS NULL", (rid,)).fetchone()
        if not r:
            return respond(start_response, layout(user,'Not found',render_card('Missing','Risk/Issue not found.'),active_path=path),'404 Not Found')
        if not can_view_owned_or_admin_exec(user, r['owner_user_id']):
            return respond(start_response, layout(user,'Forbidden',render_card('Not allowed',''),active_path=path),'403 Forbidden')
        actions = (f"<a class='btn btn-sm' href='/risks/{rid}/edit'>Edit</a>" if can_edit_entity(user,'risks',r['owner_user_id']) else '') + (delete_action('risks', rid, False) if can_delete(user,r['owner_user_id']) else '')
        body = render_page_header(f"Risk/Issue: {r['title']}", 'Risk detail', '/risks') + render_card('Summary', f"<p>{render_badge(r['type'])} {render_badge(r['status'])} {render_badge('Score '+str(r['score']),'amber')}</p><p><b>Project:</b> {e(r['project_title'])}</p><p><b>Owner:</b> {e(r['owner_name'])}</p><p><b>Description:</b> {e(r['description'])}</p><p><b>Mitigation:</b> {e(r['mitigation_plan'])}</p><p><b>Due:</b> {e(r['due_date'])}</p>", actions)
        return respond(start_response, layout(user,'Risk Detail',body,toast_message(environ),'/risks'))

    if path.startswith('/risks/') and path.endswith('/delete') and method == 'POST':
        rid = int(path.split('/')[2])
        with get_conn() as conn:
            r = conn.execute("SELECT owner_user_id FROM risk_issues WHERE id=? AND deleted_at IS NULL", (rid,)).fetchone()
            if not r or not can_delete(user, r['owner_user_id']):
                return respond(start_response, layout(user, "Forbidden", render_card("Not allowed", ""), active_path=path), "403 Forbidden")
            conn.execute("UPDATE risk_issues SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (rid,))
        return redirect(start_response, '/risks?toast=Risk%2FIssue%20deleted')

    if path == '/stakeholders':
        if method == 'POST':
            f = parse_form(environ)
            with get_conn() as conn:
                conn.execute("INSERT INTO stakeholders(name,title,org_unit,contact,influence_level,stance,notes) VALUES(?,?,?,?,?,?,?)", (f.get('name', ''), f.get('title', ''), f.get('org_unit', ''), f.get('contact') or None, safe_choice(f.get('influence_level', 'MED'), IMPACT_LEVELS, 'MED'), safe_choice(f.get('stance', 'NEUTRAL'), STANCE, 'NEUTRAL'), f.get('notes', '')))
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM stakeholders WHERE deleted_at IS NULL ORDER BY created_at DESC").fetchall()
        stakeholder_rows = []
        for s in rows:
            links = action_links(f"/stakeholders/{s['id']}", f"/stakeholders/{s['id']}/edit" if user.role in {'ADMIN','EXEC'} else None, delete_action('stakeholders', s['id'], False) if user.role=='ADMIN' else '')
            stakeholder_rows.append(f"<tr><td><a href='/stakeholders/{s['id']}'>{e(s['name'])}</a></td><td>{e(s['title'])}</td><td>{e(s['org_unit'])}</td><td>{render_badge(s['influence_level'])}</td><td>{render_badge(s['stance'])}</td><td class='actions'>{links}</td></tr>")
        rows_html = ''.join(stakeholder_rows)
        table = render_table(["Name", "Title", "Org Unit", "Influence", "Stance", "Actions"], rows_html, "No stakeholders found.")
        form = "<form method='POST' class='form-grid'><div class='field'><label>Name</label><input name='name' required></div><div class='field'><label>Title</label><input name='title'></div><div class='field'><label>Org unit</label><input name='org_unit'></div><div class='field'><label>Contact</label><input name='contact'></div><div class='field'><label>Influence</label><select name='influence_level'><option>LOW</option><option>MED</option><option>HIGH</option></select></div><div class='field'><label>Stance</label><select name='stance'><option>SUPPORTIVE</option><option>NEUTRAL</option><option>RESISTANT</option></select></div><div class='field'><label>Notes</label><textarea name='notes'></textarea></div><div class='field'><label>&nbsp;</label><button class='btn'>Create stakeholder</button></div></form>"
        body = render_page_header("Stakeholders", "Map influence and alignment") + render_card("Directory", table) + render_card("New stakeholder", form)
        return respond(start_response, layout(user, 'Stakeholders', body, toast_message(environ), path))


    if path.startswith('/stakeholders/') and path.endswith('/edit'):
        sid = int(path.split('/')[2])
        with get_conn() as conn:
            st = conn.execute("SELECT * FROM stakeholders WHERE id=? AND deleted_at IS NULL", (sid,)).fetchone()
        if not st:
            return respond(start_response, layout(user,'Not found',render_card('Missing','Stakeholder not found.'),active_path=path),'404 Not Found')
        if user.role not in {'ADMIN','EXEC'}:
            return respond(start_response, layout(user,'Forbidden',render_card('Not allowed',''),active_path=path),'403 Forbidden')
        if method == 'POST':
            f=parse_form(environ)
            with get_conn() as conn:
                conn.execute("UPDATE stakeholders SET name=?,title=?,org_unit=?,contact=?,influence_level=?,stance=?,notes=? WHERE id=?", (f.get('name',''), f.get('title',''), f.get('org_unit',''), f.get('contact') or None, safe_choice(f.get('influence_level','MED'), IMPACT_LEVELS,'MED'), safe_choice(f.get('stance','NEUTRAL'), STANCE,'NEUTRAL'), f.get('notes',''), sid))
            return redirect(start_response, f"/stakeholders/{sid}?toast=Saved")
        form=f"<form method='POST' class='form-grid'><div class='field'><label>Name</label><input name='name' value='{e(st['name'])}' required></div><div class='field'><label>Title</label><input name='title' value='{e(st['title'])}'></div><div class='field'><label>Org unit</label><input name='org_unit' value='{e(st['org_unit'])}'></div><div class='field'><label>Contact</label><input name='contact' value='{e(st['contact'])}'></div><div class='field'><label>Influence</label><select name='influence_level'>" + ''.join([f"<option {'selected' if st['influence_level']==x else ''}>{x}</option>" for x in ['LOW','MED','HIGH']]) + f"</select></div><div class='field'><label>Stance</label><select name='stance'>" + ''.join([f"<option {'selected' if st['stance']==x else ''}>{x}</option>" for x in ['SUPPORTIVE','NEUTRAL','RESISTANT']]) + f"</select></div><div class='field'><label>Notes</label><textarea name='notes'>{e(st['notes'])}</textarea></div><div class='field'><label>&nbsp;</label><button class='btn'>Save</button></div></form>"
        body = render_page_header(f"Edit Stakeholder: {st['name']}", 'Update stakeholder profile', f"/stakeholders/{sid}") + render_card('Edit stakeholder', form)
        return respond(start_response, layout(user,'Edit Stakeholder',body,active_path='/stakeholders'))

    if path.startswith('/stakeholders/') and method == 'GET' and path.count('/') == 2:
        sid = int(path.split('/')[2])
        with get_conn() as conn:
            st = conn.execute("SELECT * FROM stakeholders WHERE id=? AND deleted_at IS NULL", (sid,)).fetchone()
            links = conn.execute("SELECT p.id,p.title,ps.relationship_notes FROM project_stakeholders ps JOIN projects p ON p.id=ps.project_id WHERE ps.stakeholder_id=? AND ps.deleted_at IS NULL AND p.deleted_at IS NULL", (sid,)).fetchall()
        if not st:
            return respond(start_response, layout(user,'Not found',render_card('Missing','Stakeholder not found.'),active_path=path),'404 Not Found')
        if user.role == 'MEMBER':
            return respond(start_response, layout(user,'Forbidden',render_card('Not allowed',''),active_path=path),'403 Forbidden')
        actions = (f"<a class='btn btn-sm' href='/stakeholders/{sid}/edit'>Edit</a>" if user.role in {'ADMIN','EXEC'} else '') + (delete_action('stakeholders', sid, False) if user.role=='ADMIN' else '')
        linked = ''.join([f"<li><a href='/projects/{p['id']}'>{e(p['title'])}</a> — {e(p['relationship_notes'])}</li>" for p in links]) or "<li class='empty'>No linked projects.</li>"
        body = render_page_header(f"Stakeholder: {st['name']}", 'Stakeholder detail', '/stakeholders') + render_card('Summary', f"<p>{render_badge(st['influence_level'])} {render_badge(st['stance'])}</p><p><b>Title:</b> {e(st['title'])}</p><p><b>Org unit:</b> {e(st['org_unit'])}</p><p><b>Contact:</b> {e(st['contact'])}</p><p><b>Notes:</b> {e(st['notes'])}</p>", actions) + render_card('Linked projects', f"<ul>{linked}</ul>")
        return respond(start_response, layout(user,'Stakeholder Detail',body,toast_message(environ),'/stakeholders'))

    if path.startswith('/stakeholders/') and path.endswith('/delete') and method == 'POST':
        sid = int(path.split('/')[2])
        if user.role != 'ADMIN':
            return respond(start_response, layout(user, "Forbidden", render_card("Admins only", ""), active_path=path), "403 Forbidden")
        with get_conn() as conn:
            conn.execute("UPDATE stakeholders SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (sid,))
            conn.execute("UPDATE project_stakeholders SET deleted_at=CURRENT_TIMESTAMP WHERE stakeholder_id=?", (sid,))
        return redirect(start_response, '/stakeholders?toast=Stakeholder%20deleted')

    if path == '/search':
        q = parse_qs(environ.get('QUERY_STRING', '')).get('q', [''])[0]
        like = f"%{q}%"
        with get_conn() as conn:
            p = conn.execute("SELECT id,title FROM projects WHERE deleted_at IS NULL AND title LIKE ? LIMIT 10", (like,)).fetchall()
            d = conn.execute("SELECT id,title FROM decisions WHERE deleted_at IS NULL AND title LIKE ? LIMIT 10", (like,)).fetchall()
            a = conn.execute("SELECT id,title FROM action_items WHERE deleted_at IS NULL AND title LIKE ? LIMIT 10", (like,)).fetchall()
            r = conn.execute("SELECT id,title FROM risk_issues WHERE deleted_at IS NULL AND title LIKE ? LIMIT 10", (like,)).fetchall()
        rows = "".join([f"<tr><td>Project</td><td><a href='/projects/{x['id']}'>{e(x['title'])}</a></td></tr>" for x in p])
        rows += "".join([f"<tr><td>Decision</td><td><a href='/decisions/{x['id']}'>{e(x['title'])}</a></td></tr>" for x in d])
        rows += "".join([f"<tr><td>Action</td><td><a href='/actions/{x['id']}'>{e(x['title'])}</a></td></tr>" for x in a])
        rows += "".join([f"<tr><td>Risk/Issue</td><td><a href='/risks/{x['id']}'>{e(x['title'])}</a></td></tr>" for x in r])
        body = render_page_header("Search", f"Query: {q}") + render_card("Results", render_table(["Type", "Title"], rows, "No matches."))
        return respond(start_response, layout(user, 'Search', body, active_path=path))


    if path.startswith('/admin/users/') and method == 'GET' and path.count('/') == 3:
        if not can_manage_users(user):
            return respond(start_response, layout(user, 'Forbidden', render_card('Admins only',''), active_path=path), '403 Forbidden')
        uid = int(path.split('/')[3])
        with get_conn() as conn:
            u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return respond(start_response, layout(user,'Not found',render_card('Missing','User not found.'),active_path=path),'404 Not Found')
        actions = f"<a class='btn btn-sm' href='/admin/users/{uid}/edit'>Edit</a>"
        body = render_page_header(f"User: {u['name']}", 'User detail', '/admin/users') + render_card('Profile', f"<p><b>Email:</b> {e(u['email'])}</p><p><b>Role:</b> {render_badge(u['role'])}</p><p><b>Status:</b> {render_badge('ACTIVE' if u['is_active']==1 else 'DISABLED')}</p><p><b>Created:</b> {e(u['created_at'])}</p>", actions)
        return respond(start_response, layout(user,'User Detail',body,toast_message(environ),'/admin/users'))

    if path.startswith('/admin/users/') and path.endswith('/edit'):
        if not can_manage_users(user):
            return respond(start_response, layout(user, 'Forbidden', render_card('Admins only',''), active_path=path), '403 Forbidden')
        uid = int(path.split('/')[3])
        with get_conn() as conn:
            u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return respond(start_response, layout(user,'Not found',render_card('Missing','User not found.'),active_path=path),'404 Not Found')
        if method == 'POST':
            f = parse_form(environ)
            with get_conn() as conn:
                if uid == user.id and f.get('role') != 'ADMIN':
                    return respond(start_response, layout(user,'Invalid',render_card('Safety check','You cannot demote yourself.'),active_path=path), '400 Bad Request')
                if f.get('role') != 'ADMIN':
                    admins = conn.execute("SELECT COUNT(*) c FROM users WHERE role='ADMIN' AND is_active=1").fetchone()['c']
                    if u['role'] == 'ADMIN' and admins <= 1:
                        return respond(start_response, layout(user,'Invalid',render_card('Safety check','Cannot demote the last ADMIN.'),active_path=path), '400 Bad Request')
                conn.execute("UPDATE users SET name=?, role=?, is_active=? WHERE id=?", (f.get('name',''), safe_choice(f.get('role','MEMBER'), {'ADMIN','EXEC','MEMBER'}, 'MEMBER'), 1 if f.get('is_active') == '1' else 0, uid))
            return redirect(start_response, f"/admin/users/{uid}?toast=Saved")
        form=f"<form method='POST' class='form-grid'><div class='field'><label>Name</label><input name='name' value='{e(u['name'])}'></div><div class='field'><label>Role</label><select name='role'><option {'selected' if u['role']=='ADMIN' else ''}>ADMIN</option><option {'selected' if u['role']=='EXEC' else ''}>EXEC</option><option {'selected' if u['role']=='MEMBER' else ''}>MEMBER</option></select></div><div class='field'><label>Status</label><select name='is_active'><option value='1' {'selected' if u['is_active']==1 else ''}>active</option><option value='0' {'selected' if u['is_active']==0 else ''}>disabled</option></select></div><div class='field'><label>&nbsp;</label><button class='btn'>Save</button></div></form>"
        body = render_page_header(f"Edit User: {u['name']}", 'Admin edit user', f"/admin/users/{uid}") + render_card('Edit user', form)
        return respond(start_response, layout(user,'Edit User',body,active_path='/admin/users'))

    if path == '/admin/users':
        if not can_manage_users(user):
            return respond(start_response, layout(user, "Forbidden", render_card("Admins only", ""), active_path=path), '403 Forbidden')
        if method == 'POST':
            f = parse_form(environ)
            action = f.get('action')
            with get_conn() as conn:
                if action == 'create':
                    create_user(f.get('name', ''), f.get('email', ''), f.get('temp_password', ''), f.get('role', 'MEMBER'), must_change_password=True)
                elif action == 'edit':
                    uid = int(f['user_id'])
                    if uid == user.id and f.get('role') != 'ADMIN':
                        return respond(start_response, layout(user, "Invalid", render_card("Safety check", "You cannot demote yourself."), active_path=path), '400 Bad Request')
                    if f.get('role') != 'ADMIN':
                        admins = conn.execute("SELECT COUNT(*) c FROM users WHERE role='ADMIN' AND is_active=1").fetchone()['c']
                        current = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
                        if current and current['role'] == 'ADMIN' and admins <= 1:
                            return respond(start_response, layout(user, "Invalid", render_card("Safety check", "Cannot demote the last ADMIN."), active_path=path), '400 Bad Request')
                    conn.execute("UPDATE users SET name=?, role=?, is_active=? WHERE id=?", (f.get('name', ''), safe_choice(f.get('role', 'MEMBER'), {'ADMIN', 'EXEC', 'MEMBER'}, 'MEMBER'), 1 if f.get('is_active') == '1' else 0, uid))
                elif action == 'reset_password':
                    update_password(int(f['user_id']), f.get('temp_password', ''), must_change_password=True)
        q = parse_qs(environ.get('QUERY_STRING', '')).get('q', [''])[0]
        with get_conn() as conn:
            users = conn.execute("SELECT * FROM users WHERE name LIKE ? OR email LIKE ? ORDER BY created_at DESC", (f"%{q}%", f"%{q}%")).fetchall()
        rows = []
        for u in users:
            edit = f"<form method='POST' class='form-grid'><input type='hidden' name='action' value='edit'><input type='hidden' name='user_id' value='{u['id']}'><div class='field'><label>Name</label><input name='name' value='{e(u['name'])}'></div><div class='field'><label>Role</label><select name='role'><option {'selected' if u['role']=='ADMIN' else ''}>ADMIN</option><option {'selected' if u['role']=='EXEC' else ''}>EXEC</option><option {'selected' if u['role']=='MEMBER' else ''}>MEMBER</option></select></div><div class='field'><label>Status</label><select name='is_active'><option value='1' {'selected' if u['is_active']==1 else ''}>active</option><option value='0' {'selected' if u['is_active']==0 else ''}>disabled</option></select></div><div class='field'><label>&nbsp;</label><button class='btn btn-sm'>Save</button></div></form>"
            reset = f"<form method='POST' class='toolbar'><input type='hidden' name='action' value='reset_password'><input type='hidden' name='user_id' value='{u['id']}'><input name='temp_password' placeholder='Temp password' required><button class='btn btn-sm'>Reset password</button></form>"
            rows.append(f"<tr><td>{e(u['name'])}</td><td>{e(u['email'])}</td><td>{render_badge(u['role'])}</td><td>{e(u['created_at'])}</td><td class='actions'><a class='btn btn-sm btn-ghost' href='/admin/users/{u['id']}'>View</a><a class='btn btn-sm' href='/admin/users/{u['id']}/edit'>Edit</a>{reset}</td></tr>")
        search_bar = f"<form method='GET' class='toolbar'><div class='field'><label>Search users</label><input name='q' value='{e(q)}' placeholder='name or email'></div><button class='btn'>Search</button></form>"
        table = render_table(["Name", "Email", "Role", "Created", "Manage"], "".join(rows), "No users found.")
        create = "<form method='POST' class='form-grid'><input type='hidden' name='action' value='create'><div class='field'><label>Name</label><input name='name' required></div><div class='field'><label>Email</label><input type='email' name='email' required></div><div class='field'><label>Role</label><select name='role'><option>ADMIN</option><option>EXEC</option><option>MEMBER</option></select></div><div class='field'><label>Temporary password</label><input name='temp_password' required></div><div class='field'><label>&nbsp;</label><button class='btn'>Create user</button></div></form>"
        body = render_page_header("User Administration", "Admin-only user controls") + render_card("Users", search_bar + table) + render_card("Create user", create)
        return respond(start_response, layout(user, 'User Administration', body, toast_message(environ), path))

    body = render_page_header("Not found") + render_empty_state("Route not found", "The page you requested does not exist.")
    return respond(start_response, layout(user, 'Not found', body, active_path=path), '404 Not Found')


if __name__ == '__main__':
    migrate()
    print('Starting on http://localhost:8000')
    with make_server('0.0.0.0', 8000, app) as server:
        server.serve_forever()
