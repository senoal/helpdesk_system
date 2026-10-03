from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from config import BASE_DIR, TestConfig, env_value, sqlite_url
from servicehub import create_app, db, ensure_sqlite_directory, migrate_legacy_sla_deadlines
from servicehub.models import AuditLog, Category, Department, SLAConfig, Ticket, TicketAttachment, TicketHistory, User, utcnow
from servicehub.utils import local_to_utc, utc_to_local


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    upload_folder.mkdir(parents=True, exist_ok=True)
    for path in upload_folder.iterdir():
        if path.is_file():
            path.unlink()
    with app.app_context():
        department = Department(name="IT")
        category = Category(name="Software")
        db.session.add_all([department, category, SLAConfig(priority="High", target_hours=24)])
        admin = User(name="Admin", email="admin@test.local", role="Admin", department=department)
        agent = User(name="Agent", email="agent@test.local", role="Agent", department=department)
        employee = User(name="Employee", email="employee@test.local", role="Employee", department=department)
        admin.set_password("Password123!")
        agent.set_password("Password123!")
        employee.set_password("Password123!")
        db.session.add_all([admin, agent, employee])
        db.session.commit()
    yield app
    with app.app_context():
        db.drop_all()
    for path in upload_folder.iterdir():
        if path.is_file():
            path.unlink()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, email):
    return client.post("/login", data={"email": email, "password": "Password123!"}, follow_redirects=True)


def office_file(folder, filename):
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{folder}/{filename}", "test")
    stream.seek(0)
    return stream


def test_login_and_dashboard(client):
    response = login(client, "admin@test.local")
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_ticket_index_has_single_create_button(client):
    login(client, "admin@test.local")
    response = client.get("/tickets/")
    assert response.status_code == 200
    assert response.data.count(b"Buat Tiket") == 1


