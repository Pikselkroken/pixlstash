"""Unit tests for the GFS tier-decision logic (EnsureGfsSnapshotFinder)."""

from datetime import datetime, timezone

from pixlstash.tasks.ensure_gfs_snapshot_finder import EnsureGfsSnapshotFinder


def _dt(y, m, d, hh=12):
    return datetime(y, m, d, hh, 0, 0, tzinfo=timezone.utc)


_due = EnsureGfsSnapshotFinder._due_kind


def test_empty_vault_bootstraps_with_monthly():
    # No snapshots yet → the highest due tier (MONTHLY) is taken first.
    assert _due(_dt(2026, 1, 15), []) == "MONTHLY"


def test_aligned_boundary_day_yields_single_monthly():
    # A MONTHLY taken today satisfies this week's WEEKLY and today's DAILY too,
    # so nothing further is due on an aligned boundary day.
    now = _dt(2026, 1, 1)
    auto = [("MONTHLY", _dt(2026, 1, 1, 1))]
    assert _due(now, auto) is None


def test_weekly_due_when_month_has_monthly_but_week_has_none():
    # Monthly exists (Jan 1) but we're in a later ISO week with no weekly yet.
    now = _dt(2026, 1, 15)
    auto = [("MONTHLY", _dt(2026, 1, 1))]
    assert _due(now, auto) == "WEEKLY"


def test_daily_due_when_month_and_week_covered_but_not_today():
    now = _dt(2026, 1, 15)  # Thursday
    auto = [
        ("MONTHLY", _dt(2026, 1, 1)),
        ("WEEKLY", _dt(2026, 1, 12)),  # Monday of the same ISO week
    ]
    assert _due(now, auto) == "DAILY"


def test_nothing_due_when_today_already_has_an_auto_snapshot():
    now = _dt(2026, 1, 15)
    auto = [
        ("MONTHLY", _dt(2026, 1, 1)),
        ("WEEKLY", _dt(2026, 1, 12)),
        ("DAILY", _dt(2026, 1, 15, 6)),
    ]
    assert _due(now, auto) is None


def test_weekly_counts_toward_today_slot():
    # A WEEKLY taken today also fills today's DAILY slot (month already covered).
    now = _dt(2026, 1, 12)  # Monday
    auto = [
        ("MONTHLY", _dt(2026, 1, 1)),
        ("WEEKLY", _dt(2026, 1, 12, 3)),
    ]
    assert _due(now, auto) is None


def test_new_month_takes_monthly_even_if_week_already_had_one():
    # Crossing into Feb: a Jan weekly does not satisfy Feb's monthly slot.
    now = _dt(2026, 2, 2)
    auto = [
        ("MONTHLY", _dt(2026, 1, 1)),
        ("WEEKLY", _dt(2026, 1, 26)),
        ("DAILY", _dt(2026, 2, 1)),
    ]
    assert _due(now, auto) == "MONTHLY"


def test_naive_created_at_is_treated_as_utc():
    # created_at without tzinfo must not raise and is compared as UTC.
    now = _dt(2026, 1, 15)
    naive = datetime(2026, 1, 1, 12, 0, 0)  # no tzinfo
    assert _due(now, [("MONTHLY", naive)]) == "WEEKLY"
