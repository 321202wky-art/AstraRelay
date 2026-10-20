from user_database import UserDatabase


def test_new_wallet_receives_500_integer_points_and_a_grant_ledger_entry():
    database = UserDatabase.for_testing()
    database.initialize()
    database.create_user("wallet-user", "safe-password")

    wallet = database.ensure_wallet("wallet-user")

    assert wallet == {"balance_points": 500, "frozen_points": 0}
    assert database.wallet_transactions("wallet-user") == [
        {"transaction_type": "grant", "points": 500, "balance_after": 500, "frozen_after": 0}
    ]


def test_registration_creates_the_initial_wallet_and_audit_entry_atomically():
    database = UserDatabase.for_testing()
    database.initialize()

    assert database.create_user("new-user", "safe-password") is True
    assert database.wallet_transactions("new-user") == [
        {"transaction_type": "grant", "points": 500, "balance_after": 500, "frozen_after": 0}
    ]


def test_wallet_freeze_settle_and_release_use_only_integer_points():
    database = UserDatabase.for_testing()
    database.initialize()
    database.create_user("wallet-user", "safe-password")
    database.ensure_wallet("wallet-user")

    assert database.freeze_points("wallet-user", "request-1", 120) is True
    assert database.settle_points("wallet-user", "request-1", 90) == {"balance_points": 410, "frozen_points": 0}
    assert database.freeze_points("wallet-user", "request-2", 20) is True
    assert database.release_points("wallet-user", "request-2") == {"balance_points": 410, "frozen_points": 0}
    assert database.freeze_points("wallet-user", "request-3", 999) is False


def test_independent_reservations_are_idempotent_and_do_not_release_each_other():
    database = UserDatabase.for_testing()
    database.initialize()
    database.create_user("parallel-user", "safe-password")

    assert database.freeze_points("parallel-user", "request-a", 100) is True
    assert database.freeze_points("parallel-user", "request-a", 100) is True
    assert database.freeze_points("parallel-user", "request-b", 80) is True
    assert database.release_points("parallel-user", "request-a") == {
        "balance_points": 500,
        "frozen_points": 80,
    }
    assert database.settle_points("parallel-user", "request-b", 60) == {
        "balance_points": 440,
        "frozen_points": 0,
    }
    assert database.settle_points("parallel-user", "request-b", 60) == {
        "balance_points": 440,
        "frozen_points": 0,
    }


def test_paid_order_credits_once_and_refund_debits_once():
    database = UserDatabase.for_testing()
    database.initialize()
    database.create_user("payer", "safe-password")
    user_id = database.get_user_id("payer")
    database.ensure_wallet("payer")
    database.create(
        order_no="order-1", user_id=user_id, provider="wechat_mock",
        amount_minor=100, region="CN", currency="CNY", points=1000,
    )

    database.complete_paid_order("order-1", "mock-order-1")
    database.complete_paid_order("order-1", "mock-order-1")
    assert database.ensure_wallet("payer")["balance_points"] == 1500

    database.refund_paid_order("order-1")
    database.refund_paid_order("order-1")
    assert database.ensure_wallet("payer")["balance_points"] == 500
    ledger = database.wallet_transactions("payer")
    assert [entry["transaction_type"] for entry in ledger] == ["grant", "recharge", "refund"]
    assert [entry["request_id"] for entry in ledger[1:]] == ["order-1", "order-1"]


def test_refund_cannot_spend_points_reserved_by_an_active_model_call():
    database = UserDatabase.for_testing()
    database.initialize()
    database.create_user("payer", "safe-password")
    user_id = database.get_user_id("payer")
    database.create(
        order_no="order-frozen",
        user_id=user_id,
        provider="wechat_mock",
        amount_minor=100,
        region="CN",
        currency="CNY",
        points=1000,
    )
    database.complete_paid_order("order-frozen", "mock-order-frozen")
    assert database.freeze_points("payer", "active-model-call", 1200) is True

    try:
        database.refund_paid_order("order-frozen")
    except ValueError as error:
        assert "insufficient" in str(error)
    else:
        raise AssertionError("refund spent points reserved by an active model call")

    assert database.ensure_wallet("payer") == {
        "balance_points": 1500,
        "frozen_points": 1200,
    }
    assert database.get("order-frozen")["status"] == "paid"
