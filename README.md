# Executive Project & Decision Command Center (MVP)

Local-first web app for executive project and decision tracking.

## Run

```bash
python app/main.py
```

Open `http://localhost:8000`.

## Setup + seed

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

## Notable behavior

- Soft-delete support for Projects, Decisions, Action Items, Risks/Issues, Stakeholders.
- Deleting a Project soft-deletes linked Decisions, Action Items, Risks/Issues, and ProjectStakeholder links.
- Deleting a Decision soft-deletes linked Action Items.
- Deleted records are excluded from lists/counts by default.
- Admin user management supports: create user (temp password), edit name/role/active flag, reset password.
- Admin-created users are forced to change password on first login.
- Decision “Mark DECIDED” requires both outcome and decision date.
- Dashboard “Open decisions” includes only `PROPOSED` and `REVISIT` where `deleted_at IS NULL`.
- Dashboard includes “Decided in last 7/30 days” and a recent decided list.
- War Room Brief has been removed.
