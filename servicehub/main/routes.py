from datetime import timedelta

from flask import Blueprint, current_app, render_template
from flask_login import current_user, login_required
from sqlalchemy import case, func

from servicehub import db
from servicehub.models import Ticket, utcnow
from servicehub.utils import utc_to_local


main_bp = Blueprint("main", __name__)


def scoped_ticket_query():
    query = db.select(Ticket)
    if not current_user.is_staff:
        query = query.where(Ticket.requester_id == current_user.id)
    return query


@main_bp.route("/")
@main_bp.route("/dashboard")
@login_required
def dashboard():
    tickets = db.session.scalars(scoped_ticket_query()).all()
    timezone_name = current_app.config["APP_TIMEZONE"]
    now = utc_to_local(utcnow(), timezone_name)
    active = [t for t in tickets if not t.is_completed]
    completed = [t for t in tickets if t.is_completed]
    met = [t for t in completed if not t.is_overdue]
    metrics = {
        "total": len(tickets),
        "open": sum(t.status == "Open" for t in tickets),
        "progress": sum(t.status in {"In Progress", "Pending"} for t in tickets),
        "completed": len(completed),
        "overdue": sum(t.is_overdue and not t.is_completed for t in tickets),
        "sla_compliance": round(len(met) / len(completed) * 100, 1) if completed else 100,
    }
    priorities = ["Low", "Medium", "High", "Critical"]
    priority_counts = [sum(t.priority == p for t in tickets) for p in priorities]
    days = [(now - timedelta(days=i)).date() for i in range(6, -1, -1)]
    daily_counts = [
        sum(utc_to_local(t.created_at, timezone_name).date() == day for t in tickets)
        for day in days
    ]
    status_labels = ["Open", "In Progress", "Pending", "Resolved", "Closed"]
    status_counts = [sum(t.status == s for t in tickets) for s in status_labels]
    recent_tickets = sorted(tickets, key=lambda t: t.created_at, reverse=True)[:8]
    urgent_tickets = sorted(
        [t for t in active if t.sla_state in {"Near Due", "Overdue"}],
        key=lambda t: t.sla_deadline,
    )[:6]
    return render_template(
        "dashboard.html",
        metrics=metrics,
        priorities=priorities,
        priority_counts=priority_counts,
        day_labels=[d.strftime("%d %b") for d in days],
        daily_counts=daily_counts,
        status_labels=status_labels,
        status_counts=status_counts,
        recent_tickets=recent_tickets,
        urgent_tickets=urgent_tickets,
    )
