from app import create_app
from payment_service import PaymentService
from payments.providers import MockWechatPayProvider
from user_database import UserDatabase


class NoopDatabase:
    pass


class NoopAdapter:
    def health_check(self, model):
        return False


def authenticated_client(app, username="payer"):
    users = app.extensions["user_database"]
    account = users._testing_store["users"][username]
    account.update(
        username=username,
        email=f"{username}@example.test",
        email_verified_at=True,
        session_version=0,
        phone_verified_at=None,
        wechat_bound_at=None,
    )
    client = app.test_client()
    with client.session_transaction() as current:
        current["username"] = username
        current["email"] = account["email"]
        current["session_version"] = 0
        current["csrf_token"] = "csrf-test"
    return client


def test_recharge_create_callback_status_and_refund_are_idempotent(monkeypatch):
    users = UserDatabase.for_testing()
    users.initialize()
    users.create_user("payer", "safe-password")
    users.ensure_wallet("payer")
    service = PaymentService(users, MockWechatPayProvider())
    monkeypatch.setattr("app.config.MOCK_PAYMENT_CALLBACK_TOKEN", "mock-secret", raising=False)
    monkeypatch.setattr("app.config.ENABLE_MOCK_PAYMENTS", True, raising=False)
    application = create_app(database=NoopDatabase(), adapter=NoopAdapter(), user_database=users, payment_service=service)
    client = authenticated_client(application)

    created = client.post("/api/recharge/orders", json={"amount_minor": 100, "region": "CN", "currency": "CNY"}, headers={"X-CSRF-Token": "csrf-test"})
    assert created.status_code == 201
    order_no = created.get_json()["order"]["order_no"]

    for _ in range(2):
        callback = application.test_client().post("/api/payments/wechat/mock/callback", json={"order_no": order_no}, headers={"X-Mock-Payment-Token": "mock-secret"})
        assert callback.status_code == 200
    assert users.ensure_wallet("payer")["balance_points"] == 1500

    status = client.get(f"/api/recharge/orders/{order_no}")
    assert status.get_json()["status"] == "paid"
    refunded = client.post(f"/api/recharge/orders/{order_no}/refund", headers={"X-CSRF-Token": "csrf-test"})
    assert refunded.get_json()["status"] == "refunded"


def test_recharge_routes_require_authentication_csrf_ownership_and_callback_token(monkeypatch):
    users = UserDatabase.for_testing()
    users.initialize()
    users.create_user("owner", "safe-password")
    users.create_user("intruder", "safe-password")
    service = PaymentService(users, MockWechatPayProvider())
    monkeypatch.setattr("app.config.MOCK_PAYMENT_CALLBACK_TOKEN", "mock-secret", raising=False)
    monkeypatch.setattr("app.config.ENABLE_MOCK_PAYMENTS", True, raising=False)
    application = create_app(
        database=NoopDatabase(),
        adapter=NoopAdapter(),
        user_database=users,
        payment_service=service,
    )

    payload = {"amount_minor": 100, "region": "CN", "currency": "CNY"}
    assert application.test_client().post("/api/recharge/orders", json=payload).status_code == 401
    owner = authenticated_client(application, "owner")
    assert owner.post("/api/recharge/orders", json=payload).status_code == 400
    created = owner.post(
        "/api/recharge/orders",
        json=payload,
        headers={"X-CSRF-Token": "csrf-test"},
    )
    order_no = created.get_json()["order"]["order_no"]

    intruder = authenticated_client(application, "intruder")
    assert intruder.get(f"/api/recharge/orders/{order_no}").status_code == 404
    assert intruder.post(
        f"/api/recharge/orders/{order_no}/refund",
        headers={"X-CSRF-Token": "csrf-test"},
    ).status_code == 404
    assert application.test_client().post(
        "/api/payments/wechat/mock/callback", json={"order_no": order_no}
    ).status_code == 403


def test_mock_payment_endpoints_are_disabled_unless_explicitly_enabled(monkeypatch):
    users = UserDatabase.for_testing()
    users.initialize()
    users.create_user("payer", "safe-password")
    monkeypatch.setattr("app.config.ENABLE_MOCK_PAYMENTS", False, raising=False)
    application = create_app(
        database=NoopDatabase(),
        adapter=NoopAdapter(),
        user_database=users,
        payment_service=PaymentService(users, MockWechatPayProvider()),
    )
    client = authenticated_client(application)

    response = client.post(
        "/api/recharge/orders",
        json={"amount_minor": 100, "region": "CN", "currency": "CNY"},
        headers={"X-CSRF-Token": "csrf-test"},
    )

    assert response.status_code == 503
