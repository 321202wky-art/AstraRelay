import pytest

from payment_service import PaymentService
from payments.providers import MockWechatPayProvider


class FakePayments:
    def __init__(self):
        self.orders = {}
        self.credit_calls = 0

    def create(self, **order):
        order["status"] = "pending"
        self.orders[order["order_no"]] = order
        return dict(order)

    def complete_paid_order(self, order_no, provider_transaction_id):
        order = self.orders[order_no]
        if order["status"] == "pending":
            order["status"] = "paid"
            order["provider_transaction_id"] = provider_transaction_id
            self.credit_calls += 1
        return dict(order)

    def refund_paid_order(self, order_no):
        order = self.orders[order_no]
        if order["status"] == "paid":
            order["status"] = "refunded"
        return dict(order)

    def get(self, order_no):
        return dict(self.orders[order_no])


def test_duplicate_callback_credits_wallet_once():
    repository = FakePayments()
    service = PaymentService(repository, MockWechatPayProvider())
    created = service.create_recharge(7, 100, "CN", "CNY")
    payload = {"order_no": created["order"]["order_no"]}

    assert service.handle_mock_callback(payload)["status"] == "paid"
    assert service.handle_mock_callback(payload)["status"] == "paid"
    assert repository.credit_calls == 1


def test_callback_cannot_credit_an_order_from_another_provider():
    repository = FakePayments()
    repository.orders["foreign-order"] = {
        "order_no": "foreign-order",
        "provider": "another_provider",
        "status": "pending",
    }
    service = PaymentService(repository, MockWechatPayProvider())

    with pytest.raises(ValueError, match="provider"):
        service.handle_mock_callback({"order_no": "foreign-order"})
    assert repository.credit_calls == 0


def test_region_currency_pair_is_validated():
    service = PaymentService(FakePayments(), MockWechatPayProvider())
    with pytest.raises(ValueError):
        service.create_recharge(7, 100, "CN", "USD")


def test_refund_changes_paid_order_once():
    repository = FakePayments()
    service = PaymentService(repository, MockWechatPayProvider())
    created = service.create_recharge(7, 100, "GLOBAL", "USD")
    order_no = created["order"]["order_no"]
    service.handle_mock_callback({"order_no": order_no})

    assert service.refund(order_no)["status"] == "refunded"
    assert service.refund(order_no)["status"] == "refunded"


def test_refund_rejects_unpaid_orders_and_does_not_repeat_provider_refund():
    class CountingProvider(MockWechatPayProvider):
        def __init__(self):
            self.refund_calls = 0

        def refund(self, order):
            self.refund_calls += 1
            return super().refund(order)

    repository = FakePayments()
    provider = CountingProvider()
    service = PaymentService(repository, provider)
    created = service.create_recharge(7, 100, "CN", "CNY")
    order_no = created["order"]["order_no"]

    with pytest.raises(ValueError, match="paid"):
        service.refund(order_no)
    service.handle_mock_callback({"order_no": order_no})
    service.refund(order_no)
    service.refund(order_no)

    assert provider.refund_calls == 1
