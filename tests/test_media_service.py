from threading import Barrier, Event, Thread
from types import SimpleNamespace

import pytest


class FakeMediaClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._describe))
        self.images = SimpleNamespace(generate=self._generate)
        self.describe_request = None
        self.generate_request = None

    def _describe(self, **kwargs):
        self.describe_request = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="description"))]
        )

    def _generate(self, **kwargs):
        self.generate_request = kwargs
        return SimpleNamespace(
            data=[
                SimpleNamespace(url="https://example.test/image.png"),
                SimpleNamespace(url=""),
            ]
        )


def test_describe_image_sends_data_url_content_to_chat_client():
    from media_service import MediaService

    client = FakeMediaClient()
    service = MediaService(client=client)

    assert service.describe_image("qwen-vl-plus-latest", "Describe", b"png", "image/png") == "description"
    content = client.describe_request["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Describe"}
    assert content[1]["image_url"]["url"] == "data:image/png;base64,cG5n"


def test_default_client_is_created_once_on_first_media_operation(monkeypatch):
    from media_service import MediaService

    created_clients = []

    def create_client():
        client = FakeMediaClient()
        created_clients.append(client)
        return client

    monkeypatch.setattr(MediaService, "_create_client", staticmethod(create_client))

    service = MediaService()

    assert created_clients == []
    assert service.describe_image("qwen-vl-plus-latest", "Describe", b"png", "image/png") == "description"
    assert service.generate_image("gpt-image-2", "A lighthouse") == ["https://example.test/image.png"]
    assert created_clients == [service.client]


def test_generate_image_first_lazily_creates_and_caches_default_client(monkeypatch):
    from media_service import MediaService

    created_clients = []

    def create_client():
        client = FakeMediaClient()
        created_clients.append(client)
        return client

    monkeypatch.setattr(MediaService, "_create_client", staticmethod(create_client))

    service = MediaService()

    assert created_clients == []
    assert service.generate_image("gpt-image-2", "A lighthouse") == ["https://example.test/image.png"]
    assert service.generate_image("gpt-image-2", "A second lighthouse") == ["https://example.test/image.png"]
    assert created_clients == [service.client]


def test_generate_image_preserves_default_client_configuration_error(monkeypatch):
    from media_service import MediaService

    def create_client():
        raise RuntimeError("missing media configuration")

    monkeypatch.setattr(MediaService, "_create_client", staticmethod(create_client))

    with pytest.raises(RuntimeError, match="missing media configuration"):
        MediaService().generate_image("gpt-image-2", "A lighthouse")


def test_concurrent_first_media_operations_create_one_default_client(monkeypatch):
    from media_service import MediaService

    created_clients = []
    creation_started = Event()
    release_creation = Event()
    start_operations = Barrier(3)
    results = []

    def create_client():
        client = FakeMediaClient()
        created_clients.append(client)
        creation_started.set()
        assert release_creation.wait(1)
        return client

    def generate_image():
        start_operations.wait()
        results.append(service.generate_image("gpt-image-2", "A lighthouse"))

    monkeypatch.setattr(MediaService, "_create_client", staticmethod(create_client))
    service = MediaService()
    first = Thread(target=generate_image)
    second = Thread(target=generate_image)
    first.start()
    second.start()
    start_operations.wait()
    assert creation_started.wait(0.5)
    release_creation.set()
    first.join(1)
    second.join(1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(created_clients) == 1
    assert results == [
        ["https://example.test/image.png"],
        ["https://example.test/image.png"],
    ]


def test_generate_image_returns_only_nonempty_provider_urls():
    from media_service import MediaService

    client = FakeMediaClient()
    service = MediaService(client=client)

    assert service.generate_image("gpt-image-2", "A lighthouse") == ["https://example.test/image.png"]
    assert client.generate_request["model"] == "gpt-image-2"


def test_media_service_rejects_unsupported_mime_and_oversize_input():
    from media_service import MediaService

    service = MediaService(client=FakeMediaClient(), max_image_bytes=3)

    with pytest.raises(ValueError, match="图片格式"):
        service.describe_image("qwen-vl-plus-latest", "Describe", b"x", "image/gif")
    with pytest.raises(ValueError, match="图片文件过大"):
        service.describe_image("qwen-vl-plus-latest", "Describe", b"png!", "image/png")
