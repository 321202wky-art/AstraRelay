"""Backend-only payment provider contracts and local simulation."""

from abc import ABC, abstractmethod


class PaymentProvider(ABC):
    @abstractmethod
    def create_order(self, order):
        """Return provider-facing payment instructions for an order."""

    @abstractmethod
    def handle_callback(self, payload):
        """Validate a provider callback and return its canonical result."""

    @abstractmethod
    def refund(self, order):
        """Return the provider result for a refund request."""


class MockWechatPayProvider(PaymentProvider):
    """Local-only WeChat Pay simulation; it never contacts WeChat."""

    provider_name = "wechat_mock"

    def create_order(self, order):
        order_no = order["order_no"]
        return {
            "provider": self.provider_name,
            "order_no": order_no,
            "payment_url": f"mock-wechat://pay/{order_no}",
        }

    def handle_callback(self, payload):
        order_no = payload.get("order_no") if isinstance(payload, dict) else None
        if not isinstance(order_no, str) or not order_no:
            raise ValueError("模拟支付回调缺少订单号")
        return {
            "order_no": order_no,
            "provider_transaction_id": f"mock-{order_no}",
            "paid": True,
        }

    def refund(self, order):
        order_no = order["order_no"]
        return {
            "order_no": order_no,
            "refunded": True,
            "provider_refund_id": f"mock-refund-{order_no}",
        }
