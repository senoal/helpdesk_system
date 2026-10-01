import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from servicehub import db
from servicehub.models import AuditLog, Category, Department, SLAConfig, User
from servicehub.utils import roles_required


admin_bp = Blueprint("admin", __name__)
ROLES = ["Admin", "Agent", "Employee"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def password_is_valid(password):
    return (
        len(password) >= 10
        and any(char.islower() for char in password)
        and any(char.isupper() for char in password)
        and any(char.isdigit() for char in password)
    )


def valid_department(department_id):
    if not department_id:
        return None
    return db.session.scalar(
        db.select(Department).where(Department.id == department_id, Department.is_active)
    )


def active_admin_count():
    return db.session.scalar(
        db.select(db.func.count(User.id)).where(User.role == "Admin", User.is_active_account)
    )


def add_audit(entity_type, entity_id, action, details):
    db.session.add(AuditLog(
        actor_id=current_user.id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        details=details[:500],
    ))


@admin_bp.route("/")
@login_required
@roles_required("Admin")
def index():
    return render_template(
        "admin/index.html",
        users=db.session.scalars(db.select(User).order_by(User.name)).all(),
        departments=db.session.scalars(db.select(Department).order_by(Department.name)).all(),
        categories=db.session.scalars(db.select(Category).order_by(Category.name)).all(),
        slas=db.session.scalars(db.select(SLAConfig).order_by(SLAConfig.target_hours)).all(),
        audit_logs=db.session.scalars(db.select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100)).all(),
        roles=ROLES,
        priorities=PRIORITIES,
    )


@admin_bp.route("/users/create", methods=["POST"])
@login_required
@roles_required("Admin")
def create_user():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "Employee")
    department = valid_department(request.form.get("department_id", type=int))
    if not request.form.get("name", "").strip() or not EMAIL_PATTERN.match(email) or role not in ROLES or not department:
        flash("Nama, email, role, atau department tidak valid.", "danger")
    elif not password_is_valid(password):
        flash("Password minimal 10 karakter dan harus mengandung huruf besar, huruf kecil, serta angka.", "danger")
    else:
        user = User(
            name=request.form["name"].strip(),
            email=email,
            role=role,
            department=department,
        )
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.flush()
            add_audit("User", user.id, "create", f"Membuat user {user.email} dengan role {user.role}")
            db.session.commit()
            flash("User berhasil dibuat.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Email sudah digunakan.", "danger")
    return redirect(url_for("admin.index"))


