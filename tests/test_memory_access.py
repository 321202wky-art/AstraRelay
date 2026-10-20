import pytest

from memory.access import MemoryAccessContext, MemoryCapability


def test_access_context_allows_only_declared_capabilities():
    context = MemoryAccessContext(
        actor_type="user",
        actor_id="account:7",
        owner_user_id=7,
        capabilities=frozenset({MemoryCapability.READ}),
    )

    context.require(MemoryCapability.READ)

    with pytest.raises(PermissionError, match="memory capability denied"):
        context.require(MemoryCapability.DELETE)


@pytest.mark.parametrize("actor_type", ["", " ", None])
def test_access_context_rejects_blank_actor_type(actor_type):
    with pytest.raises(ValueError, match="actor_type"):
        MemoryAccessContext(
            actor_type=actor_type,
            actor_id="account:7",
            owner_user_id=7,
            capabilities=frozenset(),
        )


@pytest.mark.parametrize("owner_user_id", [0, True])
def test_access_context_rejects_invalid_owner_user_id(owner_user_id):
    with pytest.raises(ValueError, match="owner_user_id"):
        MemoryAccessContext(
            actor_type="user",
            actor_id="account:0",
            owner_user_id=owner_user_id,
            capabilities=frozenset(),
        )


def test_access_context_rejects_untyped_capabilities():
    with pytest.raises(ValueError, match="capabilities"):
        MemoryAccessContext(
            actor_type="user",
            actor_id="account:7",
            owner_user_id=7,
            capabilities=frozenset({"memory:read"}),
        )
