"""Plan versions through the CLI (P12; planning spec 2 and 3). The plans are proposed through
the runtime, as the generator will; the athlete lists, reviews, activates and rejects them.
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

GOAL = UUID(int=0x601)
ENGINE = Generator(kind=GeneratorKind.ENGINE, engine_version="1.0.0")


def ok(cli: Cli, *args: str) -> str:
    code, output = cli.run(*args)
    assert code == 0, output
    return output


def opened(cli: Cli) -> tuple[Runtime, OwnerScope]:
    owner = SqliteIdentityDirectory(SqliteDatabase.open(cli.data_dir / "coach.sqlite3")).owner()
    assert owner is not None
    runtime = build_runtime(ensure_layout(cli.data_dir), cli.credentials, lambda: cli.now)
    return runtime, OwnerScope(owner.athlete_id)


@pytest.mark.req("FR-PLN-002", "FR-PLN-003", "FR-PLN-004")
@pytest.mark.acceptance("AT-PLN-001", "AT-PLN-002")
def test_the_athlete_reviews_activates_and_rejects_plan_versions(tmp_path: Path) -> None:
    cli = Cli(tmp_path / "coach-data")
    assert cli.run("init")[0] == 0
    assert ok(cli, "plan", "list") == "no plans yet\n"
    runtime, scope = opened(cli)
    first = runtime.plans.propose(
        scope, goal_id=GOAL, content=content(provisional=True), generator=ENGINE
    )
    plan_id = str(first.plan_id)
    shown = ok(cli, "plan", "show")
    assert f"plan {plan_id} version 1: PROPOSED" in shown
    assert "PROVISIONAL: built on assumptions" in shown
    assert "  assumption: No recent history" in shown
    assert "2026-10-29 Thu  ENDURANCE RUNNING 60 min up to RPE 8 [key]" in shown
    assert f"content hash {first.content_hash}" in shown
    listed = ok(cli, "plan", "list")
    assert f"{plan_id}  goal {GOAL}  revision 1  active version none" in listed
    assert ok(cli, "plan", "activate", plan_id, "1").endswith("version 1 is ACTIVE\n")

    adapted = content(week(MONDAY, {0: [rest(MONDAY)], 2: [easy_run(MONDAY + timedelta(days=2))]}))
    for _ in range(2):
        runtime.plans.propose(
            scope, goal_id=GOAL, content=adapted, generator=ENGINE, plan_id=first.plan_id, parent=1
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
    shown = ok(cli, "plan", "show", plan_id, "--version", "1")
    assert f"plan {plan_id} version 1: SUPERSEDED" in shown
    assert "version 2 (from version 1): ACTIVE" in ok(cli, "plan", "show")
    assert cli.run("plan", "activate", plan_id, "9")[1] == "error: plan or version not found\n"
