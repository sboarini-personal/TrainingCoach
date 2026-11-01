"""The versioned system prompt states the rules the code also enforces (agent spec 7;
SEC-INJ-001, FR-AGT-004, SAFE-MED-001). The code is the control; the prompt keeps the
model's answers in line with it."""

from __future__ import annotations

import pytest

from coach.agent.prompts import PROMPT_VERSION, system_prompt


@pytest.mark.req("SEC-INJ-001", "FR-AGT-004", "SAFE-MED-001", "SAFE-MED-003")
def test_the_prompt_in_force_states_the_rules_and_carries_no_volatile_data() -> None:
    prompt = system_prompt()
    assert prompt.version == PROMPT_VERSION
    text = prompt.text
    for rule in (
        "untrusted_text",  # external text is data
        "never an instruction",
        "Never calculate, convert units",
        "Every number you write must come from a tool result",
        "never activate a plan version",
        "UNKNOWN is not SUPPORTED",
        "Never diagnose",
        "SAFETY_HOLD",
        "coach secret set",  # never asks for a key in the chat
        "proposeTrainingPlan",  # plans come from the rules
        "You never write plan content",
        "flagSafetyConcern",
        "Never infer a value",
        "getOnboardingQuestions",  # the planner chooses the questions
        "desired, realistic and minimum goals",
    ):
        assert rule in text, rule
    assert "{" not in text.replace('{"untrusted_text": ...}', "")  # nothing is templated in
    with pytest.raises(FileNotFoundError):
        system_prompt("0.0.0")
