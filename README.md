# Executive Project & Decision Command Center (MVP Web App)

A clean, local-first SaaS MVP for executives managing high-stakes initiatives.

## What changed

This implementation is now a **proper web app experience** with:
- A modern app shell (sidebar nav + top search bar)
- Structured pages for Dashboard, Projects, Decisions, Action Items, Risks, Stakeholders, War Room Brief, and Admin Users
- Role-aware behavior (ADMIN / EXEC / MEMBER)
- Better auth security via salted PBKDF2 password hashing
- Seeded demo dataset for immediate exploration

## Stack

- **Python standard library web server (`wsgiref`)** for zero-dependency local runtime
- **SQLite** for local data with relational schema that can be migrated later
- **Pytest** for domain-level and auth tests

> Why this stack: package registry access in constrained environments can be blocked; this approach still ships a functional, testable web MVP that runs with one command.

## Core capabilities

- Auth: signup/login/logout (session cookies)
- RBAC:
  - ADMIN: full access + user management
  - EXEC: view all and manage owned work
  - MEMBER: focused scope and owned-item edits
- Dashboard cards:
  - projects by status
  - open decisions
  - overdue tasks
  - top risks/issues
- Projects:
  - list + filters
  - detail view with linked records and activity log
- Decisions:
  - full decision records
  - status transition workflow
  - `DECIDED` requires outcome + decision date
- Action items:
  - tabular queue + create
- Risks/issues:
  - sorted by score (`probability * impact`)
- Stakeholders:
  - directory + add
- War Room Brief:
  - generated weekly summary
  - copy-to-clipboard export
- Global search over titles (projects/decisions/actions)

## Data model

Implemented tables:
- `users`
- `projects`
- `decisions`
- `action_items`
- `stakeholders`
- `project_stakeholders`
- `risk_issues`
- `activity_logs`
- `sessions`

See: `app/schema.sql`

## Project structure

```text
app/
  main.py          # web routes + rendered pages
  db.py            # db connection + migration bootstrap
  auth.py          # auth + password hashing + session utilities
  rbac.py          # role checks
  models.py        # domain rules (decision transitions, risk score)
  schema.sql       # db schema
scripts/
  seed.py          # demo users + sample records
tests/
  test_domain.py   # decision/risk/RBAC tests
  test_auth.py     # password hashing tests
```

## Prerequisites

- Python 3.10+

## One-command start

```bash
python app/main.py
```

Open `http://localhost:8000`.

## Setup + demo data

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed.py
python app/main.py
```

Demo users:
- `admin@demo.com / password123`
- `exec@demo.com / password123`
- `member@demo.com / password123`

## Tests

```bash
PYTHONPATH=. pytest -q
```

## Environment variables

None required for local run.

## Postgres migration path (later)

- Keep same table names/columns
- Replace SQLite autoincrement with Postgres identity columns
- Swap SQL date funcs to Postgres equivalents
- Introduce migration tool (Alembic/Flyway) in next phase
