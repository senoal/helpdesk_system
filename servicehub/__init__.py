import sqlite3
from pathlib import Path

from flask import Flask, render_template
from flask_login import LoginManager, current_user, logout_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from config import Config


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Silakan login untuk mengakses halaman tersebut."
login_manager.login_message_category = "warning"
csrf = CSRFProtect()
migrate = Migrate()


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    # This hook is registered globally, so do not send SQLite-specific SQL to
    # PostgreSQL/MySQL connections when DATABASE_URL is overridden.
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def ensure_sqlite_directory(database_uri):
    """Create the parent folder required by a file-based SQLite database."""
    url = make_url(database_uri)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def migrate_legacy_sla_deadlines(app):
    """Correct pre-timezone manual SLA values once, based on their audit trail."""
    from servicehub.models import Ticket, TicketHistory
    from servicehub.utils import local_to_utc

    marker = "Koreksi zona waktu deadline SLA legacy ke WIB"
    legacy_ids = set(db.session.scalars(
        db.select(TicketHistory.ticket_id).where(
            TicketHistory.action.like("Deadline SLA:%"),
            ~TicketHistory.action.like("% WIB"),
        )
    ))
    corrected_ids = set(db.session.scalars(
        db.select(TicketHistory.ticket_id).where(TicketHistory.action == marker)
    ))
    for ticket_id in legacy_ids - corrected_ids:
        ticket = db.session.get(Ticket, ticket_id)
        if not ticket:
            continue
        ticket.sla_deadline = local_to_utc(
            ticket.sla_deadline,
            app.config["APP_TIMEZONE"],
        )
        db.session.add(TicketHistory(ticket_id=ticket.id, action=marker))
    if legacy_ids - corrected_ids:
        db.session.commit()


def create_app(config_object=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    if app.config.get("ENVIRONMENT") == "production" and app.config.get("SECRET_KEY") == "change-this-secret-in-production":
        raise RuntimeError("SECRET_KEY wajib diatur ke nilai yang aman pada environment production.")
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    ensure_sqlite_directory(app.config["SQLALCHEMY_DATABASE_URI"])
    if not app.config.get("UPLOAD_FOLDER"):
        app.config["UPLOAD_FOLDER"] = str(Path(app.instance_path) / "uploads")
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)

    from servicehub.auth.routes import auth_bp
    from servicehub.main.routes import main_bp
    from servicehub.tickets.routes import tickets_bp
    from servicehub.admin.routes import admin_bp
    from servicehub.reports.routes import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(tickets_bp, url_prefix="/tickets")
    app.register_blueprint(admin_bp, url_prefix="/management")
    app.register_blueprint(reports_bp, url_prefix="/reports")

    from servicehub.models import User
    from servicehub.utils import utc_to_local

    @login_manager.user_loader
    def load_user(user_id):
        user = db.session.get(User, int(user_id))
        return user if user and user.is_active_account else None

    @app.before_request
    def invalidate_disabled_accounts():
        if current_user.is_authenticated:
            is_active = db.session.scalar(
                db.select(User.is_active_account).where(User.id == current_user.id)
            )
            if not is_active:
                logout_user()

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; script-src 'self' 'unsafe-inline'; img-src 'self' data:",
        )
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return render_template("errors/error.html", code=400, title="Permintaan Tidak Valid", message="Sesi formulir berakhir atau token keamanan tidak valid. Muat ulang halaman dan coba lagi."), 400

    @app.errorhandler(400)
    def bad_request(error):
        message = getattr(error, "description", None) or "Permintaan tidak dapat diproses. Periksa data dan coba kembali."
        return render_template("errors/error.html", code=400, title="Permintaan Tidak Valid", message=message), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/error.html", code=403, title="Akses Ditolak", message="Anda tidak memiliki hak akses untuk halaman atau tindakan ini."), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/error.html", code=404, title="Halaman Tidak Ditemukan", message="Halaman atau data yang Anda cari tidak tersedia."), 404

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template("errors/error.html", code=500, title="Terjadi Kesalahan", message="Sistem mengalami gangguan. Silakan coba lagi atau hubungi administrator."), 500

    @app.errorhandler(429)
    def too_many_requests(error):
        return render_template("errors/error.html", code=429, title="Terlalu Banyak Permintaan", message="Tunggu beberapa saat sebelum mencoba kembali."), 429

    @app.template_filter("datetime")
    def format_datetime(value, fmt="%d %b %Y %H:%M"):
        local_value = utc_to_local(value, app.config["APP_TIMEZONE"])
        return local_value.strftime(fmt) if local_value else "-"

    @app.template_filter("datetime_local")
    def format_datetime_local(value):
        local_value = utc_to_local(value, app.config["APP_TIMEZONE"])
        return local_value.strftime("%Y-%m-%dT%H:%M") if local_value else ""

    @app.template_filter("duration")
    def format_duration(hours):
        if hours is None:
            return "-"
        return f"{hours:.1f} jam"

    if app.config.get("AUTO_CREATE_DB", True):
        with app.app_context():
            db.create_all()
            migrate_legacy_sla_deadlines(app)

    return app
