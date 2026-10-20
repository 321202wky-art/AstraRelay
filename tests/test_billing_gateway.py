from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from collections import Counter

import pytest

from billing_gateway import BillingGateway


class Wallet:
    def __init__(self):
        self.calls = []

    def ensure_wallet(self, username):
        self.calls.append(("ensure", username))
        return {"balance_points": 500, "frozen_points": 0}

    def freeze_points(self, username, request_id, points):
        self.calls.append(("freeze", username, request_id, points))
        return True

    def settle_points(self, username, request_id, points):
        self.calls.append(("settle", username, request_id, points))
        return {"balance_points": 499, "frozen_points": 0}

    def release_points(self, username, request_id):
        self.calls.append(("release", username, request_id))
        return {"balance_points": 500, "frozen_points": 0}


def gateway_config(**overrides):
    values = {
        "EXCHANGE_RATE": 100,
        "TOKEN_CHARS_PER_TOKEN": 4,
        "MAX_OUTPUT_TOKENS": 1,
        "GATEWAY_COMPAT_API_KEYS": ("key-one", "key-two"),
        "MODEL_PRICES_MICRO_POINTS": {"deepseek": 101},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_key_pool_rotates_in_declaration_order():
    gateway = BillingGateway(Wallet(), config_module=gateway_config())

    assert [gateway.next_api_key() for _ in range(4)] == [
        "key-one", "key-two", "key-one", "key-two"
    ]


def test_key_pool_rotation_remains_balanced_across_concurrent_calls():
    gateway = BillingGateway(Wallet(), config_module=gateway_config())

    with ThreadPoolExecutor(max_workers=16) as executor:
        keys = list(executor.map(lambda _: gateway.next_api_key(), range(200)))

    assert Counter(keys) == Counter({"key-one": 100, "key-two": 100})


def test_key_pool_uses_the_configured_provider_pool_for_the_model():
    gateway = BillingGateway(
        Wallet(),
        config_module=gateway_config(
            GATEWAY_SECONDARY_API_KEYS=("gateway_secondary-key",),
            MODEL_METADATA={"qwen_official": {"api_key_setting": "GATEWAY_SECONDARY_API_KEY"}},
        ),
    )

    assert gateway.next_api_key("qwen_official") == "gateway_secondary-key"


def test_key_pool_uses_the_dynamic_model_provider_prefix():
    gateway = BillingGateway(
        Wallet(),
        config_module=gateway_config(
            GATEWAY_SECONDARY_API_KEYS=("gateway_secondary-key",),
            MODEL_PROVIDER_CONNECTIONS={
                "gateway_secondary": {"api_key_setting": "GATEWAY_SECONDARY_API_KEY"}
            },
        ),
    )

    assert gateway.next_api_key("gateway_secondary:qwen-max") == "gateway_secondary-key"


def test_missing_model_price_is_rejected_without_wallet_mutation():
    wallet = Wallet()
    gateway = BillingGateway(wallet, config_module=gateway_config(MODEL_PRICES_MICRO_POINTS={}))

    with pytest.raises(ValueError, match="price"):
        gateway.complete("alice", "deepseek", [], lambda key: "unused")

    assert wallet.calls == []


def test_estimate_reserves_input_tokens_plus_the_configured_output_limit():
    gateway = BillingGateway(
        Wallet(),
        config_module=gateway_config(MAX_OUTPUT_TOKENS=10, MODEL_PRICES_MICRO_POINTS={"deepseek": 100}),
    )

    assert gateway.estimate_points("deepseek", [{"role": "user", "content": "abcd"}]) == 11


def test_complete_freezes_then_calls_and_settles_ceiling_integer_micro_point_price():
    wallet = Wallet()
    received_keys = []
    gateway = BillingGateway(wallet, config_module=gateway_config())

    assert gateway.complete("alice", "deepseek", [], lambda key: received_keys.append(key) or "answer") == "answer"

    assert received_keys == ["key-one"]
    assert wallet.calls[0] == ("ensure", "alice")
    assert wallet.calls[1][0:2] == ("freeze", "alice")
    assert wallet.calls[1][3] == 2
    assert wallet.calls[2][0:2] == ("settle", "alice")
    assert wallet.calls[2][3] == 2


def test_complete_settles_actual_input_and_plain_text_output_without_exceeding_freeze():
    wallet = Wallet()
    gateway = BillingGateway(
        wallet,
        config_module=gateway_config(MAX_OUTPUT_TOKENS=10, MODEL_PRICES_MICRO_POINTS={"deepseek": 100}),
    )
    messages = [{"role": "user", "content": "abcd"}]

    assert gateway.complete("alice", "deepseek", messages, lambda key: "12345678") == "12345678"

    assert wallet.calls[1][3] == 11
    assert wallet.calls[2][3] == 3


def test_complete_releases_the_same_request_freeze_when_provider_call_raises():
    wallet = Wallet()
    gateway = BillingGateway(wallet, config_module=gateway_config())

    with pytest.raises(ConnectionError, match="offline"):
        gateway.complete("alice", "deepseek", [], lambda key: (_ for _ in ()).throw(ConnectionError("offline")))

    freeze_request_id = wallet.calls[1][2]
    assert wallet.calls[2] == ("release", "alice", freeze_request_id)


def test_complete_releases_the_same_request_freeze_when_settlement_raises():
    class FailingSettlementWallet(Wallet):
        def settle_points(self, username, request_id, points):
            self.calls.append(("settle", username, request_id, points))
            raise RuntimeError("ledger unavailable")

    wallet = FailingSettlementWallet()
    gateway = BillingGateway(wallet, config_module=gateway_config())

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        gateway.complete("alice", "deepseek", [], lambda key: "answer")

    freeze_request_id = wallet.calls[1][2]
    assert wallet.calls[3] == ("release", "alice", freeze_request_id)


def test_stream_settles_actual_text_after_all_chunks_are_yielded():
    wallet = Wallet()
    gateway = BillingGateway(
        wallet,
        config_module=gateway_config(MAX_OUTPUT_TOKENS=10, MODEL_PRICES_MICRO_POINTS={"deepseek": 100}),
    )
    messages = [{"role": "user", "content": "abcd"}]

    assert list(gateway.stream("alice", "deepseek", messages, lambda key: iter(["1234", "5678"]))) == ["1234", "5678"]

    assert wallet.calls[1][3] == 11
    assert wallet.calls[2][3] == 3


def test_stream_releases_frozen_points_when_iteration_raises_or_is_closed():
    wallet = Wallet()
    gateway = BillingGateway(wallet, config_module=gateway_config())

    def failing_stream(key):
        yield "first"
        raise ConnectionError("offline")

    result = gateway.stream("alice", "deepseek", [], failing_stream)
    assert next(result) == "first"
    with pytest.raises(ConnectionError, match="offline"):
        next(result)
    failed_request_id = wallet.calls[1][2]
    assert wallet.calls[2] == ("release", "alice", failed_request_id)

    result = gateway.stream("alice", "deepseek", [], lambda key: iter(["first", "second"]))
    assert next(result) == "first"
    closed_request_id = wallet.calls[4][2]
    result.close()
    assert wallet.calls[5] == ("release", "alice", closed_request_id)
