# Executive Project & Decision Command Center (MVP)

A production-minded MVP SaaS for busy executives running multiple high-stakes initiatives.

## Stack choice (and why)

I chose a **Python + SQLite + server-rendered WSGI** stack for this MVP:

- **Python stdlib web server (`wsgiref`)**: zero external paid services, very low setup friction, and fast local iteration.
- **SQLite (`sqlite3`)**: ideal for local development; schema is normalized and SQL-first so a future swap to Postgres is straightforward.
- **Thin domain modules** (`auth`, `rbac`, `models`) with unit tests: keeps core business rules maintainable and easy to migrate into a larger framework later.

> This is intentionally simple and pilot-focused while preserving core production concerns: auth, RBAC, validation, entity integrity, and audit/activity events.

## Features included

- Email/password auth (signup/login/logout)
- RBAC roles: `ADMIN`, `EXEC`, `MEMBER`
- Dashboard with:
  - projects by status
  - open decisions
  - overdue action items
  - top risk/issues by score
- Projects:
  - list + filters
  - create
  - detail page with linked decisions/action items/risks/stakeholders and activity log
- Decisions:
  - list + create
  - status workflow including **Mark DECIDED** validation (requires outcome + decision_date)
- Action items:
  - table list + create
- Risks/issues:
  - table sorted by computed score
  - create
- Stakeholders directory + create
- War Room Brief weekly summary with **copy-to-clipboard** export
- Global search across project/decision/action titles
- Activity log for project and decision create/update events
- Seed script with demo users + data

## Data model

Implemented entities:
- `users`
- `projects`
- `decisions`
- `action_items`
- `stakeholders`
- `project_stakeholders`
- `risk_issues`
- `activity_logs`
- `sessions`

Schema and constraints are in `app/schema.sql`.

## Project structure

```text
app/
  main.py          # WSGI app + routes + HTML views
  db.py            # DB connection + migration helpers
  auth.py          # password hashing + session handling
  rbac.py          # role/permission checks
  models.py        # domain rules (decision transitions, risk score)
  schema.sql       # SQL schema (SQLite now, Postgres-friendly design)
scripts/
  seed.py          # demo data bootstrap
tests/
  test_domain.py   # unit tests (decision transitions, risk score, RBAC)
requirements.txt
README.md
```

## Prerequisites

- Python 3.11+

## One-command local start

```bash
python app/main.py
```

Then open: `http://localhost:8000`

## Setup + seed demo data

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed.py
python app/main.py
```

Demo accounts after seeding:
- `admin@demo.com / password123` (ADMIN)
- `exec@demo.com / password123` (EXEC)
- `member@demo.com / password123` (MEMBER)

## Run tests

```bash
PYTHONPATH=. pytest -q
```

## Environment variables

No required env vars for local execution in this MVP.

(If you want, next step can add `DATABASE_URL` and `SECRET_KEY` env support for deployment hardening.)

## RBAC rules (implemented)

- **ADMIN**: can manage users and edit/view all records.
- **EXEC**: can view all records; can create new entries; can edit owned records.
- **MEMBER**: scoped views for owned records in key pages; can edit owned tasks; decisions are owner-editable only.

## Validation and defaults

- DB-level CHECK constraints for enums and score inputs.
- Decision transition guardrails in `validate_decision_transition`.
- Risk score computed as `probability * impact`.
- Safe default statuses and nullable due dates where appropriate.

## Notes for Postgres migration

- Keep table/column names unchanged.
- Convert `INTEGER PRIMARY KEY AUTOINCREMENT` to `BIGSERIAL` or identity columns.
- Replace SQLite date helpers with Postgres `CURRENT_DATE` equivalents.
- Add migration tooling (Alembic/Flyway/Prisma) in a future phase.
