import pytest

from payments.providers import MockWechatPayProvider


def test_mock_wechat_provider_creates_success_callback_and_refund():
    provider = MockWechatPayProvider()
    order = {"order_no": "R202607180001"}

    assert provider.create_order(order)["payment_url"].endswith(order["order_no"])
    assert provider.handle_callback({"order_no": order["order_no"]})["paid"] is True
    assert provider.refund(order)["refunded"] is True


def test_mock_wechat_callback_rejects_missing_order_number():
    with pytest.raises(ValueError):
        MockWechatPayProvider().handle_callback({})
