"""The AI coach's tools (tools contract; SEC-AUTHZ-002). The set is fixed in code: data and
prompts can never add a tool or widen one."""

from __future__ import annotations

from coach.agent.services import AgentServices
from coach.agent.tools.actions import action_tools
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


def coach_tools(services: AgentServices) -> list[Tool]:
    """Every tool of a coaching session: the READ tools, then the tools that change
    something (P13.4). The session grants READ, WRITE_INTERNAL and PROPOSE; EXECUTE_EXTERNAL
    tools always stop before any effect (SEC-AUTHZ-002)."""
    return [*read_tools(services), *action_tools(services)]
