"""Stable administrator capability policy for owner and restricted principals."""

ACCOUNTS_READ = "accounts.read"
ACCOUNTS_MANAGE = "accounts.manage"
MODEL_CATALOG_REFRESH = "model_catalog.refresh"

OWNER_CAPABILITIES = frozenset(
    {
        ACCOUNTS_READ,
        ACCOUNTS_MANAGE,
        MODEL_CATALOG_REFRESH,
    }
)
RESTRICTED_ADMIN_CAPABILITIES = frozenset()


class AdminAuthorizationService:
    """Resolve current administrator principals and evaluate declared capabilities."""

    def __init__(self, repository):
        self._repository = repository

    def principal_for(self, username: str) -> dict | None:
        if not isinstance(username, str) or not username:
            return None
        principal = self._repository.get_admin_principal(username)
        if principal is None or principal.get("access_tier") not in {"owner", "admin"}:
            return None
        return dict(principal)

    def allows(self, username: str, capability: str) -> bool:
        if capability not in OWNER_CAPABILITIES:
            return False
        principal = self.principal_for(username)
        if principal is None:
            return False
        capabilities = (
            OWNER_CAPABILITIES
            if principal["access_tier"] == "owner"
            else RESTRICTED_ADMIN_CAPABILITIES
        )
        return capability in capabilities
