import pytest

import ai_adapter
from ai_adapter import AIAdapter


class FakeDatabase:
    def __init__(self, history):
        self.history = history
        self.persistence_calls = 0

    def get_recent_messages(self, limit=8, model="deepseek"):
        assert limit == 8
        return self.history

    def add_message(self, *args, **kwargs):
        self.persistence_calls += 1
        raise AssertionError("AIAdapter must not persist messages")


class FakeCompletions:
    def __init__(self, reply=None, error=None, response=None):
        self.reply = reply
        self.error = error
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        if self.response is not None:
            return self.response
        return type(
            "Response",
            (),
            {"choices": [type("Choice", (), {"message": type("Message", (), {"content": self.reply})()})()]},
        )()


class FakeClient:
    def __init__(self, reply=None, error=None, response=None):
        self.completions = FakeCompletions(reply, error, response)
        self.chat = type("Chat", (), {"completions": self.completions})()


class FalseyClient(FakeClient):
    def __bool__(self):
        return False


class RecordingGateway:
    def __init__(self, reply="gateway reply", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def complete(self, username, model, messages, provider_call):
        self.calls.append((username, model, messages))
        if self.error:
            raise self.error
        return provider_call("rotated-key") if self.reply is None else self.reply


class DynamicRegistry:
    def resolve(self, platform_model_id):
        assert platform_model_id == "gateway_compat:org/model"
        return {
            "model_id": "org/model",
            "base_url": "https://gateway-compat.example.invalid/v1",
            "api_key_setting": "GATEWAY_COMPAT_API_KEY",
        }


class CaseSensitiveDynamicRegistry:
    def resolve(self, platform_model_id):
        assert platform_model_id == "gateway_main:DeepSeek-V3.2"
        return {
            "model_id": "DeepSeek-V3.2",
            "base_url": "https://api.example.test/v1",
            "api_key_setting": "GATEWAY_MAIN_API_KEY",
        }


@pytest.fixture(autouse=True)
def synthetic_gateway_settings(monkeypatch):
    """Never inherit real operator credentials or endpoints in adapter tests."""
    monkeypatch.setattr(ai_adapter.config, "DEFAULT_MODEL_RUNTIME", "legacy")
    monkeypatch.setattr(ai_adapter.config, "USE_LEGACY_API", False)
    for channel in ("COMPAT", "MAIN", "SECONDARY"):
        monkeypatch.setattr(ai_adapter.config, f"GATEWAY_{channel}_API_KEY", "<synthetic-credential>")
        monkeypatch.setattr(ai_adapter.config, f"GATEWAY_{channel}_BASE_URL", f"https://gateway-{channel.lower()}.example.invalid/v1")
    metadata = {key: dict(value) for key, value in ai_adapter.config.MODEL_METADATA.items()}
    for value in metadata.values():
        channel = value["api_key_setting"].removesuffix("_API_KEY")
        value["base_url"] = getattr(ai_adapter.config, f"{channel}_BASE_URL")
    monkeypatch.setattr(ai_adapter.config, "MODEL_METADATA", metadata)


@pytest.fixture
def gateway_main_runtime(monkeypatch):
    monkeypatch.setattr(
        ai_adapter.config,
        "DEFAULT_MODEL_RUNTIME",
        "gateway_main",
        raising=False,
    )


def test_adapter_disables_automatic_retries_for_timeout_bound_requests(monkeypatch):
    captured = {}

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(ai_adapter, "OpenAI", RecordingOpenAI)

    AIAdapter(FakeDatabase([]))

    assert captured["max_retries"] == 0


def test_reply_sends_eight_newest_history_messages_then_current_message(
    gateway_main_runtime,
):
    history = [{"role": "user", "content": str(number)} for number in range(10)]
    database = FakeDatabase(history=history)
    client = FakeClient(reply="new reply")

    assert AIAdapter(database, client=client).reply("new") == "new reply"
    assert client.completions.kwargs == {
        "model": "deepseek-v4-flash",
        "messages": history[-8:] + [{"role": "user", "content": "new"}],
        "max_tokens": ai_adapter.config.MAX_OUTPUT_TOKENS,
        "timeout": 30,
    }
    assert database.persistence_calls == 0


def test_reply_uses_explicit_falsey_client():
    client = FalseyClient(reply="ok")

    assert AIAdapter(FakeDatabase([]), client=client).reply("hello") == "ok"
    assert client.completions.kwargs["messages"] == [{"role": "user", "content": "hello"}]


def test_reply_with_history_routes_kimi_alias_through_gateway_main(
    gateway_main_runtime,
):
    client = FakeClient(reply="kimi reply")
    history = [{"role": "assistant", "content": "private Kimi context"}]

    answer = AIAdapter(FakeDatabase([]), client=client).reply_with_history(
        "hello", history, model="kimi"
    )

    assert answer == "kimi reply"
    assert client.completions.kwargs == {
        "model": "kimi-k2.6",
        "messages": history + [{"role": "user", "content": "hello"}],
        "max_tokens": ai_adapter.config.MAX_OUTPUT_TOKENS,
        "timeout": 30,
    }


def test_reply_with_history_routes_the_legacy_deepseek_alias_through_gateway_main(
    gateway_main_runtime,
):
    client = FakeClient(reply="GatewayMain reply")

    assert AIAdapter(FakeDatabase([]), client=client).reply_with_history(
        "hello", [], model="deepseek"
    ) == "GatewayMain reply"
    assert client.completions.kwargs["model"] == "deepseek-v4-flash"


def test_reply_with_history_resolves_a_dynamic_platform_model_without_changing_its_contract():
    client = FakeClient(reply="dynamic reply")
    adapter = AIAdapter(
        FakeDatabase([]), client=client, model_registry=DynamicRegistry()
    )

    assert adapter.reply_with_history(
        "hello", [], model="gateway_compat:org/model"
    ) == "dynamic reply"
    assert client.completions.kwargs["model"] == "org/model"


def test_dynamic_platform_model_preserves_case_during_client_and_model_resolution(monkeypatch):
    clients = []

    def create_client(**_kwargs):
        client = FakeClient(reply="case-sensitive reply")
        clients.append(client)
        return client

    monkeypatch.setattr(ai_adapter, "OpenAI", create_client)
    adapter = AIAdapter(
        FakeDatabase([]), model_registry=CaseSensitiveDynamicRegistry()
    )

    assert adapter.reply_with_history(
        "hello", [], model="gateway_main:DeepSeek-V3.2"
    ) == "case-sensitive reply"
    assert clients[-1].completions.kwargs["model"] == "DeepSeek-V3.2"


def test_reply_with_history_uses_injected_gateway_and_keeps_plain_text_return():
    gateway = RecordingGateway()
    adapter = AIAdapter(
        FakeDatabase([]), client=FakeClient(reply="provider reply"), billing_gateway=gateway,
    )

    with adapter.billing_user("alice"):
        assert adapter.reply_with_history("hello", [], model="deepseek") == "gateway reply"
    assert gateway.calls == [("alice", "deepseek", [{"role": "user", "content": "hello"}])]


def test_gateway_exception_releases_freeze_before_adapter_surfaces_the_error():
    class ReleasingGateway(RecordingGateway):
        def complete(self, username, model, messages, provider_call):
            self.calls.append((username, model, messages))
            self.released = True
            raise ConnectionError("offline")

    gateway = ReleasingGateway()
    adapter = AIAdapter(
        FakeDatabase([]), client=FakeClient(reply="provider reply"), billing_gateway=gateway,
    )

    with adapter.billing_user("alice"):
        with pytest.raises(RuntimeError):
            adapter.reply_with_history("hello", [], model="deepseek")
    assert gateway.released is True


def test_legacy_switch_calls_legacy_api_handler(monkeypatch):
    adapter = AIAdapter(FakeDatabase([]), client=FakeClient(reply="provider reply"))
    monkeypatch.setattr(ai_adapter.config, "USE_LEGACY_API", True, raising=False)
    calls = []
    monkeypatch.setattr(
        adapter, "legacy_api_handler", lambda messages, model: calls.append((messages, model)) or "legacy reply"
    )

    assert adapter.reply_with_history("hello", [], model="deepseek") == "legacy reply"
    assert calls == [([{"role": "user", "content": "hello"}], "deepseek")]


@pytest.mark.parametrize(
    ("model", "model_id"),
    [
        ("zhipu", "glm-5.2"),
        ("qwen", "Qwen3.6-35B-A3B"),
        ("bytedance", "Seed-OSS-36B-Instruct"),
    ],
)
def test_reply_with_history_routes_each_final_model_to_its_exact_id(
    model,
    model_id,
    gateway_main_runtime,
):
    client = FakeClient(reply="reply")

    assert AIAdapter(FakeDatabase([]), client=client).reply_with_history(
        "hello", [], model=model
    ) == "reply"
    assert client.completions.kwargs["model"] == model_id


@pytest.mark.parametrize(
    ("api_key", "configured_url"),
    [
        (None, "https://gateway-compat.example.invalid/v1"),
        ("test-key", None),
    ],
)
def test_adapter_rejects_missing_or_none_api_configuration(monkeypatch, api_key, configured_url):
    monkeypatch.setattr(ai_adapter.config, "DEFAULT_MODEL_RUNTIME", "legacy", raising=False)
    monkeypatch.setattr(ai_adapter.config, "GATEWAY_COMPAT_API_KEY", api_key, raising=False)
    monkeypatch.setattr(ai_adapter.config, "GATEWAY_COMPAT_BASE_URL", configured_url, raising=False)

    with pytest.raises(RuntimeError, match="缺少"):
        AIAdapter(FakeDatabase([]))


def test_adapter_accepts_operator_supplied_generic_gateway_base_url(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai_adapter.config, "DEFAULT_MODEL_RUNTIME", "legacy", raising=False)
    monkeypatch.setattr(ai_adapter.config, "GATEWAY_COMPAT_BASE_URL", "https://synthetic-gateway.example.invalid/v1", raising=False)
    monkeypatch.setattr(ai_adapter, "OpenAI", lambda **kwargs: captured.update(kwargs))

    AIAdapter(FakeDatabase([]))
    assert captured["base_url"] == "https://synthetic-gateway.example.invalid/v1"


@pytest.mark.parametrize("model_name", ["deepseek-ai/DeepSeek-V3", "another-model"])
def test_adapter_rejects_a_model_other_than_deepseek_v4_flash(monkeypatch, model_name):
    monkeypatch.setattr(ai_adapter.config, "MODEL_NAME", model_name, raising=False)

    with pytest.raises(RuntimeError, match="模型"):
        AIAdapter(FakeDatabase([]), client=FakeClient(reply="ok"))


@pytest.mark.parametrize(
    ("error", "message"),
    [(TimeoutError(), "超时"), (ConnectionError(), "API 或网络")],
)
def test_reply_translates_transport_errors_to_chinese_runtime_errors(error, message):
    expected_exception = TimeoutError if isinstance(error, TimeoutError) else RuntimeError
    with pytest.raises(expected_exception, match=message):
        AIAdapter(FakeDatabase([]), client=FakeClient(error=error)).reply("hello")


def test_reply_exposes_timeout_as_timeout_error_for_the_web_layer():
    with pytest.raises(TimeoutError):
        AIAdapter(FakeDatabase([]), client=FakeClient(error=TimeoutError())).reply("hello")


@pytest.mark.parametrize(
    "response",
    [
        type("Response", (), {"choices": []})(),
        type("Response", (), {"choices": [type("Choice", (), {"message": None})()]})(),
        type("Response", (), {})(),
    ],
)
def test_reply_rejects_empty_or_malformed_responses(response):
    with pytest.raises(RuntimeError, match="空响应"):
        AIAdapter(FakeDatabase([]), client=FakeClient(response=response)).reply("hello")


def test_reply_with_history_routes_qwen_official_to_gateway_secondary_client(monkeypatch):
    clients = []

    class RecordingOpenAI(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(reply="official reply")
            self.kwargs = kwargs
            clients.append(self)

    monkeypatch.setattr(ai_adapter, "OpenAI", RecordingOpenAI)
    monkeypatch.setattr(ai_adapter.config, "DEFAULT_MODEL_RUNTIME", "legacy", raising=False)
    monkeypatch.setattr(ai_adapter.config, "GATEWAY_SECONDARY_API_KEY", "gateway_secondary-test-key", raising=False)

    adapter = AIAdapter(FakeDatabase([]))
    assert adapter.reply_with_history("hello", [], model="qwen_official") == "official reply"

    official_client = clients[-1]
    assert official_client.kwargs["api_key"] == "gateway_secondary-test-key"
    assert official_client.kwargs["base_url"] == "https://gateway-secondary.example.invalid/v1"
    assert official_client.completions.kwargs["model"] == "qwen-max"


def test_health_check_uses_a_short_non_empty_request_with_a_realistic_timeout(
    gateway_main_runtime,
):
    client = FakeClient(reply="ok")

    assert AIAdapter(FakeDatabase([]), client=client).health_check("deepseek") is True
    assert client.completions.kwargs == {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "timeout": 30,
    }


def test_regular_reply_enforces_the_same_output_limit_used_for_wallet_freezing():
    client = FakeClient(reply="bounded reply")

    AIAdapter(FakeDatabase([]), client=client).reply_with_history(
        "hello", [], model="deepseek"
    )

    assert client.completions.kwargs["max_tokens"] == ai_adapter.config.MAX_OUTPUT_TOKENS


def test_stream_reply_with_history_yields_text_deltas_in_order():
    class StreamingCompletions:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return [
                type("Chunk", (), {"choices": [type("Choice", (), {"delta": type("Delta", (), {"content": "你"})()})()]})(),
                type("Chunk", (), {"choices": [type("Choice", (), {"delta": type("Delta", (), {"content": "好"})()})()]})(),
            ]

    completions = StreamingCompletions()
    client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()

    chunks = list(AIAdapter(FakeDatabase([]), client=client).stream_reply_with_history(
        "hello", [], model="deepseek"
    ))

    assert chunks == ["你", "好"]
    assert completions.kwargs["stream"] is True
    assert completions.kwargs["max_tokens"] == ai_adapter.config.MAX_OUTPUT_TOKENS


def test_stream_reply_uses_billing_gateway_inside_a_billing_user_context():
    class StreamingGateway(RecordingGateway):
        def stream(self, username, model, messages, provider_stream):
            self.calls.append((username, model, messages))
            yield "billed "
            yield "stream"

    gateway = StreamingGateway()
    adapter = AIAdapter(
        FakeDatabase([]), client=FakeClient(reply="unused"), billing_gateway=gateway
    )

    with adapter.billing_user("alice"):
        chunks = list(adapter.stream_reply_with_history("hello", [], model="deepseek"))

    assert chunks == ["billed ", "stream"]
    assert gateway.calls == [
        ("alice", "deepseek", [{"role": "user", "content": "hello"}])
    ]
