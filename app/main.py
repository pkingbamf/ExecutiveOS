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
from app.models import compute_risk_score, validate_decision_transition
from app.rbac import can_edit_owned_or_admin, can_manage_users, can_view_all


def parse_form(environ):
    try:
        size = int(environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        size = 0
    body = environ["wsgi.input"].read(size).decode("utf-8")
    form = {k: v[0] for k, v in parse_qs(body).items()}
    return form


def redirect(start_response, location):
    start_response("302 Found", [("Location", location)])
    return [b""]


def response(start_response, body, status="200 OK", headers=None):
    hdrs = [("Content-Type", "text/html; charset=utf-8")]
    if headers:
        hdrs.extend(headers)
    start_response(status, hdrs)
    return [body.encode("utf-8")]


def get_cookie(environ, key):
    raw = environ.get("HTTP_COOKIE", "")
    for item in raw.split(";"):
        if "=" in item:
            k, v = item.strip().split("=", 1)
            if k == key:
                return v
    return None


def layout(user, title, content):
    nav = ""
    if user:
        nav = """
        <nav>
          <a href='/dashboard'>Dashboard</a> |
          <a href='/projects'>Projects</a> |
          <a href='/decisions'>Decisions</a> |
          <a href='/actions'>Action Items</a> |
          <a href='/risks'>Risks</a> |
          <a href='/stakeholders'>Stakeholders</a> |
          <a href='/war-room'>War Room Brief</a> |
          <a href='/logout'>Logout</a>
        </nav>
        """
    return f"""<!doctype html><html><head><title>{title}</title><style>
    body{{font-family:Inter,Arial;margin:24px;background:#f6f8fb}} .card{{background:#fff;padding:16px;border-radius:10px;margin-bottom:12px}}
    table{{width:100%;border-collapse:collapse}} td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}
    input,select,textarea{{padding:8px;margin:4px 0;width:100%}} .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
    .kpi{{display:inline-block;background:#fff;padding:12px;border-radius:8px;margin-right:8px}}
    </style></head><body><h2>Executive Project & Decision Command Center</h2>{nav}{content}</body></html>"""


def require_user(environ, start_response):
    token = get_cookie(environ, "session")
    user = get_user_by_session(token)
    if not user:
        redirect(start_response, "/login")
        return None
    return user


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
        return response(start_response, layout(user, "Sign up", "<div class='card'><form method='POST'><input name='name' placeholder='Name' required><input name='email' placeholder='Email' required><input type='password' name='password' placeholder='Password' required><button>Sign up</button></form></div>"))

    if path == "/login":
        if method == "POST":
            form = parse_form(environ)
            auth_user = authenticate(form.get("email", ""), form.get("password", ""))
            if auth_user:
                token = create_session(auth_user.id)
                start_response("302 Found", [("Location", "/dashboard"), ("Set-Cookie", f"session={token}; HttpOnly; Path=/")])
                return [b""]
            return response(start_response, layout(user, "Login", "<div class='card'>Invalid credentials</div>"), status="401 Unauthorized")
        return response(start_response, layout(user, "Login", "<div class='card'><form method='POST'><input name='email' placeholder='Email' required><input type='password' name='password' placeholder='Password' required><button>Login</button></form><a href='/signup'>Sign up</a></div>"))

    if path == "/logout":
        destroy_session(get_cookie(environ, "session"))
        start_response("302 Found", [("Location", "/login"), ("Set-Cookie", "session=; Max-Age=0; Path=/")])
        return [b""]

    if not user:
        return redirect(start_response, "/login")

    if path == "/dashboard":
        with get_conn() as conn:
            projects_by_status = conn.execute("SELECT status, COUNT(*) c FROM projects GROUP BY status").fetchall()
            open_decisions = conn.execute("SELECT COUNT(*) c FROM decisions WHERE status IN ('PROPOSED','REVISIT')").fetchone()["c"]
            overdue_actions = conn.execute("SELECT COUNT(*) c FROM action_items WHERE status != 'DONE' AND due_date < date('now')").fetchone()["c"]
            top_risks = conn.execute("SELECT id,title,probability*impact score FROM risk_issues ORDER BY score DESC LIMIT 5").fetchall()
        cards = "".join([f"<span class='kpi'>{r['status']}: {r['c']}</span>" for r in projects_by_status])
        risks = "".join([f"<li>{html.escape(r['title'])} (score {r['score']})</li>" for r in top_risks])
        body = f"<div class='card'><h3>At a glance</h3>{cards}<p>Open decisions: {open_decisions}</p><p>Overdue action items: {overdue_actions}</p><ul>{risks}</ul><p><a href='/projects'>New Project</a> | <a href='/decisions'>New Decision</a> | <a href='/actions'>New Action Item</a> | <a href='/risks'>New Risk</a></p></div>"
        return response(start_response, layout(user, "Dashboard", body))

    if path == "/projects":
        if method == "POST":
            form = parse_form(environ)
            owner = int(form.get("owner_user_id", user.id))
            if user.role == "MEMBER" and owner != user.id:
                owner = user.id
            with get_conn() as conn:
                cur = conn.execute("INSERT INTO projects(title,short_description,status,priority,owner_user_id,sponsor_name,start_date,target_date,tags) VALUES(?,?,?,?,?,?,?,?,?)",
                             (form.get("title", ""), form.get("short_description", ""), form.get("status", "NOT_STARTED"), form.get("priority", "P2"), owner, form.get("sponsor_name", ""), form.get("start_date") or None, form.get("target_date") or None, json.dumps([t.strip() for t in form.get("tags", "").split(",") if t.strip()])))
                pid = cur.lastrowid
                conn.execute("INSERT INTO activity_logs(entity_type,entity_id,action,actor_user_id,metadata) VALUES('PROJECT',?,?,?,?)", (pid, "CREATE", user.id, "{}"))
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        where, args = [], []
        for key in ["status", "priority", "owner_user_id"]:
            if qs.get(key):
                where.append(f"{key} = ?")
                args.append(qs[key][0])
        if qs.get("tags"):
            where.append("tags LIKE ?")
            args.append(f"%{qs['tags'][0]}%")
        scope = "" if can_view_all(user) else "WHERE owner_user_id = ?"
        if scope:
            args = [user.id] + args
        if where:
            clause = " AND ".join(where)
            scope = (scope + " AND " + clause) if scope else ("WHERE " + clause)
        with get_conn() as conn:
            projects = conn.execute(f"SELECT p.*,u.name owner_name FROM projects p JOIN users u ON u.id=p.owner_user_id {scope} ORDER BY updated_at DESC", args).fetchall()
            users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
        rows = "".join([f"<tr><td><a href='/projects/{p['id']}'>{html.escape(p['title'])}</a></td><td>{p['status']}</td><td>{p['priority']}</td><td>{html.escape(p['owner_name'])}</td></tr>" for p in projects])
        user_opts = "".join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        body = f"<div class='card'><h3>Projects</h3><form method='GET' class='grid'><input name='status' placeholder='status'><input name='priority' placeholder='priority'><input name='tags' placeholder='tag'><button>Filter</button></form><table><tr><th>Title</th><th>Status</th><th>Priority</th><th>Owner</th></tr>{rows}</table></div><div class='card'><h4>New Project</h4><form method='POST' class='grid'><input name='title' placeholder='Title' required><input name='short_description' placeholder='Short description'><select name='status'><option>NOT_STARTED</option><option>IN_PROGRESS</option><option>AT_RISK</option><option>BLOCKED</option><option>DONE</option></select><select name='priority'><option>P0</option><option>P1</option><option>P2</option><option>P3</option></select><select name='owner_user_id'>{user_opts}</select><input name='sponsor_name' placeholder='Sponsor'><input type='date' name='start_date'><input type='date' name='target_date'><input name='tags' placeholder='comma tags'><button>Create</button></form></div>"
        return response(start_response, layout(user, "Projects", body))

    if path.startswith('/projects/'):
        pid = int(path.split('/')[-1])
        with get_conn() as conn:
            p = conn.execute("SELECT p.*,u.name owner_name FROM projects p JOIN users u ON u.id=p.owner_user_id WHERE p.id=?", (pid,)).fetchone()
            decisions = conn.execute("SELECT id,title,status FROM decisions WHERE project_id=? ORDER BY updated_at DESC", (pid,)).fetchall()
            actions = conn.execute("SELECT id,title,status,due_date FROM action_items WHERE project_id=? ORDER BY updated_at DESC", (pid,)).fetchall()
            risks = conn.execute("SELECT id,title,status,probability*impact score FROM risk_issues WHERE project_id=? ORDER BY score DESC", (pid,)).fetchall()
            stakeholders = conn.execute("SELECT s.id,s.name,ps.relationship_notes FROM stakeholders s JOIN project_stakeholders ps ON s.id=ps.stakeholder_id WHERE ps.project_id=?", (pid,)).fetchall()
            activity = conn.execute("SELECT action,created_at FROM activity_logs WHERE entity_type='PROJECT' AND entity_id=? ORDER BY created_at DESC", (pid,)).fetchall()
        if not p:
            return response(start_response, layout(user, "Not found", "<div class='card'>Not found</div>"), "404 Not Found")
        if not can_view_all(user) and p["owner_user_id"] != user.id:
            return response(start_response, layout(user, "Forbidden", "<div class='card'>Forbidden</div>"), "403 Forbidden")
        decisions_html = ''.join([f"<li><a href='/decisions'>{html.escape(d['title'])}</a> ({d['status']})</li>" for d in decisions])
        actions_html = ''.join([f"<li>{html.escape(a['title'])} [{a['status']}] due {a['due_date'] or '-'} </li>" for a in actions])
        risks_html = ''.join([f"<li>{html.escape(r['title'])} score {r['score']}</li>" for r in risks])
        stakeholders_html = ''.join([f"<li>{html.escape(s['name'])} - {html.escape(s['relationship_notes'] or '')}</li>" for s in stakeholders])
        activity_html = ''.join([f"<li>{e['created_at']}: {e['action']}</li>" for e in activity])
        body = f"<div class='card'><h3>{html.escape(p['title'])}</h3><p>{html.escape(p['short_description'])}</p><p>Status: {p['status']} Priority: {p['priority']} Owner: {html.escape(p['owner_name'])}</p><p>Timeline: {p['start_date']} → {p['target_date']}</p></div>"
        body += f"<div class='card'><h4>Linked Decisions</h4><ul>{decisions_html}</ul></div>"
        body += f"<div class='card'><h4>Action Items</h4><ul>{actions_html}</ul></div>"
        body += f"<div class='card'><h4>Risks & Issues</h4><ul>{risks_html}</ul></div>"
        body += f"<div class='card'><h4>Stakeholders</h4><ul>{stakeholders_html}</ul></div>"
        body += f"<div class='card'><h4>Activity log</h4><ul>{activity_html}</ul></div>"
        return response(start_response, layout(user, p['title'], body))

    if path == "/decisions":
        if method == "POST":
            form = parse_form(environ)
            owner = int(form.get("owner_user_id", user.id))
            if user.role == "MEMBER" and owner != user.id:
                owner = user.id
            with get_conn() as conn:
                cur = conn.execute("INSERT INTO decisions(project_id,title,decision_type,status,owner_user_id,approver,rationale,options_considered,due_date,impact_level) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (form.get("project_id") or None, form.get("title",""), form.get("decision_type","STRATEGIC"), form.get("status","PROPOSED"), owner, form.get("approver",""), form.get("rationale",""), form.get("options_considered",""), form.get("due_date") or None, form.get("impact_level","MED")))
                did = cur.lastrowid
                conn.execute("INSERT INTO activity_logs(entity_type,entity_id,action,actor_user_id,metadata) VALUES('DECISION',?,?,?,?)", (did, "CREATE", user.id, "{}"))
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        where, args = [], []
        for key in ["status", "decision_type", "owner_user_id", "project_id"]:
            if qs.get(key): where.append(f"d.{key}=?") or args.append(qs[key][0])
        scope = "" if can_view_all(user) else "WHERE d.owner_user_id = ?"
        if scope: args = [user.id] + args
        if where:
            clause = " AND ".join(where)
            scope = (scope + " AND " + clause) if scope else ("WHERE " + clause)
        with get_conn() as conn:
            decisions = conn.execute(f"SELECT d.*,u.name owner_name,p.title project_title FROM decisions d JOIN users u ON u.id=d.owner_user_id LEFT JOIN projects p ON p.id=d.project_id {scope} ORDER BY d.updated_at DESC", args).fetchall()
            projects = conn.execute("SELECT id,title FROM projects ORDER BY title").fetchall()
            users = conn.execute("SELECT id,name FROM users ORDER BY name").fetchall()
        rows=''.join([f"<tr><td>{html.escape(d['title'])}</td><td>{d['status']}</td><td>{d['decision_type']}</td><td>{html.escape(d['owner_name'])}</td><td>{html.escape(d['project_title'] or 'Org-level')}</td><td><form method='POST' action='/decisions/{d['id']}/status'><select name='status'><option>PROPOSED</option><option>DECIDED</option><option>REVISIT</option><option>CANCELLED</option></select><input type='date' name='decision_date'><input name='decision_outcome' placeholder='Outcome'><button>Update</button></form></td></tr>" for d in decisions])
        proj_opts=''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects])
        user_opts=''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        body=f"<div class='card'><h3>Decisions</h3><table><tr><th>Title</th><th>Status</th><th>Type</th><th>Owner</th><th>Project</th><th>Workflow</th></tr>{rows}</table></div><div class='card'><h4>New Decision</h4><form method='POST' class='grid'><input name='title' placeholder='Decision statement' required><select name='project_id'><option value=''>Org-level</option>{proj_opts}</select><select name='decision_type'><option>STRATEGIC</option><option>FINANCIAL</option><option>OPERATIONAL</option><option>GOVERNANCE</option></select><select name='status'><option>PROPOSED</option><option>REVISIT</option></select><select name='owner_user_id'>{user_opts}</select><input name='approver' placeholder='Approver'><input name='due_date' type='date'><select name='impact_level'><option>LOW</option><option>MED</option><option>HIGH</option></select><textarea name='rationale' placeholder='Rationale'></textarea><textarea name='options_considered' placeholder='Options'></textarea><button>Create</button></form></div>"
        return response(start_response, layout(user,"Decisions",body))

    if path.startswith('/decisions/') and path.endswith('/status') and method == 'POST':
        did = int(path.split('/')[2])
        form = parse_form(environ)
        with get_conn() as conn:
            d = conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
            if not d:
                return response(start_response, layout(user, "Not found", ""), "404 Not Found")
            if not can_edit_owned_or_admin(user, d['owner_user_id']):
                return response(start_response, layout(user, "Forbidden", ""), "403 Forbidden")
            ok,msg = validate_decision_transition(d['status'], form.get('status',''), form.get('decision_outcome',''), form.get('decision_date'))
            if not ok:
                return response(start_response, layout(user, "Invalid", f"<div class='card'>{msg}</div>"), "400 Bad Request")
            conn.execute("UPDATE decisions SET status=?,decision_date=?,decision_outcome=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (form.get('status'), form.get('decision_date') or d['decision_date'], form.get('decision_outcome') or d['decision_outcome'], did))
            conn.execute("INSERT INTO activity_logs(entity_type,entity_id,action,actor_user_id,metadata) VALUES('DECISION',?,?,?,?)", (did, f"STATUS_{form.get('status')}", user.id, "{}"))
        return redirect(start_response, "/decisions")

    if path == "/actions":
        if method == "POST":
            form=parse_form(environ)
            owner=int(form.get('owner_user_id', user.id))
            if user.role == 'MEMBER' and owner != user.id: owner = user.id
            with get_conn() as conn:
                conn.execute("INSERT INTO action_items(project_id,decision_id,title,owner_user_id,status,due_date,notes) VALUES(?,?,?,?,?,?,?)", (form.get('project_id') or None, form.get('decision_id') or None, form.get('title',''), owner, form.get('status','OPEN'), form.get('due_date') or None, form.get('notes','')))
        with get_conn() as conn:
            rows=conn.execute("SELECT a.*,u.name owner_name,p.title project_title FROM action_items a JOIN users u ON u.id=a.owner_user_id LEFT JOIN projects p ON p.id=a.project_id ORDER BY a.status,a.due_date").fetchall()
            users=conn.execute("SELECT id,name FROM users").fetchall(); projects=conn.execute("SELECT id,title FROM projects").fetchall(); decisions=conn.execute("SELECT id,title FROM decisions").fetchall()
        if user.role == 'MEMBER':
            rows=[r for r in rows if r['owner_user_id']==user.id]
        table=''.join([f"<tr><td>{html.escape(r['title'])}</td><td>{r['status']}</td><td>{html.escape(r['owner_name'])}</td><td>{r['due_date'] or '-'}</td><td>{html.escape(r['project_title'] or '')}</td></tr>" for r in rows])
        user_opts = ''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        project_opts = ''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects])
        decision_opts = ''.join([f"<option value='{d['id']}'>{html.escape(d['title'])}</option>" for d in decisions])
        body=f"<div class='card'><h3>Action Items</h3><table><tr><th>Title</th><th>Status</th><th>Owner</th><th>Due</th><th>Project</th></tr>{table}</table></div>"
        body += f"<div class='card'><h4>New Action</h4><form method='POST' class='grid'><input name='title' required><select name='owner_user_id'>{user_opts}</select><select name='project_id'><option value=''>None</option>{project_opts}</select><select name='decision_id'><option value=''>None</option>{decision_opts}</select><select name='status'><option>OPEN</option><option>IN_PROGRESS</option><option>DONE</option><option>CANCELLED</option></select><input type='date' name='due_date'><textarea name='notes'></textarea><button>Create</button></form></div>"
        return response(start_response, layout(user, 'Action Items', body))

    if path == '/risks':
        if method == 'POST':
            form=parse_form(environ)
            with get_conn() as conn:
                conn.execute("INSERT INTO risk_issues(project_id,type,title,description,probability,impact,status,owner_user_id,mitigation_plan,due_date) VALUES(?,?,?,?,?,?,?,?,?,?)", (form.get('project_id'), form.get('type','RISK'), form.get('title',''), form.get('description',''), int(form.get('probability','1')), int(form.get('impact','1')), form.get('status','OPEN'), int(form.get('owner_user_id', user.id)), form.get('mitigation_plan',''), form.get('due_date') or None))
        with get_conn() as conn:
            rows=conn.execute("SELECT r.*,u.name owner_name,p.title project_title,(probability*impact) score FROM risk_issues r JOIN users u ON u.id=r.owner_user_id JOIN projects p ON p.id=r.project_id ORDER BY score DESC").fetchall()
            users=conn.execute("SELECT id,name FROM users").fetchall(); projects=conn.execute("SELECT id,title FROM projects").fetchall()
        table=''.join([f"<tr><td>{html.escape(r['title'])}</td><td>{r['type']}</td><td>{r['score']}</td><td>{r['status']}</td><td>{html.escape(r['project_title'])}</td></tr>" for r in rows])
        project_opts = ''.join([f"<option value='{p['id']}'>{html.escape(p['title'])}</option>" for p in projects])
        user_opts = ''.join([f"<option value='{u['id']}'>{html.escape(u['name'])}</option>" for u in users])
        body=f"<div class='card'><h3>Risks / Issues</h3><table><tr><th>Title</th><th>Type</th><th>Score</th><th>Status</th><th>Project</th></tr>{table}</table></div>"
        body += f"<div class='card'><h4>New Risk/Issue</h4><form method='POST' class='grid'><input name='title' required><select name='type'><option>RISK</option><option>ISSUE</option></select><select name='project_id'>{project_opts}</select><select name='owner_user_id'>{user_opts}</select><input name='probability' type='number' min='1' max='5' value='3'><input name='impact' type='number' min='1' max='5' value='3'><input type='date' name='due_date'><select name='status'><option>OPEN</option><option>MITIGATED</option><option>CLOSED</option></select><textarea name='description' placeholder='Description'></textarea><textarea name='mitigation_plan' placeholder='Mitigation'></textarea><button>Create</button></form></div>"
        return response(start_response, layout(user, 'Risks', body))

    if path == '/stakeholders':
        if method == 'POST':
            form=parse_form(environ)
            with get_conn() as conn:
                conn.execute("INSERT INTO stakeholders(name,title,org_unit,contact,influence_level,stance,notes) VALUES(?,?,?,?,?,?,?)", (form.get('name',''), form.get('title',''), form.get('org_unit',''), form.get('contact') or None, form.get('influence_level','MED'), form.get('stance','NEUTRAL'), form.get('notes','')))
        with get_conn() as conn:
            rows=conn.execute("SELECT * FROM stakeholders ORDER BY created_at DESC").fetchall()
        table=''.join([f"<tr><td>{html.escape(r['name'])}</td><td>{html.escape(r['title'])}</td><td>{html.escape(r['org_unit'])}</td><td>{r['influence_level']}</td><td>{r['stance']}</td></tr>" for r in rows])
        body=f"<div class='card'><h3>Stakeholders</h3><table><tr><th>Name</th><th>Title</th><th>Org Unit</th><th>Influence</th><th>Stance</th></tr>{table}</table></div><div class='card'><h4>New Stakeholder</h4><form method='POST' class='grid'><input name='name' required><input name='title'><input name='org_unit'><input name='contact'><select name='influence_level'><option>LOW</option><option>MED</option><option>HIGH</option></select><select name='stance'><option>SUPPORTIVE</option><option>NEUTRAL</option><option>RESISTANT</option></select><textarea name='notes'></textarea><button>Create</button></form></div>"
        return response(start_response, layout(user, 'Stakeholders', body))

    if path == '/war-room':
        with get_conn() as conn:
            p0p1=conn.execute("SELECT title,status,short_description FROM projects WHERE priority IN ('P0','P1') ORDER BY priority,status").fetchall()
            decisions=conn.execute("SELECT title,status,due_date FROM decisions WHERE status IN ('PROPOSED','REVISIT') ORDER BY due_date LIMIT 10").fetchall()
            overdue=conn.execute("SELECT title,due_date FROM action_items WHERE status != 'DONE' AND due_date < date('now') ORDER BY due_date LIMIT 5").fetchall()
            risks=conn.execute("SELECT title,probability*impact score FROM risk_issues ORDER BY score DESC LIMIT 5").fetchall()
        txt = ["WAR ROOM BRIEF", f"Week of {date.today()}", "", "P0/P1 Projects:"]
        txt += [f"- {r['title']} [{r['status']}] blocker: {r['short_description']}" for r in p0p1]
        txt += ["", "Decisions needed this week:"] + [f"- {d['title']} ({d['status']}) due {d['due_date'] or 'n/a'}" for d in decisions]
        txt += ["", "Top overdue action items:"] + [f"- {a['title']} due {a['due_date']}" for a in overdue]
        txt += ["", "Top risks/issues:"] + [f"- {r['title']} score {r['score']}" for r in risks]
        brief = "\n".join(txt)
        body=f"<div class='card'><h3>War Room Brief</h3><textarea id='brief' rows='20'>{html.escape(brief)}</textarea><button onclick=\"navigator.clipboard.writeText(document.getElementById('brief').value)\">Copy to clipboard</button></div>"
        return response(start_response, layout(user, 'War Room Brief', body))

    if path == '/search':
        q = parse_qs(environ.get("QUERY_STRING", "")).get("q", [""])[0]
        like=f"%{q}%"
        with get_conn() as conn:
            projects=conn.execute("SELECT id,title,'project' t FROM projects WHERE title LIKE ? LIMIT 10", (like,)).fetchall()
            decisions=conn.execute("SELECT id,title,'decision' t FROM decisions WHERE title LIKE ? LIMIT 10", (like,)).fetchall()
            actions=conn.execute("SELECT id,title,'action' t FROM action_items WHERE title LIKE ? LIMIT 10", (like,)).fetchall()
        rows=projects+decisions+actions
        body="<div class='card'><h3>Search</h3><form><input name='q' value='{}'><button>Search</button></form><ul>{}</ul></div>".format(html.escape(q), ''.join([f"<li>{r['t']}: {html.escape(r['title'])}</li>" for r in rows]))
        return response(start_response, layout(user, 'Search', body))

    if path == '/admin/users':
        if not can_manage_users(user):
            return response(start_response, layout(user, 'Forbidden', 'Forbidden'), '403 Forbidden')
        if method == 'POST':
            form=parse_form(environ)
            create_user(form['name'], form['email'], form['password'], form['role'])
        with get_conn() as conn:
            users=conn.execute("SELECT name,email,role,created_at FROM users").fetchall()
        rows=''.join([f"<tr><td>{html.escape(u['name'])}</td><td>{html.escape(u['email'])}</td><td>{u['role']}</td><td>{u['created_at']}</td></tr>" for u in users])
        body=f"<div class='card'><h3>Users</h3><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Created</th></tr>{rows}</table></div><div class='card'><form method='POST' class='grid'><input name='name' required><input name='email' required><input name='password' required><select name='role'><option>ADMIN</option><option>EXEC</option><option>MEMBER</option></select><button>Create User</button></form></div>"
        return response(start_response, layout(user, 'Users', body))

    return response(start_response, layout(user, 'Not found', '<div class="card">Not found</div>'), '404 Not Found')


if __name__ == '__main__':
    migrate()
    print('Starting on http://localhost:8000')
    with make_server('0.0.0.0', 8000, app) as server:
        server.serve_forever()
