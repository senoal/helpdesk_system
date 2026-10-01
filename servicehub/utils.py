from datetime import UTC
from functools import wraps
from zoneinfo import ZoneInfo

from flask import abort
from flask_login import current_user


def utc_to_local(value, timezone_name="Asia/Jakarta"):
    if value is None:
        return None
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.astimezone(ZoneInfo(timezone_name))


def local_to_utc(value, timezone_name="Asia/Jakarta"):
    if value is None:
        return None
    local_value = value.replace(tzinfo=ZoneInfo(timezone_name)) if value.tzinfo is None else value
    return local_value.astimezone(UTC).replace(tzinfo=None)


def roles_required(*roles):
    def decorator(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)
            return func(*args, **kwargs)

        return wrapped

    return decorator


def can_access_ticket(user, ticket):
    return user.is_staff or ticket.requester_id == user.id
