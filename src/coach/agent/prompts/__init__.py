"""Versioned system prompts (agent spec 7). A prompt is a file in the repository; its semantic
version is recorded on every conversation and decision. A new version is a new file: a stored
conversation stays bound to the prompt it started with (D-15)."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Final

PROMPT_VERSION: Final = "1.0.0"


@dataclass(frozen=True, slots=True)
class Prompt:
    version: str
    text: str


def system_prompt(version: str = PROMPT_VERSION) -> Prompt:
    resource = files(__package__).joinpath(f"system-{version}.md")
    return Prompt(version, resource.read_text(encoding="utf-8").strip())
