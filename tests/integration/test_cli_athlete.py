"""What planning needs, through the CLI (P12; onboarding spec 2 to 4, profile spec 3 to 5,
goals spec 1 and 2). Synthetic data only."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli_harness import Cli
from coach.application.ownership import OwnerScope
from coach.cli.runtime import build_runtime
from coach.domain.coaching.state import Condition
from coach.infrastructure.datadir import ensure_layout
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory

PLANTED = "Zx81Qp0LmN4vB7cT2wYe9KdR"  # pragma: allowlist secret


@pytest.fixture
def cli(tmp_path: Path) -> Cli:
    command = Cli(tmp_path / "coach-data")
    assert command.run("init")[0] == 0
    return command


def ok(cli: Cli, *args: str) -> str:
    code, output = cli.run(*args)
    assert code == 0, output
    return output


@pytest.mark.req("FR-ONB-003", "FR-ONB-004", "FR-ONB-005", "FR-ONB-006", "FR-PRF-004")
@pytest.mark.acceptance("AT-ONB-002")
def test_onboarding_inputs_bring_readiness_and_the_lifecycle_forward(cli: Cli) -> None:
    first = ok(cli, "readiness")
    assert first.startswith("readiness: NOT_READY")
    assert "missing (required): weekly availability" in first
    assert "coaching state: NEW_USER" in first
    ok(cli, "profile", "timezone", "America/Argentina/Buenos_Aires")
    ok(cli, "profile", "name", "Synthetic Runner")
    ok(cli, "profile", "birth-date", "1985-03-02")
    ok(cli, "consent", "decline", "ai-processing")
    ok(cli, "profile", "health", "none")
    ok(
        cli,
        "profile",
        "resources",
        "--gym",
        "yes",
        "--equipment",
        "dumbbells,bench",
        "--pool",
        "25",
        "--no-tennis",
    )
    ok(cli, "profile", "experience", "running", "--years", "3", "--race", "10k=50:00@2026-08-01")
    ok(
        cli,
        "profile",
        "experience",
        "strength",
        "--years",
        "1",
        "--sessions-per-week",
        "2",
        "--rpe-familiar",
        "yes",
    )
    ok(cli, "profile", "experience", "tennis", "--sessions-per-week", "0")
    ok(
        cli,
        "availability",
        "set",
        "--days",
        "4",
        "--max-hours",
        "6",
        "--rest-day",
        "mon",
        "--window",
        "tue 18:00-20:00",
    )
    added = ok(
        cli,
        "goal",
        "add",
        "--sport",
        "running",
        "--distance-km",
        "10",
        "--target-time",
        "45:00",
        "--date",
        "2026-12-20",
    )
    goal_id = added.split()[-1]
    ok(cli, "goal", "activate", goal_id)
    feasible = ok(cli, "goal", "feasibility")
    lines = feasible.splitlines()
    assert lines[0] == "feasibility: UNLIKELY, confidence HIGH (rules 1.0.0)"
    assert "  gap: 0:05:00 to gain" in lines
    assert "  available: 8.0 weeks, 6 h a week" in lines
    assert "  required progression: 38 s a week (1.25 % a week)" in lines
    assert "  desired goal: 0:45:00" in lines
    assert "  realistic goal: 0:49:00" in lines
    assert "  minimum satisfactory goal: 0:50:00" in lines
    assert lines[-1] == "an estimate from your data, never a guarantee of the outcome"
    assert "you will" not in feasible.lower()
    report = ok(cli, "readiness")
    assert report.startswith("readiness: PROVISIONAL")
    assert "missing (required)" not in report
    assert "missing (preferred): recent activity history (8 weeks)" in report
    assert "history for the goal sport: insufficient (FEW_ACTIVITIES)" in report
    assert "coaching state: BASELINE_COLLECTION" in report
    other = ok(
        cli, "goal", "add", "--sport", "running", "--distance-km", "21.1", "--date", "2027-04-10"
    )
    code, output = cli.run("goal", "activate", other.split()[-1])
    assert (code, "another ACTIVE priority-A goal" in output) == (2, True)
    listing = ok(cli, "goal", "list")
    assert "ACTIVE  RUNNING 10 km in 0:45:00" in listing
    shown = ok(cli, "availability", "show", "--days", "3")
    assert "weekly: 4 days, at most 6 h, rest days mon" in shown
    assert "2026-10-26 mon  unavailable (RECURRING)" in shown
    assert "2026-10-27 tue  120 min (RECURRING)" in shown


@pytest.mark.req("FR-PRF-005", "FR-ONB-006")
@pytest.mark.acceptance("AT-AVL-001")
def test_a_day_without_availability_is_not_plannable_and_raises_data_incomplete(
    cli: Cli,
) -> None:
    ok(cli, "profile", "timezone", "America/Argentina/Buenos_Aires")
    ok(cli, "availability", "set", "--days", "2", "--max-hours", "3", "--window", "thu 07:00-08:30")
    shown = ok(cli, "availability", "show", "--days", "5")
    assert "2026-10-26 mon  undefined (UNDEFINED)" in shown  # no window, exception or default
    assert "2026-10-29 thu  90 min (RECURRING)" in shown
    runtime = build_runtime(ensure_layout(cli.data_dir), cli.credentials, lambda: cli.now)
    owner = SqliteIdentityDirectory(SqliteDatabase.open(cli.data_dir / "coach.sqlite3")).owner()
    assert owner is not None
    scope = OwnerScope(owner.athlete_id)
    assert Condition.DATA_INCOMPLETE in runtime.conditions.current(scope).conditions
    assert "  incomplete (DATA_INCOMPLETE): a day of the coming week has no availability" in ok(
        cli, "readiness"
    )
    ok(cli, "availability", "default", "--minutes", "60")  # every day is plannable again
    assert Condition.DATA_INCOMPLETE not in runtime.conditions.current(scope).conditions
    assert "DATA_INCOMPLETE" not in ok(cli, "readiness")
    actions = [
        record.document["action"]
        for record in runtime.audit.events_for(scope)
        if (record.document["target"] or {}).get("type") == "CONDITION"
    ]
    assert actions == ["coaching.condition_activated", "coaching.condition_cleared"]


@pytest.mark.req("FR-PRF-005", "FR-ONB-006")
def test_setting_the_timezone_re_evaluates_data_incomplete(cli: Cli) -> None:
    """Review of 2026-11-01: profile commands outside the inputs service skipped it."""
    ok(cli, "availability", "set", "--days", "2", "--max-hours", "3")  # no timezone: no today
    runtime = build_runtime(ensure_layout(cli.data_dir), cli.credentials, lambda: cli.now)
    owner = SqliteIdentityDirectory(SqliteDatabase.open(cli.data_dir / "coach.sqlite3")).owner()
    assert owner is not None
    scope = OwnerScope(owner.athlete_id)
    assert Condition.DATA_INCOMPLETE not in runtime.conditions.current(scope).conditions
    ok(cli, "profile", "timezone", "America/Argentina/Buenos_Aires")
    assert Condition.DATA_INCOMPLETE in runtime.conditions.current(scope).conditions


@pytest.mark.req("FR-PRF-006", "SEC-SEC-005", "SAFE-MED-001")
def test_health_constraints_name_regions_exactly_and_screen_their_text(cli: Cli) -> None:
    code, output = cli.run(
        "profile", "health", "add", "--description", "molestia", "--region", "tobillo raro"
    )
    assert (code, "not a body region name" in output) == (2, True)
    output = ok(
        cli,
        "profile",
        "health",
        "add",
        "--description",
        f"rodilla sensible, api_key={PLANTED}",
        "--region",
        "rodilla",
        "--no-impact",
        "--max-rpe",
        "6",
    )
    assert "health declaration recorded: 1 constraints" in output
    shown = ok(cli, "profile", "health", "show")
    assert PLANTED not in shown
    assert "regions KNEES  NO_IMPACT, MAX_RPE 6" in shown
    constraint_id = shown.splitlines()[1].split()[0]
    ok(cli, "profile", "health", "remove", constraint_id)
    assert "declared on" in ok(cli, "profile", "health", "show")
    confirmed = ok(cli, "profile", "health", "confirm")
    assert confirmed.strip() == "health declaration recorded: 0 constraints"


@pytest.mark.req("FR-GOL-001", "FR-GOL-002")
def test_events_and_goals_are_owned_and_validated(cli: Cli) -> None:
    ok(cli, "profile", "timezone", "America/Argentina/Buenos_Aires")
    added = ok(
        cli,
        "event",
        "add",
        "--name",
        "Synthetic 10K",
        "--sport",
        "running",
        "--date",
        "2026-12-20",
        "--distance-km",
        "10",
    )
    event_id = added.split()[-1]
    assert "Synthetic 10K" in ok(cli, "event", "list")
    code, output = cli.run("goal", "add", "--sport", "running", "--date", "2026-01-01")
    assert (code, "today or later" in output) == (2, True)
    code, output = cli.run(
        "goal",
        "add",
        "--sport",
        "running",
        "--date",
        "2026-12-20",
        "--event",
        "0193b2a0-0000-7000-8000-000000000999",
    )
    assert (code, output.strip()) == (2, "error: goal or event not found")
    ok(cli, "goal", "add", "--sport", "running", "--date", "2026-12-20", "--event", event_id)
