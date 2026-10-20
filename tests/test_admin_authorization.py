from admin_authorization import (
    ACCOUNTS_MANAGE,
    ACCOUNTS_READ,
    MODEL_CATALOG_REFRESH,
    AdminAuthorizationService,
)


class Principals:
    def __init__(self, records):
        self.records = records

    def get_admin_principal(self, username):
        record = self.records.get(username)
        return None if record is None else dict(record)


def test_owner_receives_every_declared_capability():
    service = AdminAuthorizationService(
        Principals(
            {
                "owner": {
                    "username": "owner",
                    "access_tier": "owner",
                    "policy_version": 1,
                }
            }
        )
    )

    assert service.allows("owner", ACCOUNTS_READ) is True
    assert service.allows("owner", ACCOUNTS_MANAGE) is True
    assert service.allows("owner", MODEL_CATALOG_REFRESH) is True


def test_restricted_admin_and_missing_principal_receive_no_operational_capability():
    service = AdminAuthorizationService(
        Principals(
            {
                "operator": {
                    "username": "operator",
                    "access_tier": "admin",
                    "policy_version": 1,
                }
            }
        )
    )

    assert service.allows("operator", ACCOUNTS_READ) is False
    assert service.allows("operator", MODEL_CATALOG_REFRESH) is False
    assert service.allows("missing", ACCOUNTS_READ) is False


def test_unknown_capability_is_rejected_even_for_owner():
    service = AdminAuthorizationService(
        Principals(
            {
                "owner": {
                    "username": "owner",
                    "access_tier": "owner",
                    "policy_version": 1,
                }
            }
        )
    )

    assert service.allows("owner", "credentials.read") is False
