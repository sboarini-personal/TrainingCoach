"""A prompt or the tool set never changes under the same version (agent spec 7 and 9;
FR-AGT-003): conversations, decisions and AI-written texts record the versions they used, so
each version must name exactly one content. Changing a prompt or a tool means a new version
(a new prompt file, a new TOOLS_VERSION) and a new line here."""

from __future__ import annotations

import hashlib
import json
from typing import cast

import pytest

from coach.agent.prompts import NARRATIVE_VERSION, PROMPT_VERSION, narrative_prompt, system_prompt
from coach.agent.services import AgentServices
from coach.agent.tools import coach_tools
from coach.agent.tools.base import TOOLS_VERSION

# version -> SHA-256 of the content released under it (P13, 2026-11-01)
SYS = "89550685a967cdf6ac1cf3f0e5890a73ca21aab9f357af5473d096429056ddc7"  # pragma: allowlist secret
SYSTEM = {"1.0.0": SYS}
NAR = "ecb07c332865934165e6163406d5ebc4073dcf04424bf4ae3ff7852986858eba"  # pragma: allowlist secret
NARRATIVE = {"1.0.0": NAR}
TLS = "20d2039221a604cd20430d4cb675e0e8404c5a2f58e4f8986a7745f0376e9978"  # pragma: allowlist secret
TOOLS = {"1.0.0": TLS}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.req("FR-AGT-003")
def test_each_version_names_one_content() -> None:
    assert _sha(system_prompt(PROMPT_VERSION).text) == SYSTEM[PROMPT_VERSION]
    assert _sha(narrative_prompt(NARRATIVE_VERSION).text) == NARRATIVE[NARRATIVE_VERSION]
    tools = [
        {
            "name": t.name,
            "kind": t.kind.value,
            "description": t.description,
            "schema": t.input_schema,
        }
        for t in coach_tools(cast(AgentServices, object()))
    ]
    assert _sha(json.dumps(tools, sort_keys=True)) == TOOLS[TOOLS_VERSION]
