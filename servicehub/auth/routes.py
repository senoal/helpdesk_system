from urllib.parse import urljoin, urlparse
from collections import defaultdict, deque
from time import monotonic

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from servicehub import db
from servicehub.models import User


auth_bp = Blueprint("auth", __name__)
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_LOGIN_FAILURES = 5
login_failures = defaultdict(deque)


def login_attempt_key(email):
    return f"{request.remote_addr or 'unknown'}:{email}"


def is_rate_limited(key):
    attempts = login_failures[key]
    cutoff = monotonic() - LOGIN_WINDOW_SECONDS
    while attempts and attempts[0] < cutoff:
        attempts.popleft()
    return len(attempts) >= MAX_LOGIN_FAILURES


def is_safe_url(target):
    host = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in {"http", "https"} and host.netloc == redirect_url.netloc


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        attempt_key = login_attempt_key(email)
        if is_rate_limited(attempt_key):
            flash("Terlalu banyak percobaan login. Coba lagi dalam 15 menit.", "danger")
            return render_template("auth/login.html"), 429
        user = db.session.scalar(db.select(User).where(User.email == email))
        if user and user.is_active and user.check_password(request.form.get("password", "")):
            login_failures.pop(attempt_key, None)
            login_user(user, remember=bool(request.form.get("remember")))
            next_url = request.args.get("next")
            flash(f"Selamat datang, {user.name}.", "success")
            return redirect(next_url if next_url and is_safe_url(next_url) else url_for("main.dashboard"))
        login_failures[attempt_key].append(monotonic())
        flash("Email atau password tidak valid.", "danger")
    return render_template("auth/login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("Anda telah logout.", "info")
    return redirect(url_for("auth.login"))
