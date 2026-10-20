"""Caller identity and capability contracts for the Memory layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet


class MemoryCapability(str, Enum):
    """Capabilities granted to one authenticated Memory caller."""

    READ = "memory:read"
    SUGGEST = "memory:suggest"
    WRITE_CONFIRMED = "memory:write-confirmed"
    DELETE = "memory:delete"


@dataclass(frozen=True)
class MemoryAccessContext:
    """Bind an actor to one user-owned Memory scope."""

    actor_type: str
    actor_id: str
    owner_user_id: int
    capabilities: FrozenSet[MemoryCapability]

    def __post_init__(self) -> None:
        if not isinstance(self.actor_type, str) or not self.actor_type.strip():
            raise ValueError("actor_type is required")
        if not isinstance(self.actor_id, str) or not self.actor_id.strip():
            raise ValueError("actor_id is required")
        if (
            not isinstance(self.owner_user_id, int)
            or isinstance(self.owner_user_id, bool)
            or self.owner_user_id <= 0
        ):
            raise ValueError("owner_user_id must be a positive integer")
        if not isinstance(self.capabilities, frozenset):
            raise ValueError("capabilities must be a frozenset")
        if not all(
            isinstance(capability, MemoryCapability)
            for capability in self.capabilities
        ):
            raise ValueError("capabilities must contain MemoryCapability values")

    def require(self, capability: MemoryCapability) -> None:
        """Reject callers that were not granted the requested capability."""

        if capability not in self.capabilities:
            raise PermissionError("memory capability denied")
