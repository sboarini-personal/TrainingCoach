"""The AI coach's tools (tools contract; SEC-AUTHZ-002). The set is fixed in code: data and
prompts can never add a tool or widen one."""

from __future__ import annotations

from coach.agent.services import AgentServices
from coach.agent.tools.athlete import athlete_tools
from coach.agent.tools.base import Tool
from coach.agent.tools.plans import plan_tools
from coach.agent.tools.recovery import recovery_tools
from coach.agent.tools.training import training_tools


def read_tools(services: AgentServices) -> list[Tool]:
    """The READ tools (P13.2)."""
    return [
        *athlete_tools(services),
        *training_tools(services),
        *recovery_tools(services),
        *plan_tools(services),
    ]
