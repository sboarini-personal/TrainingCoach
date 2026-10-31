"""Plan versions through the CLI (P12; planning spec 2 and 3). The plans are proposed through
the runtime, as the generator does; the athlete lists, reviews, activates and rejects them.
Synthetic data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from cli_harness import Cli
from coach.application.ownership import OwnerScope
from coach.cli.runtime import Runtime, build_runtime
from coach.domain.plans.plan import Generator, GeneratorKind
from coach.infrastructure.datadir import ensure_layout
from coach.infrastructure.sqlite.database import SqliteDatabase
from coach.infrastructure.sqlite.identities import SqliteIdentityDirectory
from file_samples import fit_file, fit_session
from plan_samples import MONDAY, content, easy_run, rest, week

ENGINE = Generator(kind=GeneratorKind.ENGINE, engine_version="1.0.0")
ONBOARDING = (
    ("profile", "timezone", "America/Argentina/Buenos_Aires"),
    ("profile", "name", "Synthetic Runner"),
    ("profile", "birth-date", "1985-03-02"),
    ("profile", "health", "none"),
    ("profile", "resources", "--gym", "yes", "--equipment", "dumbbells,bench", "--no-tennis"),
    ("profile", "experience", "running", "--years", "3", "--race", "10k=50:00@2026-08-01"),
    ("profile", "experience", "strength", "--years", "1", "--sessions-per-week", "2"),
    ("profile", "experience", "tennis", "--sessions-per-week", "0"),
    ("availability", "set", "--days", "6", "--max-hours", "10"),
    ("availability", "default", "--minutes", "240"),
)


def ok(cli: Cli, *args: str) -> str:
    code, output = cli.run(*args)
    assert code == 0, output
    return output


def onboarded(tmp_path: Path, *goal: str) -> tuple[Cli, Runtime, OwnerScope, UUID]:
    """Every REQUIRED input and an active running goal; no history, so PROVISIONAL."""
    cli = Cli(tmp_path / "coach-data")
    assert cli.run("init")[0] == 0
    for command in ONBOARDING:
        ok(cli, *command)
    added = ok(
        cli,
        "goal",
        "add",
        "--sport",
        "running",
        "--date",
        "2027-03-14",
        "--distance-km",
        "10",
        *goal,
    )
    goal_id = added.strip().rsplit(" ", 1)[-1]
    ok(cli, "goal", "activate", goal_id)
    owner = SqliteIdentityDirectory(SqliteDatabase.open(cli.data_dir / "coach.sqlite3")).owner()
    assert owner is not None
    runtime = build_runtime(ensure_layout(cli.data_dir), cli.credentials, lambda: cli.now)
    return cli, runtime, OwnerScope(owner.athlete_id), UUID(goal_id)


@pytest.mark.req("FR-PLN-002", "FR-PLN-003", "FR-PLN-004")
@pytest.mark.acceptance("AT-PLN-001", "AT-PLN-002")
def test_the_athlete_reviews_activates_and_rejects_plan_versions(tmp_path: Path) -> None:
    cli, runtime, scope, goal = onboarded(tmp_path)
    assert ok(cli, "plan", "list") == "no plans yet\n"
    first = runtime.plans.propose(
        scope, goal_id=goal, content=content(provisional=True), generator=ENGINE
    )
    plan_id = str(first.plan_id)
    shown = ok(cli, "plan", "show")
    assert f"plan {plan_id} version 1: PROPOSED" in shown
    assert "PROVISIONAL: built on assumptions" in shown
    assert "  assumption: No recent history" in shown
    assert "2026-10-29 Thu  ENDURANCE RUNNING 60 min up to RPE 8 [key]" in shown
    assert f"content hash {first.content_hash}" in shown
    listed = ok(cli, "plan", "list")
    assert f"{plan_id}  goal {goal}  revision 1  active version none" in listed
    with pytest.raises(SystemExit):  # the revision the athlete reviewed is required
        cli.run("plan", "activate", plan_id, "1")
    activated = ok(cli, "plan", "activate", plan_id, "1", "--revision", "1")
    assert activated.endswith("version 1 is ACTIVE\n")

    adapted = content(
        week(MONDAY, {0: [rest(MONDAY)], 2: [easy_run(MONDAY + timedelta(days=2))]}),
        provisional=True,
    )
    for _ in range(2):
        runtime.plans.propose(
            scope, goal_id=goal, content=adapted, generator=ENGINE, plan_id=first.plan_id, parent=1
        )
    listed = ok(cli, "plan", "list")
    assert "revision 4  active version 1" in listed
    assert "  version 3  PROPOSED" in listed
    assert ok(cli, "plan", "activate", plan_id, "2", "--revision", "4").endswith(
        "version 2 is ACTIVE\n"
    )
    code, output = cli.run("plan", "activate", plan_id, "3", "--revision", "4")
    assert (code, output) == (
        2,
        "error: the plan changed since you reviewed it; run plan list again\n",
    )
    assert ok(cli, "plan", "reject", plan_id, "3").endswith("version 3 is SUPERSEDED\n")
    code, output = cli.run("plan", "reject", plan_id, "2")  # the ACTIVE one stays
    assert code == 2
    assert "superseded only by activating another" in output
    shown = ok(cli, "plan", "show", plan_id, "--version", "1")
    assert f"plan {plan_id} version 1: SUPERSEDED" in shown
    assert "version 2 (from version 1): ACTIVE" in ok(cli, "plan", "show")
    missing = cli.run("plan", "activate", plan_id, "9", "--revision", "5")[1]
    assert missing == "error: plan, version, workout or activity not found\n"


@pytest.mark.req("SAFE-MED-003", "FR-POL-001")
@pytest.mark.acceptance("AT-SAFE-001")
def test_activation_is_denied_with_reasons_while_a_hold_is_active(tmp_path: Path) -> None:
    cli, runtime, scope, goal = onboarded(tmp_path)
    first = runtime.plans.propose(
        scope, goal_id=goal, content=content(provisional=True), generator=ENGINE
    )
    cli.run("checkin", "add", "--notes", "me duele el pecho desde ayer")
    code, output = cli.run("plan", "activate", str(first.plan_id), "1", "--revision", "1")
    assert (code, output) == (
        2,
        "error: denied by policy: SAFETY_HOLD_ACTIVE; SAFETY_HOLD_ACTIVE (2026-10-27); "
        "SAFETY_HOLD_ACTIVE (2026-10-29)\n",
    )
    ok(cli, "safety", "release", "--cleared-by-professional")
    activated = ok(cli, "plan", "activate", str(first.plan_id), "1", "--revision", "1")
    assert activated.endswith("is ACTIVE\n")


@pytest.mark.req("FR-ONB-003", "FR-ONB-005", "FR-PLN-002", "FR-STR-001")
@pytest.mark.acceptance("AT-ONB-002", "AT-ONB-004")
def test_plan_generate_follows_readiness_and_the_baseline_block(tmp_path: Path) -> None:
    cli = Cli(tmp_path / "coach-data")
    assert cli.run("init")[0] == 0
    ok(cli, "profile", "timezone", "America/Argentina/Buenos_Aires")
    code, output = cli.run("plan", "generate")
    assert code == 2
    assert output.startswith("error: not ready for a plan; missing: ")
    assert "weekly availability" in output
    assert ok(cli, "plan", "list") == "no plans yet\n"  # no version created (AT-ONB-002)

    cli, _runtime, _scope, goal = onboarded(tmp_path / "second")
    ok(cli, "consent", "decline", "ai-processing")
    assert "coaching state: BASELINE_COLLECTION" in ok(cli, "readiness")  # no history yet
    shown = ok(cli, "plan", "generate")
    first = shown.splitlines()[0]
    plan_id = first.split()[1]
    assert first == f"plan {plan_id} version 1: PROPOSED"
    assert "PROVISIONAL: built on assumptions" in shown
    assert "  assumption: Baseline collection: few recent running activities" in shown
    assert shown.count("EVALUATION RUNNING 30 min up to RPE 5 [key]: 10 minutes easy, then 20") == 2
    assert "STRENGTH STRENGTH 45 min" in shown
    assert f"review it, then: coach plan activate {plan_id} 1 --revision 1" in shown
    ok(cli, "plan", "activate", plan_id, "1", "--revision", "1")
    second = ok(cli, "plan", "generate").splitlines()[0]
    assert second.endswith("version 2 (from version 1): PROPOSED")
    third = ok(cli, "plan", "generate").splitlines()[0]
    assert third.endswith("version 3 (from version 1): PROPOSED")
    listed = ok(cli, "plan", "list")
    assert f"{plan_id}  goal {goal}  revision 4  active version 1" in listed
    assert "  version 2  SUPERSEDED" in listed  # replaced by the newer proposal


@pytest.mark.req("FR-PLN-007", "FR-ACT-011")
def test_executions_show_planned_versus_done_and_links_change_them(tmp_path: Path) -> None:
    cli, _runtime, _scope, _goal = onboarded(tmp_path)
    ok(cli, "consent", "grant", "activities")
    plan_id = ok(cli, "plan", "generate").splitlines()[0].split()[1]
    ok(cli, "plan", "activate", plan_id, "1", "--revision", "1")
    run = tmp_path / "run.fit"
    wednesday = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)  # 18:00 in Buenos Aires
    run.write_bytes(fit_file([fit_session(start=wednesday, seconds=2400)]))
    ok(cli, "import", str(run))
    cli.now = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
    shown = ok(cli, "plan", "executions")
    assert shown.startswith("week of 2026-10-26: ")
    activity_id = next(
        line.split()[1] for line in shown.splitlines() if line.startswith("    activity ")
    )
    assert (
        ok(cli, "plan", "link", activity_id, "none")
        == f"activity {activity_id} linked: unplanned\n"
    )
    shown = ok(cli, "plan", "executions")
    assert "2026-10-28 Wed  UNPLANNED  an activity not in the plan (linked by you)" in shown
    lines = shown.splitlines()
    index = next(number for number, line in enumerate(lines) if "ENDURANCE RUNNING" in line)
    workout_id = lines[index + 1].split()[1]
    ok(cli, "plan", "link", activity_id, workout_id)
    linked = next(
        line for line in ok(cli, "plan", "executions").splitlines() if "linked by you" in line
    )
    assert "ENDURANCE RUNNING" in linked
    assert "duration " in linked  # 40 minutes against the planned duration
    code, output = cli.run("plan", "link", "00000000-0000-0000-0000-000000000999", workout_id)
    assert (code, output) == (2, "error: plan, version, workout or activity not found\n")
    ok(cli, "consent", "revoke", "activities")
    hint = "error: grant the ACTIVITIES consent first: coach consent grant activities\n"
    for command in (("executions",), ("link", activity_id, "none"), ("adapt",)):
        assert cli.run("plan", *command) == (2, hint), command  # activities are not read


@pytest.mark.req("FR-ADP-001", "FR-ADP-004")
def test_plan_adapt_records_and_shows_the_decision(tmp_path: Path) -> None:
    cli, _runtime, _scope, _goal = onboarded(tmp_path)
    code, output = cli.run("plan", "adapt")
    assert (code, output) == (2, "error: there is no ACTIVE plan to adapt\n")
    assert ok(cli, "plan", "decisions") == "no decisions yet\n"
    ok(cli, "consent", "grant", "activities")
    plan_id = ok(cli, "plan", "generate").splitlines()[0].split()[1]
    ok(cli, "plan", "activate", plan_id, "1", "--revision", "1")
    cli.now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    shown = ok(cli, "plan", "adapt", "--trigger", "weekly-review")
    assert shown.startswith("2026-10-29  MAINTAIN (NO_SIGNAL; confidence MEDIUM)\n")
    assert "the plan stays" in shown
    assert "proposed:" not in shown  # MAINTAIN proposes no version
    assert ok(cli, "plan", "decisions") == shown


@pytest.mark.req("FR-RPT-001", "FR-RPT-002", "FR-RPT-003", "FR-PLN-007", "FR-GOL-005")
def test_reports_show_planned_versus_done_adherence_and_goal_progress(tmp_path: Path) -> None:
    cli, _runtime, _scope, _goal = onboarded(tmp_path)
    ok(cli, "consent", "grant", "activities")
    plan_id = ok(cli, "plan", "generate").splitlines()[0].split()[1]
    ok(cli, "plan", "activate", plan_id, "1", "--revision", "1")
    run = tmp_path / "run.fit"
    tuesday = datetime(2026, 10, 27, 21, 0, tzinfo=UTC)  # the planned 35-minute easy run
    run.write_bytes(fit_file([fit_session(start=tuesday, seconds=1500)]))
    ok(cli, "import", str(run))
    cli.now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    executions = ok(cli, "plan", "executions", "--week", "2026-10-28")
    activity = next(
        line.split()[1] for line in executions.splitlines() if line.startswith("    activity ")
    )
    single = ok(cli, "report", "activity", activity).splitlines()
    planned = single.index("Planned vs Actual")
    assert single[planned + 1 : planned + 4] == [
        "  status: PARTIAL",
        "  planned: ENDURANCE: 35 minutes easy (RPE 3 to 4)",
        "  duration done of planned: 71.4 %",  # 25 of 35 minutes
    ]
    assert "  duration done of planned outside 80 % to 120 %: 71.4 %" in single
    week = ok(cli, "report", "week", "--week", "2026-W44").splitlines()
    adherence = week.index("Adherence")
    assert week[adherence + 1 : adherence + 8] == [
        "  planned workouts due: 3",
        "  complete: 0",
        "  partial: 1",
        "  skipped: 2",
        "  replaced: 0",
        "  unplanned activities: 0",
        "  done as planned: 0.0 %",
    ]
    progress = week.index("Progression toward the A goal")
    assert week[progress + 1] == "  classification: INSUFFICIENT_DATA"
    assert "  current estimate for the goal distance: 0:50:00" in week  # the declared 10k race
    assert "  reason: NO_TARGET_TIME" in week
    assert "Cautious forecast: not available (NEEDS_GOAL_METRICS)" in ok(cli, "report", "month")
    timed, *_ = onboarded(tmp_path / "timed", "--target-time", "48:00")
    forecast = ok(timed, "report", "month").splitlines()
    start = forecast.index("Cautious forecast")
    assert forecast[start + 3] == "  classification: LIKELY"  # 2 min in 20 weeks: 0.2 % a week
    assert forecast[start + 1].startswith("  realistic goal: ")
    assert forecast[start + 2] == "  minimum satisfactory goal: 0:50:00"
    assert "never a guarantee of the outcome" in forecast[start + 4]
