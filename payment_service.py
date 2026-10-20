"""Idempotent recharge orchestration for payment-provider adapters."""

from uuid import uuid4


EXCHANGE_RATE_CN = 10
EXCHANGE_RATE_GLOBAL = 150


class PaymentService:
    def __init__(self, repository, provider):
        self.repository = repository
        self.provider = provider

    def create_recharge(self, user_id, amount_minor, region, currency):
        if type(amount_minor) is not int or amount_minor <= 0:
            raise ValueError("充值金额必须为正整数最小货币单位")
        valid_pairs = {("CN", "CNY"), ("GLOBAL", "USD")}
        if (region, currency) not in valid_pairs:
            raise ValueError("区域与币种不匹配")
        rate = EXCHANGE_RATE_CN if region == "CN" else EXCHANGE_RATE_GLOBAL
        order = self.repository.create(
            order_no=uuid4().hex,
            user_id=user_id,
            provider=self.provider.provider_name,
            amount_minor=amount_minor,
            region=region,
            currency=currency,
            points=amount_minor * rate,
        )
        return {"order": order, "payment": self.provider.create_order(order)}

    def handle_mock_callback(self, payload):
        result = self.provider.handle_callback(payload)
        order = self.repository.get(result["order_no"])
        if order.get("provider") != self.provider.provider_name:
            raise ValueError("payment provider does not match the order")
        return self.repository.complete_paid_order(
            result["order_no"], result["provider_transaction_id"]
        )

    def get_order(self, order_no):
        return self.repository.get(order_no)

    def refund(self, order_no):
        order = self.repository.get(order_no)
        if order.get("provider") != self.provider.provider_name:
            raise ValueError("payment provider does not match the order")
        if order.get("status") == "refunded":
            return order
        if order.get("status") != "paid":
            raise ValueError("only paid orders can be refunded")
        self.provider.refund(order)
        return self.repository.refund_paid_order(order_no)
