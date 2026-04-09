"""Order deadline calculation with Dutch public holiday awareness."""

import datetime

import holidays

# Dutch public holidays (includes Easter Monday, King's Day, Whit Monday, Christmas, etc.)
NL_HOLIDAYS = holidays.Netherlands()


def get_order_deadline(week: str) -> tuple[datetime.datetime, bool]:
    """Calculate the order deadline for a given ISO week.

    Default deadline: Monday 08:00 Europe/Amsterdam.
    If Monday is a public holiday, shift forward one day at a time
    until a non-holiday weekday is found.

    Args:
        week: ISO week string like '2026-W15'.

    Returns:
        Tuple of (deadline_datetime, was_extended).
    """
    monday = datetime.datetime.strptime(week + "-1", "%G-W%V-%u").date()
    deadline_date = monday
    extended = False

    while deadline_date in NL_HOLIDAYS:
        deadline_date += datetime.timedelta(days=1)
        extended = True

    deadline_dt = datetime.datetime.combine(
        deadline_date, datetime.time(8, 0)
    )
    return deadline_dt, extended


def get_next_deadline() -> tuple[str, datetime.datetime, bool]:
    """Get the next upcoming deadline.

    If the current week's deadline has passed, return next week's deadline.

    Returns:
        Tuple of (week_string, deadline_datetime, was_extended).
    """
    now = datetime.datetime.now()
    today = now.date()
    week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    deadline_dt, extended = get_order_deadline(week)

    if now > deadline_dt:
        # Deadline passed, show next week
        next_week_date = today + datetime.timedelta(days=7)
        week = f"{next_week_date.isocalendar().year}-W{next_week_date.isocalendar().week:02d}"
        deadline_dt, extended = get_order_deadline(week)

    return week, deadline_dt, extended
