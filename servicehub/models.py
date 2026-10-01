from datetime import UTC, datetime, timedelta

from flask_login import UserMixin
from sqlalchemy import event
from werkzeug.security import check_password_hash, generate_password_hash

from servicehub import db


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Department(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    users = db.relationship("User", back_populates="department")


class Category(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    tickets = db.relationship("Ticket", back_populates="category")


class SLAConfig(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    priority = db.Column(db.String(20), unique=True, nullable=False)
    target_hours = db.Column(db.Integer, nullable=False)
    warning_percent = db.Column(db.Integer, default=80, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class User(UserMixin, TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="Employee", nullable=False)
    is_active_account = db.Column(db.Boolean, default=True, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"))
    department = db.relationship("Department", back_populates="users")
    requested_tickets = db.relationship(
        "Ticket", foreign_keys="Ticket.requester_id", back_populates="requester"
    )
    assigned_tickets = db.relationship(
        "Ticket", foreign_keys="Ticket.assignee_id", back_populates="assignee"
    )
    comments = db.relationship("Comment", back_populates="author")
    uploaded_attachments = db.relationship("TicketAttachment", back_populates="uploader")

    @property
    def is_active(self):
        return self.is_active_account

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_staff(self):
        return self.role in {"Admin", "Agent"}


class Ticket(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_number = db.Column(db.String(30), unique=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), default="Medium", nullable=False, index=True)
    status = db.Column(db.String(30), default="Open", nullable=False, index=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("department.id"), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False)
    assignee_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    sla_deadline = db.Column(db.DateTime, nullable=False, index=True)
    resolved_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    requester = db.relationship(
        "User", foreign_keys=[requester_id], back_populates="requested_tickets"
    )
    assignee = db.relationship(
        "User", foreign_keys=[assignee_id], back_populates="assigned_tickets"
    )
    department = db.relationship("Department")
    category = db.relationship("Category", back_populates="tickets")
    comments = db.relationship(
        "Comment", back_populates="ticket", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    histories = db.relationship(
        "TicketHistory", back_populates="ticket", cascade="all, delete-orphan", order_by="TicketHistory.created_at.desc()"
    )
    attachments = db.relationship(
        "TicketAttachment", back_populates="ticket", cascade="all, delete-orphan", order_by="TicketAttachment.created_at"
    )

    @property
    def is_completed(self):
        return self.status in {"Resolved", "Closed"}

    @property
    def is_overdue(self):
        comparison = self.resolved_at if self.is_completed and self.resolved_at else utcnow()
        return comparison > self.sla_deadline

    @property
    def sla_state(self):
        if self.is_overdue:
            return "Overdue"
        if self.is_completed:
            return "Met"
        total = max((self.sla_deadline - self.created_at).total_seconds(), 1)
        elapsed = (utcnow() - self.created_at).total_seconds()
        config = db.session.scalar(db.select(SLAConfig).where(SLAConfig.priority == self.priority))
        threshold = (config.warning_percent if config else 80) / 100
        return "Near Due" if elapsed / total >= threshold else "On Track"

    @property
    def resolution_hours(self):
        if not self.resolved_at:
            return None
        return (self.resolved_at - self.created_at).total_seconds() / 3600

    @property
    def sla_target_hours(self):
        return (self.sla_deadline - self.created_at).total_seconds() / 3600

    @property
    def sla_remaining_hours(self):
        comparison = self.resolved_at if self.is_completed and self.resolved_at else utcnow()
        return (self.sla_deadline - comparison).total_seconds() / 3600


class Comment(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    is_internal = db.Column(db.Boolean, default=False, nullable=False)
    ticket = db.relationship("Ticket", back_populates="comments")
    author = db.relationship("User", back_populates="comments")


class TicketAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False, index=True)
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(255), unique=True, nullable=False)
    mime_type = db.Column(db.String(120))
    size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    ticket = db.relationship("Ticket", back_populates="attachments")
    uploader = db.relationship("User", back_populates="uploaded_attachments")


class TicketHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    ticket = db.relationship("Ticket", back_populates="histories")
    actor = db.relationship("User")


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    entity_type = db.Column(db.String(50), nullable=False, index=True)
    entity_id = db.Column(db.Integer)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    actor = db.relationship("User")


@event.listens_for(Ticket, "before_insert")
def set_ticket_defaults(mapper, connection, target):
    if not target.sla_deadline:
        table = SLAConfig.__table__
        row = connection.execute(
            table.select().where(table.c.priority == target.priority)
        ).first()
        hours = row.target_hours if row else {"Low": 72, "Medium": 48, "High": 24, "Critical": 4}.get(target.priority, 48)
        target.sla_deadline = (target.created_at or utcnow()) + timedelta(hours=hours)


@event.listens_for(Ticket, "after_insert")
def set_ticket_number(mapper, connection, target):
    if not target.ticket_number:
        number = f"TKT-{(target.created_at or utcnow()):%Y%m}-{target.id:05d}"
        connection.execute(
            Ticket.__table__.update().where(Ticket.id == target.id).values(ticket_number=number)
        )
        target.ticket_number = number
