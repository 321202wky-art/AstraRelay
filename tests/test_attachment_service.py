import pytest
from threading import Event, Thread


class Upload:
    def __init__(self, filename, payload, mimetype):
        self.filename = filename
        self._payload = payload
        self.mimetype = mimetype

    def read(self):
        return self._payload


def test_text_attachment_returns_owner_bound_token_and_bounded_context():
    from attachment_service import AttachmentService, AttachmentStore

    store = AttachmentStore(ttl_seconds=60)
    service = AttachmentService(store=store, max_file_bytes=1024)

    result = service.prepare_files(
        "user-1",
        "conversation-1",
        [Upload("notes.md", b"# plan\nDo the task.", "text/markdown")],
    )

    assert result[0]["name"] == "notes.md"
    assert result[0]["token"]
    assert result[0]["preview"] == "# plan\nDo the task."
    resolved = store.resolve("user-1", "conversation-1", [result[0]["token"]])
    assert resolved[0]["context"].startswith("附件 notes.md")
    assert store.resolve("another-user", "conversation-1", [result[0]["token"]]) == []


def test_attachment_service_rejects_oversize_and_unsupported_files():
    from attachment_service import AttachmentService, AttachmentStore

    service = AttachmentService(store=AttachmentStore(), max_file_bytes=3)

    with pytest.raises(ValueError, match="文件过大"):
        service.prepare_files("user-1", "conversation-1", [Upload("a.txt", b"four", "text/plain")])
    with pytest.raises(ValueError, match="暂不支持"):
        service.prepare_files(
            "user-1", "conversation-1", [Upload("run.exe", b"x", "application/octet-stream")]
        )


def test_image_attachment_uses_safe_media_description_without_storing_bytes():
    from attachment_service import AttachmentService, AttachmentStore

    class FakeMediaService:
        def describe_image(self, model_id, prompt, image_bytes, mime_type):
            assert model_id == "qwen3-vl-plus-2025-09-23"
            assert prompt == "请概述这张图片中的关键信息，供后续任务使用。"
            assert image_bytes == b"image-bytes"
            assert mime_type == "image/png"
            return "一张白板照片"

    store = AttachmentStore(ttl_seconds=60)
    service = AttachmentService(
        store=store,
        media_service=FakeMediaService(),
        max_file_bytes=1024,
    )

    result = service.prepare_files(
        "user-1",
        "conversation-1",
        [Upload("board.png", b"image-bytes", "image/png")],
    )
    resolved = store.resolve("user-1", "conversation-1", [result[0]["token"]])

    assert result[0]["preview"] == "图片已提取摘要"
    assert "一张白板照片" in resolved[0]["context"]
    assert "image-bytes" not in resolved[0]["context"]


def test_conversation_lifecycle_serializes_waiters_and_reclaims_unused_lock():
    from attachment_service import AttachmentStore

    store = AttachmentStore()
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def first():
        with store.conversation_lifecycle("alice", "conversation-1"):
            first_entered.set()
            assert release_first.wait(2)

    def second():
        assert first_entered.wait(1)
        with store.conversation_lifecycle("alice", "conversation-1"):
            second_entered.set()

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(1)
    assert not second_entered.wait(0.1)
    release_first.set()
    first_thread.join(2)
    second_thread.join(2)

    assert second_entered.is_set()
    assert store._lifecycle_locks == {}
