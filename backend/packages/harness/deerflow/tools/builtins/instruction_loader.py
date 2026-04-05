"""Instruction loader -- deferred system prompt sections loaded at runtime.

When the lead agent runs on a model with limited context, heavy system prompt
sections are registered here as named "instruction packs".  The agent sees a
compact index in its system prompt and calls ``load_instructions("name")`` to
fetch the full text on demand.

Mirrors the deferred-tool pattern in ``tool_search.py``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@dataclass
class InstructionPack:
    """A named, deferred section of the system prompt."""

    name: str
    description: str  # One-line hint shown in the slim prompt index
    content: str  # Full instruction text returned when loaded


class InstructionRegistry:
    """Registry of instruction packs, fetchable by exact name."""

    def __init__(self) -> None:
        self._packs: dict[str, InstructionPack] = {}

    def register(self, pack: InstructionPack) -> None:
        self._packs[pack.name] = pack

    def get(self, name: str) -> InstructionPack | None:
        return self._packs.get(name)

    def get_multiple(self, names: list[str]) -> list[InstructionPack]:
        return [self._packs[n] for n in names if n in self._packs]

    @property
    def packs(self) -> list[InstructionPack]:
        return list(self._packs.values())

    def __len__(self) -> int:
        return len(self._packs)


# ── Singleton ──────────────────────────────────────────────────────────

_registry: InstructionRegistry | None = None


def get_instruction_registry() -> InstructionRegistry | None:
    return _registry


def set_instruction_registry(registry: InstructionRegistry) -> None:
    global _registry
    _registry = registry


def reset_instruction_registry() -> None:
    global _registry
    _registry = None


# ── Tool ───────────────────────────────────────────────────────────────

@tool("load_instructions")
def load_instructions(names: str) -> str:
    """Load instruction packs by name to learn how to handle specific tasks.

    Your system prompt lists available packs in <available-instructions>.
    Call this BEFORE performing tasks that need specialized instructions.

    Args:
        names: Comma-separated pack names, e.g. "citations" or "citations,subagents".
    """
    registry = get_instruction_registry()
    if registry is None:
        return "No instruction packs available."

    name_list = [n.strip() for n in names.split(",") if n.strip()]
    packs = registry.get_multiple(name_list)

    if not packs:
        available = ", ".join(p.name for p in registry.packs)
        return f"No packs found for: {names}. Available: {available}"

    sections = []
    for pack in packs:
        sections.append(f"<instructions:{pack.name}>\n{pack.content}\n</instructions:{pack.name}>")

    return "\n\n".join(sections)
