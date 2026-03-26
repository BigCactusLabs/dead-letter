"""Shared conversation segmentation result models."""

from __future__ import annotations

from dataclasses import dataclass, field

from dead_letter.core.types import ConversationZone


@dataclass(slots=True)
class ConversationResult:
    """Normalized output from HTML or plain-text conversation segmentation."""

    zones: list[ConversationZone]
    client_hint: str | None = None
    rules_triggered: list[str] = field(default_factory=list)
    fallback_used: str | None = None

    def __post_init__(self) -> None:
        self.zones = list(self.zones)
        self.rules_triggered = list(self.rules_triggered)
