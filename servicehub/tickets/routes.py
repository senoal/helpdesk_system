from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from servicehub import db
from servicehub.models import Category, Comment, Department, SLAConfig, Ticket, TicketAttachment, TicketHistory, User, utcnow
from servicehub.utils import can_access_ticket, local_to_utc, roles_required, utc_to_local


tickets_bp = Blueprint("tickets", __name__)
PRIORITIES = ["Low", "Medium", "High", "Critical"]
STATUSES = ["Open", "In Progress", "Pending", "Resolved", "Closed"]
ALLOWED_ATTACHMENT_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "png", "jpg", "jpeg", "gif", "webp"}
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 10000
MAX_COMMENT_LENGTH = 5000


def attachment_size(file):
    position = file.stream.tell()
    file.stream.seek(0, 2)
    size = file.stream.tell()
    file.stream.seek(position)
    return size


def has_valid_file_signature(file, extension):
    position = file.stream.tell()
    file.stream.seek(0)
    header = file.stream.read(16)
    file.stream.seek(0)
    try:
        if extension == "pdf":
            valid = header.startswith(b"%PDF-")
        elif extension == "png":
            valid = header.startswith(b"\x89PNG\r\n\x1a\n")
        elif extension in {"jpg", "jpeg"}:
            valid = header.startswith(b"\xff\xd8\xff")
        elif extension == "gif":
            valid = header.startswith((b"GIF87a", b"GIF89a"))
        elif extension == "webp":
            valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        elif extension in {"doc", "xls"}:
            valid = header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
        elif extension in {"docx", "xlsx"}:
            try:
                with ZipFile(file.stream) as archive:
                    names = set(archive.namelist())
                    marker = "word/" if extension == "docx" else "xl/"
                    valid = "[Content_Types].xml" in names and any(name.startswith(marker) for name in names)
            except (BadZipFile, OSError):
                valid = False
        else:
            valid = False
    finally:
        file.stream.seek(position)
    return valid


def validate_attachments(files, existing_count=0):
    max_files = current_app.config["MAX_ATTACHMENTS_PER_TICKET"]
    max_size = current_app.config["MAX_ATTACHMENT_SIZE"]
    if existing_count + len(files) > max_files:
        remaining = max(max_files - existing_count, 0)
        return f"Maksimal {max_files} lampiran untuk setiap tiket. Anda hanya dapat menambahkan {remaining} file lagi."
    for file in files:
        filename = secure_filename(file.filename or "")
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not filename or extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
            return f"Format file '{file.filename}' tidak didukung."
        if len(filename) > 255:
            return f"Nama file '{file.filename}' terlalu panjang."
        size = attachment_size(file)
        if size == 0:
            return f"File '{file.filename}' kosong."
        if size > max_size:
            return f"Ukuran file '{file.filename}' melebihi {max_size // (1024 * 1024)} MB."
        if not has_valid_file_signature(file, extension):
            return f"Isi file '{file.filename}' tidak sesuai dengan format {extension.upper()}."
    return None


def save_attachments(files, ticket):
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    upload_folder.mkdir(parents=True, exist_ok=True)
    saved_paths = []
    try:
        for file in files:
            original_name = secure_filename(file.filename)
            extension = original_name.rsplit(".", 1)[-1].lower()
            stored_name = f"{uuid4().hex}.{extension}"
            destination = upload_folder / stored_name
            file.save(destination)
            saved_paths.append(destination)
            db.session.add(TicketAttachment(
                ticket_id=ticket.id,
                uploader_id=current_user.id,
                original_name=original_name,
                stored_name=stored_name,
                mime_type=file.mimetype,
                size=destination.stat().st_size,
            ))
    except Exception:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise
    return saved_paths


