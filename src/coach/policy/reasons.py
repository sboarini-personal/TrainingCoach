"""A policy reason: the policy, a reason code and its parameters (codes and identifiers only,
never athlete text; policy spec section 1)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyReason:
    policy: str
    code: str
    parameters: tuple[tuple[str, str], ...] = ()

    def parameter(self, name: str) -> str | None:
        return dict(self.parameters).get(name)
