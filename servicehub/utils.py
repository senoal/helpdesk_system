from datetime import UTC, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import abort
from flask_login import current_user


DEFAULT_TIMEZONE = "Asia/Jakarta"


def get_timezone(timezone_name=DEFAULT_TIMEZONE):
    """Load an IANA timezone, with a safe WIB fallback for minimal Windows installs."""
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        # Some Windows Python installations do not include the IANA timezone
        # database. tzdata is installed by requirements.txt, but this fallback
        # keeps the default WIB configuration usable even if dependencies were
        # installed incompletely.
        if timezone_name == DEFAULT_TIMEZONE:
            return timezone(timedelta(hours=7), DEFAULT_TIMEZONE)
        raise RuntimeError(
            f"Data zona waktu '{timezone_name}' tidak tersedia. "
            "Jalankan kembali start.bat untuk memasang dependensi tzdata."
        ) from error


def utc_to_local(value, timezone_name="Asia/Jakarta"):
    if value is None:
        return None
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.astimezone(get_timezone(timezone_name))


def local_to_utc(value, timezone_name="Asia/Jakarta"):
    if value is None:
        return None
    local_value = value.replace(tzinfo=get_timezone(timezone_name)) if value.tzinfo is None else value
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
