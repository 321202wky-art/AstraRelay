"""Future vector-search port; Phase 1 provides no implementation."""

from typing import Protocol, Sequence


class VectorIndex(Protocol):
    def upsert(self, memory_id: str, owner_user_id: int, content_text: str) -> str: ...

    def delete(self, embedding_id: str) -> None: ...

    def search(self, owner_user_id: int, query: str, limit: int) -> Sequence[str]: ...
