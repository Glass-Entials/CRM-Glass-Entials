"""
utils/timezone.py
─────────────────
Single source of truth for timezone handling in GlassEntials CRM.

Architecture:
  • All timestamps are stored in UTC in the database.
  • Conversion to IST (Asia/Kolkata, UTC+5:30) happens only at display time.
  • Never store IST directly — always store UTC.

Usage in Python:
  from utils.timezone import utcnow, to_ist, format_ist

Usage in Jinja templates (registered as filter):
  {{ doc.uploaded_at | ist }}                   → "12 Aug 2026, 11:06 AM"
  {{ doc.uploaded_at | ist('%H:%M') }}          → "11:06"
  {{ log.created_at  | ist('%d %b %Y') }}       → "12 Aug 2026"
"""

from datetime import datetime, timezone, timedelta

# IST is UTC + 5 hours 30 minutes — no DST ever
_IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# Default display format used by the Jinja filter
_DEFAULT_FMT = "%d %b %Y, %I:%M %p"


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime (matches existing model convention)."""
    return datetime.utcnow()


def to_ist(dt: datetime) -> datetime:
    """
    Convert a naive UTC datetime to a naive IST datetime suitable for .strftime().
    If dt is already timezone-aware, it is first normalised to UTC then converted.
    Returns None if dt is None.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        # Already tz-aware — convert to UTC naive first
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt + timedelta(hours=5, minutes=30)


def format_ist(dt: datetime, fmt: str = _DEFAULT_FMT) -> str:
    """
    Format a UTC datetime as an IST string.
    Returns empty string if dt is None.
    """
    if dt is None:
        return ""
    return to_ist(dt).strftime(fmt)


def ist_filter(dt: datetime, fmt: str = _DEFAULT_FMT) -> str:
    """
    Jinja2 template filter.
    Registered in app.py as: app.add_template_filter(ist_filter, 'ist')

    Usage:
        {{ record.created_at | ist }}
        {{ record.created_at | ist('%H:%M') }}
    """
    return format_ist(dt, fmt)


def time_ago_ist(dt: datetime) -> str:
    """
    Return a human-readable relative time string based on IST-aware comparison.
    Suitable for replacing the time_ago utility in app.py context processor.
    """
    if not dt:
        return ""
    now_ist = to_ist(datetime.utcnow())
    dt_ist = to_ist(dt)
    diff = now_ist - dt_ist
    seconds = diff.total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} mins ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    if seconds < 172800:
        return "Yesterday"
    return dt_ist.strftime("%d %b")
