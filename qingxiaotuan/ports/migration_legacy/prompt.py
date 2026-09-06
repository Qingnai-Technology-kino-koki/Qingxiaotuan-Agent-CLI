"""First-launch migration prompt: pure decision mapping.

Owns the *decision tree* a user walks through when kimi-code detects a legacy
``~/.kimi/`` install on first launch. Decoupled from any rendering: the host
renders the two logical choices and feeds them to
:func:`resolve_migration_scope`, which maps them into a decision + scope.

Two-layer prompt:
  Prompt 1: now | later | never
  Prompt 2 (only if "now"): config-only | all-sessions
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

Prompt1Choice = Literal["now", "later", "never"]
Prompt2Choice = Literal["config-only", "all-sessions"]
AnyChoice = Union[Prompt1Choice, Prompt2Choice]


@dataclass
class MigrationScope:
    config: bool
    mcp: bool
    user_history: bool
    skills: bool
    sessions: bool


@dataclass
class MigrationPromptResult:
    decision: Literal["now", "later", "never"]
    scope: MigrationScope | None = None


# Mirror of the TS MigrationScope shape used in report/summary modules.
def resolve_migration_scope(choices: list[AnyChoice]) -> MigrationPromptResult:
    """Map the user's prompt choices into a migration decision + scope.

    Pure; production logic (not a simulation).
    """
    c1 = choices[0] if choices else None
    c2 = choices[1] if len(choices) > 1 else None
    if c1 == "later":
        return MigrationPromptResult(decision="later")
    if c1 == "never":
        return MigrationPromptResult(decision="never")
    # c1 == "now" (or absent -> default to migrating everything)
    return MigrationPromptResult(
        decision="now",
        scope=MigrationScope(
            config=True,
            mcp=True,
            user_history=True,
            skills=True,
            sessions=c2 == "all-sessions",
        ),
    )
