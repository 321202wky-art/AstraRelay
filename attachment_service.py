"""Secure, short-lived preprocessing for workspace attachments."""

from __future__ import annotations

import io
import json
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock


class AttachmentStore:
    """Keep bounded attachment context in memory and scoped to an owner/session."""

    def __init__(self, ttl_seconds=900):
        self.ttl_seconds = ttl_seconds
        self._items = {}
        self._lock = Lock()
        self._lifecycle_guard = Lock()
        self._lifecycle_locks = {}

    @contextmanager
    def conversation_lifecycle(self, user_id, conversation_id):
        """Serialize attachment lifecycle work for one owner and conversation."""
        key = (user_id, conversation_id)
        with self._lifecycle_guard:
            entry = self._lifecycle_locks.get(key)
            if entry is None:
                entry = {"lock": RLock(), "users": 0}
                self._lifecycle_locks[key] = entry
            entry["users"] += 1
        try:
            with entry["lock"]:
                yield
        finally:
            with self._lifecycle_guard:
                entry["users"] -= 1
                if entry["users"] == 0 and self._lifecycle_locks.get(key) is entry:
                    del self._lifecycle_locks[key]

    def put(self, user_id, conversation_id, name, media_type, context):
        token = secrets.token_urlsafe(24)
        item = {
            "token": token,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "name": name,
            "media_type": media_type,
            "context": context,
            "expires_at": time.monotonic() + self.ttl_seconds,
        }
        with self._lock:
            self._discard_expired()
            self._items[token] = item
        return token

    def resolve(self, user_id, conversation_id, tokens):
        if not isinstance(tokens, list):
            return []
        with self._lock:
            self._discard_expired()
            resolved = []
            for token in tokens:
                item = self._items.get(token)
                if (
                    item
                    and item["user_id"] == user_id
                    and item["conversation_id"] == conversation_id
                ):
                    resolved.append(dict(item))
            return resolved

    def discard_conversation(self, user_id, conversation_id):
        """Discard every attachment token owned by one conversation."""
        with self._lock:
            self._discard_expired()
            for token, item in list(self._items.items()):
                if (
                    item["user_id"] == user_id
                    and item["conversation_id"] == conversation_id
                ):
                    del self._items[token]

    def _discard_expired(self):
        now = time.monotonic()
        for token, item in list(self._items.items()):
            if item["expires_at"] <= now:
                del self._items[token]


class AttachmentService:
    """Validate uploads and convert them into safe, bounded model context."""

    MAX_FILES = 5
    MAX_FILE_BYTES = 10 * 1024 * 1024
    MAX_CONTEXT_CHARS = 12_000
    MAX_PREVIEW_CHARS = 240
    IMAGE_PROMPT = "请概述这张图片中的关键信息，供后续任务使用。"
    IMAGE_MODEL_ID = "qwen3-vl-plus-2025-09-23"

    IMAGE_MIME_TYPES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    TEXT_EXTENSIONS = {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".py",
        ".js",
        ".ts",
        ".java",
        ".go",
        ".c",
        ".cpp",
        ".h",
        ".html",
        ".css",
        ".xml",
        ".yaml",
        ".yml",
        ".sql",
    }
    DOCUMENT_EXTENSIONS = {".pdf", ".docx"}

    def __init__(self, store=None, media_service=None, max_file_bytes=None):
        self.store = store or AttachmentStore()
        self.media_service = media_service
        self.max_file_bytes = self.MAX_FILE_BYTES if max_file_bytes is None else max_file_bytes

    def prepare_files(self, user_id, conversation_id, files):
        if not isinstance(files, (list, tuple)) or not files:
            raise ValueError("请选择至少一个附件")
        if len(files) > self.MAX_FILES:
            raise ValueError(f"一次最多上传 {self.MAX_FILES} 个附件")

        prepared = []
        for upload in files:
            name = self._safe_name(getattr(upload, "filename", ""))
            extension = Path(name).suffix.lower()
            supported_extensions = set(self.IMAGE_MIME_TYPES) | self.TEXT_EXTENSIONS | self.DOCUMENT_EXTENSIONS
            if extension not in supported_extensions:
                raise ValueError("暂不支持该文件类型")

            payload = upload.read()
            if not isinstance(payload, bytes) or not payload:
                raise ValueError("附件内容为空")
            if len(payload) > self.max_file_bytes:
                raise ValueError("文件过大，请上传不超过 10MB 的文件")

            media_type = self._media_type(extension, getattr(upload, "mimetype", ""))
            extracted = self._extract(extension, payload, media_type)
            context = self._bounded_context(name, extracted)
            token = self.store.put(user_id, conversation_id, name, media_type, context)
            prepared.append(
                {
                    "token": token,
                    "name": name,
                    "media_type": media_type,
                    "size": len(payload),
                    "preview": self._preview(extracted, extension),
                }
            )
        return prepared

    @staticmethod
    def _safe_name(name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("附件缺少文件名")
        normalized = name.replace("\\", "/").split("/")[-1].strip()
        if not normalized or normalized in {".", ".."}:
            raise ValueError("附件文件名无效")
        return normalized[:160]

    def _media_type(self, extension, reported_type):
        if extension in self.IMAGE_MIME_TYPES:
            expected = self.IMAGE_MIME_TYPES[extension]
            if reported_type and reported_type.lower() not in {expected, "application/octet-stream"}:
                raise ValueError("图片格式与文件扩展名不一致")
            return expected
        if extension == ".pdf":
            return "application/pdf"
        if extension == ".docx":
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return "text/plain"

    def _extract(self, extension, payload, media_type):
        if extension in self.IMAGE_MIME_TYPES:
            if self.media_service is None:
                raise ValueError("图片解析服务暂不可用")
            try:
                return self.media_service.describe_image(
                    self.IMAGE_MODEL_ID, self.IMAGE_PROMPT, payload, media_type
                )
            except (TimeoutError, RuntimeError, ValueError) as exc:
                raise ValueError("图片解析失败，请稍后重试") from exc
        if extension == ".pdf":
            return self._extract_pdf(payload)
        if extension == ".docx":
            return self._extract_docx(payload)
        return self._extract_text(extension, payload)

    def _extract_text(self, extension, payload):
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("文本文件必须使用 UTF-8 编码") from exc
        if extension == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass
        return text.strip() or "（文件没有可提取的文本内容）"

    def _extract_pdf(self, payload):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(payload))
            return "\n".join((page.extract_text() or "") for page in reader.pages).strip() or "（PDF 没有可提取的文本内容）"
        except Exception as exc:
            raise ValueError("PDF 内容无法解析，请确认文件后重试") from exc

    def _extract_docx(self, payload):
        try:
            from docx import Document

            document = Document(io.BytesIO(payload))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs).strip()
            return text or "（DOCX 没有可提取的文本内容）"
        except Exception as exc:
            raise ValueError("DOCX 内容无法解析，请确认文件后重试") from exc

    def _bounded_context(self, name, extracted):
        text = str(extracted).strip()
        if len(text) > self.MAX_CONTEXT_CHARS:
            text = f"{text[:self.MAX_CONTEXT_CHARS]}\n（附件内容已截断）"
        return f"附件 {name}：\n{text}"

    def _preview(self, extracted, extension):
        if extension in self.IMAGE_MIME_TYPES:
            return "图片已提取摘要"
        text = str(extracted).strip()
        return text[: self.MAX_PREVIEW_CHARS] + ("…" if len(text) > self.MAX_PREVIEW_CHARS else "")
