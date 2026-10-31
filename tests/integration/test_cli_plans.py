"""Plan versions through the CLI (P12; planning spec 2 and 3). The plans are proposed through
the runtime, as the generator does; the athlete lists, reviews, activates and rejects them.
Synthetic data only."""

from __future__ import annotations

from datetime import timedelta
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


def onboarded(tmp_path: Path) -> tuple[Cli, Runtime, OwnerScope, UUID]:
    """Every REQUIRED input and an active running goal; no history, so PROVISIONAL."""
    cli = Cli(tmp_path / "coach-data")
    assert cli.run("init")[0] == 0
    for command in ONBOARDING:
        ok(cli, *command)
    added = ok(
        cli, "goal", "add", "--sport", "running", "--date", "2027-03-14", "--distance-km", "10"
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
    assert missing == "error: plan or version not found\n"


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
    assert shown.count("EVALUATION RUNNING 30 min [key]: 10 minutes easy, then 20 minutes") == 2
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
