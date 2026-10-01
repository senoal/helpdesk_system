from datetime import timedelta

from servicehub import create_app, db
from servicehub.models import Category, Department, SLAConfig, Ticket, User, utcnow


app = create_app()


def seed():
    with app.app_context():
        if db.session.scalar(db.select(User).limit(1)):
            print("Database already contains data; seed skipped.")
            return
        departments = [Department(name=name) for name in ["Information Technology", "Human Resources", "Finance", "Operations", "Sales & Marketing"]]
        categories = [Category(name=name) for name in ["Hardware", "Software", "Network", "Account & Access", "Email", "General Support"]]
        slas = [
            SLAConfig(priority="Low", target_hours=72), SLAConfig(priority="Medium", target_hours=48),
            SLAConfig(priority="High", target_hours=24), SLAConfig(priority="Critical", target_hours=4),
        ]
        db.session.add_all(departments + categories + slas)
        db.session.flush()
        users = []
        for name, email, role, dept in [
            ("System Administrator", "admin@servicehub.local", "Admin", departments[0]),
            ("Helpdesk Agent", "agent@servicehub.local", "Agent", departments[0]),
            ("Demo Employee", "employee@servicehub.local", "Employee", departments[3]),
        ]:
            user = User(name=name, email=email, role=role, department=dept)
            user.set_password("Password123!")
            users.append(user)
        db.session.add_all(users)
        db.session.flush()
        samples = [
            ("Laptop tidak dapat menyala", "Laptop mati total meskipun charger telah dipasang.", "Critical", "In Progress", categories[0], users[2], users[1], 3),
            ("Reset akses VPN", "Akun VPN terkunci setelah beberapa kali salah password.", "High", "Open", categories[3], users[2], None, 8),
            ("Microsoft Excel sering crash", "Excel tertutup ketika membuka laporan bulanan.", "Medium", "Resolved", categories[1], users[2], users[1], 30),
        ]
        for title, description, priority, status, category, requester, assignee, age in samples:
            created = utcnow() - timedelta(hours=age)
            ticket = Ticket(title=title, description=description, priority=priority, status=status, category=category, requester=requester, department=requester.department, assignee=assignee, created_at=created)
            if status == "Resolved":
                ticket.resolved_at = created + timedelta(hours=12)
            db.session.add(ticket)
        db.session.commit()
        print("Seed complete. Login: admin@servicehub.local / Password123!")


if __name__ == "__main__":
    seed()