def test_create_ticket_generates_number_and_sla(app, client):
    login(client, "employee@test.local")
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
    response = client.post("/tickets/create", data={
        "title": "Aplikasi error", "description": "Tidak dapat dibuka",
        "priority": "High", "department_id": department.id, "category_id": category.id,
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"TKT-" in response.data
    with app.app_context():
        ticket = db.session.scalar(db.select(Ticket))
        assert ticket.ticket_number.startswith("TKT-")
        assert 23.9 <= (ticket.sla_deadline - ticket.created_at).total_seconds() / 3600 <= 24.1


def test_requester_can_add_attachments_to_existing_ticket(app, client):
    login(client, "employee@test.local")
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
    client.post("/tickets/create", data={
        "title": "Butuh lampiran susulan",
        "description": "Tiket dibuat tanpa lampiran",
        "priority": "Medium",
        "department_id": department.id,
        "category_id": category.id,
    })
    with app.app_context():
        ticket = db.session.scalar(db.select(Ticket))
        ticket_id = ticket.id

    empty_file = client.post(f"/tickets/{ticket_id}/attachments", data={
        "attachments": (BytesIO(b""), "kosong.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert empty_file.status_code == 200
    assert b"kosong" in empty_file.data
    with app.app_context():
        assert db.session.scalar(db.select(TicketAttachment)) is None

    response = client.post(f"/tickets/{ticket_id}/attachments", data={
        "attachments": [
            (BytesIO(b"%PDF-1.4\nvalid"), "susulan.pdf"),
            (BytesIO(b"\x89PNG\r\n\x1a\nvalid"), "foto.png"),
        ],
    }, content_type="multipart/form-data", follow_redirects=True)

    assert response.status_code == 200
    assert b"2 lampiran berhasil ditambahkan" in response.data
    assert b"susulan.pdf" in response.data
    assert b"foto.png" in response.data
    with app.app_context():
        attachments = db.session.scalars(db.select(TicketAttachment)).all()
        assert len(attachments) == 2
        assert all(item.ticket_id == ticket_id for item in attachments)
        history = db.session.scalar(
            db.select(TicketHistory).where(TicketHistory.action == "Menambahkan 2 lampiran")
        )
        assert history is not None

    over_limit = client.post(f"/tickets/{ticket_id}/attachments", data={
        "attachments": [
            (BytesIO(str(index).encode()), f"tambahan-{index}.pdf") for index in range(4)
        ],
    }, content_type="multipart/form-data", follow_redirects=True)
    assert over_limit.status_code == 200
    assert b"hanya dapat menambahkan 3 file lagi" in over_limit.data
    with app.app_context():
        assert len(db.session.scalars(db.select(TicketAttachment)).all()) == 2


def test_other_employee_cannot_add_attachment_to_ticket(app, client):
    login(client, "employee@test.local")
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
        employee = db.session.scalar(db.select(User).where(User.email == "employee@test.local"))
        ticket = Ticket(
            title="Tiket privat",
            description="Hanya requester dan staff",
            requester=employee,
            department=department,
            category=category,
        )
        other_employee = User(name="Other", email="other@test.local", role="Employee", department=department)
        other_employee.set_password("Password123!")
        db.session.add_all([ticket, other_employee])
        db.session.commit()
        ticket_id = ticket.id
    client.post("/logout")
    login(client, "other@test.local")
    response = client.post(f"/tickets/{ticket_id}/attachments", data={
        "attachments": (BytesIO(b"forbidden"), "forbidden.pdf"),
    }, content_type="multipart/form-data")
    assert response.status_code == 403
    with app.app_context():
        assert db.session.scalar(db.select(TicketAttachment)) is None


def test_create_ticket_with_attachment_and_download(app, client):
    login(client, "employee@test.local")
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
    response = client.post("/tickets/create", data={
        "title": "Laporan gagal dibuka",
        "description": "File laporan menampilkan error",
        "priority": "Medium",
        "department_id": department.id,
        "category_id": category.id,
        "attachments": [
            (BytesIO(b"%PDF-1.4\nsample pdf content"), "bukti-error.pdf"),
            (office_file("word", "document.xml"), "kronologi.docx"),
            (office_file("xl", "workbook.xml"), "data.xlsx"),
            (BytesIO(b"\x89PNG\r\n\x1a\nsample image content"), "screenshot.png"),
        ],
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"bukti-error.pdf" in response.data
    assert b"kronologi.docx" in response.data
    assert b"data.xlsx" in response.data
    assert b"screenshot.png" in response.data
    with app.app_context():
        attachments = db.session.scalars(db.select(TicketAttachment)).all()
        assert len(attachments) == 4
        attachment = next(item for item in attachments if item.original_name == "bukti-error.pdf")
        assert attachment.original_name == "bukti-error.pdf"
        assert all((Path(app.config["UPLOAD_FOLDER"]) / item.stored_name).is_file() for item in attachments)
        download_url = f"/tickets/{attachment.ticket_id}/attachments/{attachment.id}"
    download = client.get(download_url)
    assert download.status_code == 200
    assert download.data == b"%PDF-1.4\nsample pdf content"
    assert "attachment" in download.headers["Content-Disposition"]
    download.close()
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        other_employee = User(name="Other", email="other@test.local", role="Employee", department=department)
        other_employee.set_password("Password123!")
        db.session.add(other_employee)
        db.session.commit()
    client.post("/logout")
    login(client, "other@test.local")
    assert client.get(download_url).status_code == 403


def test_rejects_unsupported_attachment_without_creating_ticket(app, client):
    login(client, "employee@test.local")
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
    response = client.post("/tickets/create", data={
        "title": "Lampiran tidak valid",
        "description": "Mencoba format yang tidak didukung",
        "priority": "Medium",
        "department_id": department.id,
        "category_id": category.id,
        "attachments": (BytesIO(b"echo unsafe"), "script.exe"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert b"tidak didukung" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(Ticket)) is None
        assert db.session.scalar(db.select(TicketAttachment)) is None


def test_employee_cannot_access_management(client):
    login(client, "employee@test.local")
    assert client.get("/management/").status_code == 403


def test_admin_can_export_reports(client):
    login(client, "admin@test.local")
    response = client.get("/reports/export.xlsx")
    assert response.status_code == 200
    assert response.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_admin_can_configure_default_sla_for_new_tickets(app, client):
    login(client, "admin@test.local")
    response = client.post("/management/sla/save", data={
        "priority": "High",
        "target_hours": 12,
        "warning_percent": 75,
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Konfigurasi SLA disimpan" in response.data
    client.post("/logout")
    login(client, "employee@test.local")
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
    client.post("/tickets/create", data={
        "title": "SLA dari Admin",
        "description": "Menggunakan konfigurasi SLA terbaru",
        "priority": "High",
        "department_id": department.id,
        "category_id": category.id,
    })
    with app.app_context():
        ticket = db.session.scalar(db.select(Ticket))
        assert 11.9 <= ticket.sla_target_hours <= 12.1


def test_only_admin_can_override_ticket_sla_and_requester_can_view_it(app, client):
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
        employee = db.session.scalar(db.select(User).where(User.email == "employee@test.local"))
        ticket = Ticket(
            title="Deadline khusus",
            description="SLA ditentukan Admin",
            priority="Medium",
            requester=employee,
            department=department,
            category=category,
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = ticket.id
        created_local = utc_to_local(ticket.created_at, app.config["APP_TIMEZONE"]).replace(tzinfo=None)
        custom_deadline_local = (created_local + timedelta(hours=10)).replace(second=0, microsecond=0)
        custom_deadline_utc = local_to_utc(custom_deadline_local, app.config["APP_TIMEZONE"])

    login(client, "admin@test.local")
    response = client.post(
        f"/tickets/{ticket_id}/sla",
        data={"sla_deadline": custom_deadline_local.strftime("%Y-%m-%dT%H:%M")},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Deadline SLA berhasil diperbarui" in response.data
    assert f'value="{custom_deadline_local:%Y-%m-%dT%H:%M}"'.encode() in response.data
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        assert ticket.sla_deadline == custom_deadline_utc
        assert 9.9 <= ticket.sla_target_hours <= 10.1
        history = db.session.scalar(
            db.select(TicketHistory).where(TicketHistory.action.like("Deadline SLA:%"))
        )
        assert history is not None

    client.post("/logout")
    login(client, "employee@test.local")
    detail = client.get(f"/tickets/{ticket_id}")
    assert detail.status_code == 200
    assert b"Informasi SLA" in detail.data
    assert b"Target SLA" in detail.data
    assert created_local.strftime("%d %b %Y %H:%M").encode() in detail.data
    assert custom_deadline_local.strftime("%d %b %Y %H:%M").encode() in detail.data
    assert b"Zona waktu WIB" in detail.data
    assert b"Simpan Deadline SLA" not in detail.data
    forbidden = client.post(
        f"/tickets/{ticket_id}/sla",
        data={"sla_deadline": (custom_deadline_local + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")},
    )
    assert forbidden.status_code == 403

    client.post("/logout")
    login(client, "agent@test.local")
    assert client.post(
        f"/tickets/{ticket_id}/sla",
        data={"sla_deadline": (custom_deadline_local + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")},
    ).status_code == 403


def test_legacy_manual_sla_deadline_is_migrated_to_wib_once(app):
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
        employee = db.session.scalar(db.select(User).where(User.email == "employee@test.local"))
        ticket = Ticket(
            title="SLA lama",
            description="Deadline dimasukkan sebelum dukungan timezone",
            priority="Critical",
            requester=employee,
            department=department,
            category=category,
            created_at=datetime(2026, 10, 1, 5, 18),
            sla_deadline=datetime(2026, 10, 1, 13, 4),
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add(TicketHistory(
            ticket_id=ticket.id,
            action="Deadline SLA: 01 Oct 2026 09:18 → 01 Oct 2026 13:04",
        ))
        db.session.commit()

        migrate_legacy_sla_deadlines(app)
        assert ticket.sla_deadline == datetime(2026, 10, 1, 6, 4)
        migrate_legacy_sla_deadlines(app)
        markers = db.session.scalars(db.select(TicketHistory).where(
            TicketHistory.action == "Koreksi zona waktu deadline SLA legacy ke WIB"
        )).all()
        assert len(markers) == 1


def test_priority_update_recalculates_deadline(app, client):
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
        employee = db.session.scalar(db.select(User).where(User.role == "Employee"))
        ticket = Ticket(
            title="Priority berubah", description="Dampak meningkat", priority="High",
            requester=employee, department=department, category=category,
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = ticket.id
    login(client, "admin@test.local")
    response = client.post(
        f"/tickets/{ticket_id}/update",
        data={"status": "In Progress", "priority": "Critical"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        assert ticket.priority == "Critical"
        assert 3.9 <= (ticket.sla_deadline - ticket.created_at).total_seconds() / 3600 <= 4.1


def test_invalid_report_dates_return_400(client):
    login(client, "admin@test.local")
    assert client.get("/reports/?date_from=invalid").status_code == 400
    assert client.get("/reports/export.csv?date_to=2026-99-99").status_code == 400
    assert client.get("/reports/?date_from=2026-10-02&date_to=2026-10-01").status_code == 400


def test_invalid_master_data_is_rejected(app, client):
    login(client, "employee@test.local")
    response = client.post("/tickets/create", data={
        "title": "Orphan ticket", "description": "Harus ditolak", "priority": "High",
        "department_id": 99999, "category_id": 99999,
    }, follow_redirects=True)
    assert b"tidak valid" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(Ticket)) is None


def test_employee_cannot_be_assignee_and_ticket_can_be_unassigned(app, client):
    login(client, "admin@test.local")
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
        employee = db.session.scalar(db.select(User).where(User.role == "Employee"))
        agent = db.session.scalar(db.select(User).where(User.role == "Agent"))
    rejected = client.post("/tickets/create", data={
        "title": "Invalid assignee", "description": "Harus ditolak", "priority": "High",
        "department_id": department.id, "category_id": category.id, "assignee_id": employee.id,
    }, follow_redirects=True)
    assert b"Assignee harus" in rejected.data
    with app.app_context():
        ticket = Ticket(title="Unassign", description="QA", priority="High", requester=employee, department=department, category=category, assignee=agent)
        db.session.add(ticket)
        db.session.commit()
        ticket_id = ticket.id
    client.post(f"/tickets/{ticket_id}/update", data={"status": "Open", "priority": "High", "assignee_id": ""})
    with app.app_context():
        assert db.session.get(Ticket, ticket_id).assignee_id is None


def test_completed_ticket_priority_and_sla_are_immutable(app, client):
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
        employee = db.session.scalar(db.select(User).where(User.role == "Employee"))
        ticket = Ticket(title="Selesai", description="QA", priority="High", status="Resolved", requester=employee, department=department, category=category)
        ticket.resolved_at = utcnow()
        db.session.add(ticket)
        db.session.commit()
        ticket_id, old_deadline = ticket.id, ticket.sla_deadline
    login(client, "admin@test.local")
    client.post(f"/tickets/{ticket_id}/update", data={"status": "Resolved", "priority": "Critical", "assignee_id": ""})
    with app.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        assert ticket.priority == "High"
        assert ticket.sla_deadline == old_deadline


def test_exports_neutralize_spreadsheet_formulas(app, client):
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
        employee = db.session.scalar(db.select(User).where(User.role == "Employee"))
        db.session.add(Ticket(title="=HYPERLINK(\"https://invalid\")", description="QA", requester=employee, department=department, category=category))
        db.session.commit()
    login(client, "admin@test.local")
    csv_response = client.get("/reports/export.csv")
    assert "'=HYPERLINK" in csv_response.get_data(as_text=True)
    xlsx_response = client.get("/reports/export.xlsx")
    workbook = load_workbook(BytesIO(xlsx_response.data), data_only=False)
    assert workbook.active["B2"].data_type == "s"
    assert workbook.active["B2"].value.startswith("'=")


def test_security_headers_and_csrf_enforcement(client):
    response = client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"

    class CsrfConfig(TestConfig):
        WTF_CSRF_ENABLED = True

    csrf_app = create_app(CsrfConfig)
    csrf_client = csrf_app.test_client()
    assert csrf_client.post("/login", data={"email": "x@example.com", "password": "anything"}).status_code == 400


def test_last_admin_and_self_deactivation_are_protected(app, client):
    login(client, "admin@test.local")
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.role == "Admin"))
        admin_id = admin.id
    response = client.post(f"/management/users/{admin_id}/toggle", follow_redirects=True)
    assert b"tidak dapat menonaktifkan akun sendiri" in response.data
    with app.app_context():
        assert db.session.get(User, admin_id).is_active_account


def test_corrupt_allowed_attachment_is_rejected(app, client):
    login(client, "employee@test.local")
    with app.app_context():
        department = db.session.scalar(db.select(Department))
        category = db.session.scalar(db.select(Category))
    response = client.post("/tickets/create", data={
        "title": "PDF palsu", "description": "Harus ditolak", "priority": "High",
        "department_id": department.id, "category_id": category.id,
        "attachments": (BytesIO(b"bukan pdf"), "palsu.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert b"tidak sesuai" in response.data
    with app.app_context():
        assert db.session.scalar(db.select(Ticket)) is None


def test_production_rejects_default_secret():
    class UnsafeProductionConfig(TestConfig):
        ENVIRONMENT = "production"
        SECRET_KEY = "change-this-secret-in-production"

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(UnsafeProductionConfig)


def test_default_sqlite_url_is_absolute_and_windows_safe():
    database_uri = sqlite_url(BASE_DIR / "instance" / "servicehub.db")

    assert database_uri.startswith("sqlite:///")
    assert "\\" not in database_uri


def test_blank_environment_value_uses_portable_default(monkeypatch):
    monkeypatch.setenv("PORTABLE_TEST_VALUE", "   ")

    assert env_value("PORTABLE_TEST_VALUE", "fallback") == "fallback"


def test_sqlite_parent_directory_is_created():
    database_path = BASE_DIR / "instance" / "portable test" / "nested" / "servicehub.db"
    ensure_sqlite_directory(sqlite_url(database_path))

    assert database_path.parent.is_dir()
    database_path.parent.rmdir()
    database_path.parent.parent.rmdir()


def test_login_rate_limit_blocks_repeated_failures(app):
    client = app.test_client()
    for _ in range(5):
        assert client.post("/login", data={"email": "blocked@test.local", "password": "wrong"}).status_code == 200
    response = client.post("/login", data={"email": "blocked@test.local", "password": "wrong"})
    assert response.status_code == 429
    assert b"Terlalu banyak percobaan login" in response.data


def test_admin_can_edit_user_reset_password_and_audit(app, client):
    login(client, "admin@test.local")
    with app.app_context():
        agent = db.session.scalar(db.select(User).where(User.role == "Agent"))
        department = db.session.scalar(db.select(Department))
        agent_id = agent.id
    update = client.post(f"/management/users/{agent_id}/update", data={
        "name": "Agent Updated", "email": "updated@test.local", "role": "Agent",
        "department_id": department.id,
    }, follow_redirects=True)
    assert b"Data user berhasil diperbarui" in update.data
    reset = client.post(f"/management/users/{agent_id}/reset-password", data={
        "password": "NewPassword123",
    }, follow_redirects=True)
    assert b"berhasil direset" in reset.data
    with app.app_context():
        agent = db.session.get(User, agent_id)
        assert agent.name == "Agent Updated"
        assert agent.check_password("NewPassword123")
        actions = set(db.session.scalars(db.select(AuditLog.action)).all())
        assert {"update", "reset_password"}.issubset(actions)


def test_deactivated_user_session_is_invalidated(app):
    agent_client = app.test_client()
    admin_client = app.test_client()
    login(agent_client, "agent@test.local")
    login(admin_client, "admin@test.local")
    with app.app_context():
        agent = db.session.scalar(db.select(User).where(User.role == "Agent"))
        agent_id = agent.id
    admin_client.post(f"/management/users/{agent_id}/toggle")
    response = agent_client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