@admin_bp.route("/users/<int:user_id>/update", methods=["POST"])
@login_required
@roles_required("Admin")
def update_user(user_id):
    user = db.get_or_404(User, user_id)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "")
    department = valid_department(request.form.get("department_id", type=int))
    if not name or len(name) > 120 or not EMAIL_PATTERN.match(email) or role not in ROLES or not department:
        flash("Data perubahan user tidak valid.", "danger")
        return redirect(url_for("admin.index"))
    if user.role == "Admin" and role != "Admin" and user.is_active_account and active_admin_count() <= 1:
        flash("Admin aktif terakhir tidak dapat diturunkan rolenya.", "danger")
        return redirect(url_for("admin.index"))
    user.name = name
    user.email = email
    user.role = role
    user.department = department
    try:
        add_audit("User", user.id, "update", f"Memperbarui user {user.email}; role={user.role}; department={department.name}")
        db.session.commit()
        flash("Data user berhasil diperbarui.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Email sudah digunakan oleh user lain.", "danger")
    return redirect(url_for("admin.index"))


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@roles_required("Admin")
def reset_password(user_id):
    user = db.get_or_404(User, user_id)
    password = request.form.get("password", "")
    if not password_is_valid(password):
        flash("Password minimal 10 karakter dan harus mengandung huruf besar, huruf kecil, serta angka.", "danger")
    else:
        user.set_password(password)
        add_audit("User", user.id, "reset_password", f"Mereset password user {user.email}")
        db.session.commit()
        flash(f"Password {user.name} berhasil direset.", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@login_required
@roles_required("Admin")
def toggle_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id and user.is_active_account:
        flash("Anda tidak dapat menonaktifkan akun sendiri.", "danger")
        return redirect(url_for("admin.index"))
    if user.role == "Admin" and user.is_active_account and active_admin_count() <= 1:
        flash("Admin aktif terakhir tidak dapat dinonaktifkan.", "danger")
        return redirect(url_for("admin.index"))
    user.is_active_account = not user.is_active_account
    add_audit("User", user.id, "toggle", f"Status user {user.email}: {'aktif' if user.is_active_account else 'nonaktif'}")
    db.session.commit()
    flash("Status user diperbarui.", "success")
    return redirect(url_for("admin.index"))


def create_named_model(model, label):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    if not name or len(name) > 100 or len(description) > 255:
        flash(f"Nama {label} wajib diisi.", "danger")
    else:
        duplicate = db.session.scalar(db.select(model).where(db.func.lower(model.name) == name.lower()))
        if duplicate:
            flash(f"Nama {label} sudah tersedia.", "danger")
            return
        item = model(name=name, description=description)
        db.session.add(item)
        try:
            db.session.flush()
            add_audit(model.__name__, item.id, "create", f"Membuat {label} {name}")
            db.session.commit()
            flash(f"{label.title()} berhasil ditambahkan.", "success")
        except IntegrityError:
            db.session.rollback()
            flash(f"Nama {label} sudah tersedia.", "danger")


@admin_bp.route("/departments/create", methods=["POST"])
@login_required
@roles_required("Admin")
def create_department():
    create_named_model(Department, "department")
    return redirect(url_for("admin.index"))


@admin_bp.route("/categories/create", methods=["POST"])
@login_required
@roles_required("Admin")
def create_category():
    create_named_model(Category, "kategori")
    return redirect(url_for("admin.index"))


@admin_bp.route("/master/<kind>/<int:item_id>/toggle", methods=["POST"])
@login_required
@roles_required("Admin")
def toggle_master(kind, item_id):
    model = {"department": Department, "category": Category}.get(kind)
    if not model:
        return "Not found", 404
    item = db.get_or_404(model, item_id)
    item.is_active = not item.is_active
    add_audit(model.__name__, item.id, "toggle", f"Status {item.name}: {'aktif' if item.is_active else 'nonaktif'}")
    db.session.commit()
    flash("Status master data diperbarui.", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/master/<kind>/<int:item_id>/update", methods=["POST"])
@login_required
@roles_required("Admin")
def update_master(kind, item_id):
    model = {"department": Department, "category": Category}.get(kind)
    if not model:
        return "Not found", 404
    item = db.get_or_404(model, item_id)
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    if not name or len(name) > 100 or len(description) > 255:
        flash("Nama atau deskripsi tidak valid.", "danger")
        return redirect(url_for("admin.index"))
    duplicate = db.session.scalar(
        db.select(model).where(db.func.lower(model.name) == name.lower(), model.id != item.id)
    )
    if duplicate:
        flash("Nama tersebut sudah digunakan.", "danger")
        return redirect(url_for("admin.index"))
    item.name = name
    item.description = description
    add_audit(model.__name__, item.id, "update", f"Memperbarui master data menjadi {name}")
    db.session.commit()
    flash("Master data berhasil diperbarui.", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/sla/save", methods=["POST"])
@login_required
@roles_required("Admin")
def save_sla():
    priority = request.form.get("priority")
    hours = request.form.get("target_hours", type=int)
    warning = request.form.get("warning_percent", 80, type=int)
    if priority not in PRIORITIES or not hours or hours < 1 or not 1 <= warning <= 100:
        flash("Konfigurasi SLA tidak valid.", "danger")
    else:
        config = db.session.scalar(db.select(SLAConfig).where(SLAConfig.priority == priority))
        if config:
            config.target_hours = hours
            config.warning_percent = warning
        else:
            config = SLAConfig(priority=priority, target_hours=hours, warning_percent=warning)
            db.session.add(config)
            db.session.flush()
        add_audit("SLAConfig", config.id, "update", f"{priority}: target={hours} jam; warning={warning}%")
        db.session.commit()
        flash("Konfigurasi SLA disimpan.", "success")
    return redirect(url_for("admin.index"))
