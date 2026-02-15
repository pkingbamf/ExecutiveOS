from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.auth import create_user
from app.db import get_conn, migrate


def run():
    migrate()
    with get_conn() as conn:
        if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] > 0:
            print("Seed skipped: data already exists")
            return
    create_user("Alice Admin", "admin@demo.com", "password123", "ADMIN")
    create_user("Evan Exec", "exec@demo.com", "password123", "EXEC")
    create_user("Mina Member", "member@demo.com", "password123", "MEMBER")

    with get_conn() as conn:
        conn.execute("INSERT INTO projects(title,short_description,status,priority,owner_user_id,sponsor_name,start_date,target_date,tags) VALUES('M&A Integration','Integrate acquired business systems','IN_PROGRESS','P0',2,'CEO','2026-01-01','2026-06-30','[\"integration\",\"finance\"]')")
        conn.execute("INSERT INTO projects(title,short_description,status,priority,owner_user_id,sponsor_name,start_date,target_date,tags) VALUES('AI Ops Modernization','Deploy AI copilots for ops team','AT_RISK','P1',3,'COO','2026-02-01','2026-07-15','[\"ai\",\"operations\"]')")
        conn.execute("INSERT INTO decisions(project_id,title,decision_type,status,owner_user_id,approver,rationale,options_considered,due_date,impact_level) VALUES(1,'Choose ERP consolidation path','STRATEGIC','PROPOSED',2,'Board Finance Committee','Avoid duplicated cost base','Single ERP vs phased model','2026-03-01','HIGH')")
        conn.execute("INSERT INTO action_items(project_id,decision_id,title,owner_user_id,status,due_date,notes) VALUES(1,1,'Finalize ERP cost model',3,'IN_PROGRESS','2026-02-20','Collect vendor final quotes')")
        conn.execute("INSERT INTO risk_issues(project_id,type,title,description,probability,impact,status,owner_user_id,mitigation_plan,due_date) VALUES(2,'RISK','Data pipeline latency risk','Inference latency may breach SLA',4,5,'OPEN',2,'Load test + optimize feature store','2026-02-28')")
        conn.execute("INSERT INTO stakeholders(name,title,org_unit,contact,influence_level,stance,notes) VALUES('Jordan Lee','CFO','Finance','jordan@example.com','HIGH','SUPPORTIVE','Sponsor for budget approvals')")
        conn.execute("INSERT INTO project_stakeholders(project_id,stakeholder_id,relationship_notes) VALUES(1,1,'Monthly steering committee')")
    print("Seed complete. Login: admin@demo.com / password123")


if __name__ == '__main__':
    run()