def commit_attachments(files, ticket, history_action):
    saved_paths = []
    try:
        saved_paths = save_attachments(files, ticket)
        db.session.add(TicketHistory(
            ticket_id=ticket.id,
            actor_id=current_user.id,
            action=history_action,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise


def active_options():
    categories = db.session.scalars(db.select(Category).where(Category.is_active).order_by(Category.name)).all()
    departments = db.session.scalars(db.select(Department).where(Department.is_active).order_by(Department.name)).all()
    agents = db.session.scalars(
        db.select(User).where(User.role.in_(["Admin", "Agent"]), User.is_active_account).order_by(User.name)
    ).all()
    return categories, departments, agents


def active_master(model, item_id):
    if not item_id:
        return None
    return db.session.scalar(db.select(model).where(model.id == item_id, model.is_active))


def active_agent(user_id):
    if not user_id:
        return None
    return db.session.scalar(
        db.select(User).where(
            User.id == user_id,
            User.role.in_(["Admin", "Agent"]),
            User.is_active_account,
        )
    )


@tickets_bp.app_errorhandler(RequestEntityTooLarge)
def attachment_request_too_large(error):
    flash("Total ukuran upload terlalu besar. Maksimal 10 MB per file.", "danger")
    ticket_id = (request.view_args or {}).get("ticket_id")
    if ticket_id:
        return redirect(url_for("tickets.detail", ticket_id=ticket_id))
    return redirect(url_for("tickets.create"))


@tickets_bp.route("/")
@login_required
def index():
    query = db.select(Ticket).options(
        joinedload(Ticket.requester), joinedload(Ticket.assignee), joinedload(Ticket.category)
    )
    if not current_user.is_staff:
        query = query.where(Ticket.requester_id == current_user.id)
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    category_id = request.args.get("category", type=int)
    if search:
        term = f"%{search}%"
        query = query.where(or_(Ticket.ticket_number.ilike(term), Ticket.title.ilike(term), Ticket.description.ilike(term)))
    if status in STATUSES:
        query = query.where(Ticket.status == status)
    if priority in PRIORITIES:
        query = query.where(Ticket.priority == priority)
    if category_id:
        query = query.where(Ticket.category_id == category_id)
    sort = request.args.get("sort", "newest")
    orders = {
        "oldest": Ticket.created_at.asc(),
        "deadline": Ticket.sla_deadline.asc(),
        "priority": db.case({"Critical": 1, "High": 2, "Medium": 3, "Low": 4}, value=Ticket.priority),
    }
    query = query.order_by(orders.get(sort, Ticket.created_at.desc()))
    page = db.paginate(query, page=request.args.get("page", 1, type=int), per_page=10, error_out=False)
    categories = db.session.scalars(db.select(Category).where(Category.is_active).order_by(Category.name)).all()
    return render_template("tickets/index.html", page=page, categories=categories, statuses=STATUSES, priorities=PRIORITIES)


@tickets_bp.route("/create", methods=["GET", "POST"])
@login_required
def create():
    categories, departments, agents = active_options()
    if request.method == "POST":
        files = [file for file in request.files.getlist("attachments") if file and file.filename]
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        priority = request.form.get("priority", "Medium")
        category_id = request.form.get("category_id", type=int)
        department_id = request.form.get("department_id", type=int)
        category = active_master(Category, category_id)
        department = active_master(Department, department_id)
        requested_assignee_id = request.form.get("assignee_id", type=int) if current_user.is_staff else None
        assignee = active_agent(requested_assignee_id)
        attachment_error = validate_attachments(files)
        if not title or not description or priority not in PRIORITIES:
            flash("Lengkapi seluruh field wajib.", "danger")
        elif len(title) > MAX_TITLE_LENGTH or len(description) > MAX_DESCRIPTION_LENGTH:
            flash("Judul maksimal 200 karakter dan deskripsi maksimal 10.000 karakter.", "danger")
        elif not category or not department:
            flash("Kategori atau department tidak valid/tidak aktif.", "danger")
        elif requested_assignee_id and not assignee:
            flash("Assignee harus merupakan Admin atau Agent aktif.", "danger")
        elif attachment_error:
            flash(attachment_error, "danger")
        else:
            ticket = Ticket(
                title=title,
                description=description,
                priority=priority,
                category=category,
                department=department,
                requester_id=current_user.id,
            )
            ticket.assignee = assignee
            db.session.add(ticket)
            db.session.flush()
            action = "Tiket dibuat"
            if files:
                action += f" dengan {len(files)} lampiran"
            commit_attachments(files, ticket, action)
            flash(f"Tiket {ticket.ticket_number} berhasil dibuat.", "success")
            return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    return render_template("tickets/form.html", categories=categories, departments=departments, agents=agents, priorities=PRIORITIES)


@tickets_bp.route("/<int:ticket_id>")
@login_required
def detail(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    if not can_access_ticket(current_user, ticket):
        abort(403)
    _, _, agents = active_options()
    return render_template("tickets/detail.html", ticket=ticket, agents=agents, statuses=STATUSES, priorities=PRIORITIES)


@tickets_bp.route("/<int:ticket_id>/attachments/<int:attachment_id>")
@login_required
def download_attachment(ticket_id, attachment_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    if not can_access_ticket(current_user, ticket):
        abort(403)
    attachment = db.get_or_404(TicketAttachment, attachment_id)
    if attachment.ticket_id != ticket.id:
        abort(404)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        attachment.stored_name,
        as_attachment=True,
        download_name=attachment.original_name,
        mimetype=attachment.mime_type,
    )


@tickets_bp.route("/<int:ticket_id>/sla", methods=["POST"])
@login_required
@roles_required("Admin")
def update_sla(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    if ticket.is_completed:
        flash("Deadline SLA tiket yang sudah selesai tidak dapat diubah.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    raw_deadline = request.form.get("sla_deadline", "").strip()
    try:
        local_deadline = datetime.strptime(raw_deadline, "%Y-%m-%dT%H:%M")
        deadline = local_to_utc(local_deadline, current_app.config["APP_TIMEZONE"])
    except ValueError:
        flash("Tanggal dan waktu deadline SLA tidak valid.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    if deadline <= ticket.created_at:
        flash("Deadline SLA harus setelah waktu tiket dibuat.", "danger")
    elif deadline == ticket.sla_deadline.replace(second=0, microsecond=0):
        flash("Deadline SLA tidak berubah.", "info")
    else:
        old_deadline = ticket.sla_deadline
        ticket.sla_deadline = deadline
        old_local = utc_to_local(old_deadline, current_app.config["APP_TIMEZONE"])
        new_local = utc_to_local(deadline, current_app.config["APP_TIMEZONE"])
        db.session.add(TicketHistory(
            ticket_id=ticket.id,
            actor_id=current_user.id,
            action=f"Deadline SLA: {old_local:%d %b %Y %H:%M} → {new_local:%d %b %Y %H:%M} WIB",
        ))
        db.session.commit()
        flash("Deadline SLA berhasil diperbarui.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))


@tickets_bp.route("/<int:ticket_id>/attachments", methods=["POST"])
@login_required
def add_attachments(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    if not can_access_ticket(current_user, ticket):
        abort(403)
    files = [file for file in request.files.getlist("attachments") if file and file.filename]
    if not files:
        flash("Pilih minimal satu file untuk dilampirkan.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    attachment_error = validate_attachments(files, existing_count=len(ticket.attachments))
    if attachment_error:
        flash(attachment_error, "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    commit_attachments(
        files,
        ticket,
        f"Menambahkan {len(files)} lampiran",
    )
    flash(f"{len(files)} lampiran berhasil ditambahkan.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))


@tickets_bp.route("/<int:ticket_id>/update", methods=["POST"])
@login_required
def update(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    if not current_user.is_staff:
        abort(403)
    changes = []
    new_status = request.form.get("status", ticket.status)
    new_priority = request.form.get("priority", ticket.priority)
    assignee_id = request.form.get("assignee_id", type=int)
    if new_status not in STATUSES or new_priority not in PRIORITIES:
        flash("Status atau priority tidak valid.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    requested_agent = active_agent(assignee_id)
    if assignee_id and not requested_agent:
        flash("Assignee harus merupakan Admin atau Agent aktif.", "danger")
        return redirect(url_for("tickets.detail", ticket_id=ticket.id))
    if new_status in STATUSES and new_status != ticket.status:
        changes.append(f"Status: {ticket.status} → {new_status}")
        ticket.status = new_status
        if new_status == "Resolved" and not ticket.resolved_at:
            ticket.resolved_at = utcnow()
        elif new_status not in {"Resolved", "Closed"}:
            ticket.resolved_at = None
            ticket.closed_at = None
        if new_status == "Closed":
            ticket.closed_at = utcnow()
            ticket.resolved_at = ticket.resolved_at or ticket.closed_at
        elif new_status != "Closed":
            ticket.closed_at = None
    if new_priority in PRIORITIES and new_priority != ticket.priority:
        if ticket.is_completed:
            flash("Priority dan deadline SLA tiket selesai tidak dapat diubah.", "warning")
        else:
            changes.append(f"Priority: {ticket.priority} → {new_priority}")
            ticket.priority = new_priority
            sla = db.session.scalar(db.select(SLAConfig).where(SLAConfig.priority == new_priority))
            hours = sla.target_hours if sla else {"Low": 72, "Medium": 48, "High": 24, "Critical": 4}[new_priority]
            ticket.sla_deadline = ticket.created_at + timedelta(hours=hours)
    if assignee_id != ticket.assignee_id:
        old = ticket.assignee.name if ticket.assignee else "Belum ditugaskan"
        ticket.assignee = requested_agent
        changes.append(f"Assignee: {old} → {requested_agent.name if requested_agent else 'Belum ditugaskan'}")
    if changes:
        db.session.add(TicketHistory(ticket_id=ticket.id, actor_id=current_user.id, action="; ".join(changes)))
        db.session.commit()
        flash("Tiket berhasil diperbarui.", "success")
    else:
        flash("Tidak ada perubahan.", "info")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))


@tickets_bp.route("/<int:ticket_id>/comments", methods=["POST"])
@login_required
def add_comment(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    if not can_access_ticket(current_user, ticket):
        abort(403)
    body = request.form.get("body", "").strip()
    if not body:
        flash("Komentar tidak boleh kosong.", "danger")
    elif len(body) > MAX_COMMENT_LENGTH:
        flash("Komentar maksimal 5.000 karakter.", "danger")
    else:
        internal = current_user.is_staff and bool(request.form.get("is_internal"))
        db.session.add(Comment(body=body, ticket_id=ticket.id, author_id=current_user.id, is_internal=internal))
        db.session.add(TicketHistory(ticket_id=ticket.id, actor_id=current_user.id, action="Menambahkan catatan internal" if internal else "Menambahkan komentar"))
        db.session.commit()
        flash("Komentar ditambahkan.", "success")
    return redirect(url_for("tickets.detail", ticket_id=ticket.id))
