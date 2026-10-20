"""Bounded, process-safe scheduling for safe model catalog refreshes."""

from __future__ import annotations

import hashlib
import os
import random
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable, Iterator, Mapping

from model_catalog import CATALOG_DEGRADED_MESSAGE, CATALOG_UNAVAILABLE_MESSAGE

_SAFE_FAILURE_CATEGORIES = {
    "configuration",
    "transport",
    "http",
    "invalid_response",
    "cache_write",
    "unknown",
}


class ProcessFileLock:
    """Use one safe temporary byte-range lock for a catalog cache path."""

    _held_paths: set[str] = set()
    _held_paths_lock = Lock()

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_cache_path(cls, cache_path: str | Path) -> "ProcessFileLock":
        normalized = str(Path(cache_path).resolve()).casefold()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        lock_path = (
            Path(tempfile.gettempdir())
            / "astrarelay-catalog-locks"
            / f"{digest}.lock"
        )
        return cls(lock_path)

    @contextmanager
    def acquire(self) -> Iterator[bool]:
        path_key = str(self.path.resolve()).casefold()
        with self._held_paths_lock:
            locally_acquired = path_key not in self._held_paths
            if locally_acquired:
                self._held_paths.add(path_key)

        if not locally_acquired:
            yield False
            return

        handle = None
        locked = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    pass
                else:
                    locked = True
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    pass
                else:
                    locked = True
        except OSError:
            locked = False

        if not locked:
            if handle is not None:
                handle.close()
            with self._held_paths_lock:
                self._held_paths.discard(path_key)
            yield False
            return

        try:
            yield True
        finally:
            if handle is not None:
                try:
                    if os.name == "nt":
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                handle.close()
            with self._held_paths_lock:
                self._held_paths.discard(path_key)


class _NoopProcessLock:
    @contextmanager
    def acquire(self) -> Iterator[bool]:
        yield True


class CatalogRefreshCoordinator:
    """Serialize refreshes and schedule bounded retries without blocking startup."""

    RETRY_DELAYS = (30, 120, 600, 1800)
    NORMAL_INTERVAL = 3600

    def __init__(
        self,
        catalog_service: Any,
        logger: Any,
        *,
        wait: Callable[[float], bool] | None = None,
        jitter: Callable[[float], float] | None = None,
        process_lock: Any | None = None,
    ) -> None:
        self.catalog_service = catalog_service
        self.logger = logger
        self._refresh_lock = Lock()
        self._stop_event = Event()
        self._wait = wait or self._stop_event.wait
        self._jitter = jitter or (
            lambda delay: random.uniform(delay * 0.8, delay * 1.2)
        )
        cache_path = getattr(catalog_service, "cache_path", None)
        self._process_lock = (
            process_lock
            if process_lock is not None
            else ProcessFileLock.for_cache_path(cache_path)
            if isinstance(cache_path, (str, Path))
            else _NoopProcessLock()
        )
        self._retry_index = 0

    def refresh_once(self, *, manual: bool = False) -> dict[str, Any]:
        """Run one serialized refresh and return only a safe catalog result."""
        with self._refresh_lock:
            if manual:
                self._retry_index = 0
            with self._process_lock.acquire() as acquired:
                if not acquired:
                    self.logger.info("Model catalog refresh skipped reason=busy")
                    return self._loaded_catalog_or_safe_failure()
                try:
                    catalog = self.catalog_service.refresh()
                except Exception:
                    self.logger.warning("Model catalog refresh failed category=unknown")
                    catalog = self._unexpected_failure_catalog()

            availability = catalog.get("availability")
            if availability == "fresh":
                self._retry_index = 0
            categories = catalog.get("failure_categories")
            safe_categories = (
                ",".join(categories)
                if isinstance(categories, list)
                and all(
                    isinstance(category, str)
                    and category in _SAFE_FAILURE_CATEGORIES
                    for category in categories
                )
                else "unknown"
            )
            self.logger.info(
                "Model catalog refresh availability=%s categories=%s",
                availability if availability in {"fresh", "degraded", "unavailable"} else "unknown",
                safe_categories or "none",
            )
            return catalog

    def run_forever(self) -> None:
        """Refresh immediately, then wait for the bounded next interval."""
        while True:
            catalog = self.refresh_once()
            if self._wait(self._next_delay(catalog)):
                return

    def stop(self) -> None:
        self._stop_event.set()

    def _next_delay(self, catalog: Mapping[str, Any]) -> float:
        if catalog.get("retryable") is True and self._retry_index < len(
            self.RETRY_DELAYS
        ):
            delay = self.RETRY_DELAYS[self._retry_index]
            self._retry_index += 1
            return self._jitter(delay)
        self._retry_index = 0
        return self.NORMAL_INTERVAL

    def _unexpected_failure_catalog(self) -> dict[str, Any]:
        cached = self._loaded_catalog_or_safe_failure()
        if cached.get("failure_categories") == ["unknown"]:
            return cached
        providers = cached.get("providers")
        safe_providers = providers if isinstance(providers, list) else []
        availability = "degraded" if safe_providers else "unavailable"
        last_attempt_at = cached.get("last_attempt_at")
        last_success_at = cached.get("last_success_at")
        return {
            "availability": availability,
            "last_attempt_at": last_attempt_at if isinstance(last_attempt_at, str) else None,
            "last_success_at": last_success_at if isinstance(last_success_at, str) else None,
            "failure_categories": ["unknown"],
            "retryable": False,
            "message": (
                CATALOG_DEGRADED_MESSAGE
                if availability == "degraded"
                else CATALOG_UNAVAILABLE_MESSAGE
            ),
            "providers": safe_providers,
            "updated_at": last_attempt_at if isinstance(last_attempt_at, str) else None,
        }

    def _loaded_catalog_or_safe_failure(self) -> dict[str, Any]:
        try:
            cached = self.catalog_service.load()
        except Exception:
            cached = {}
        if isinstance(cached, dict) and cached.get("availability") in {
            "fresh",
            "degraded",
            "unavailable",
        }:
            return cached
        providers = cached.get("providers") if isinstance(cached, Mapping) else None
        safe_providers = providers if isinstance(providers, list) else []
        availability = "degraded" if safe_providers else "unavailable"
        last_attempt_at = (
            cached.get("last_attempt_at") if isinstance(cached, Mapping) else None
        )
        last_success_at = (
            cached.get("last_success_at") if isinstance(cached, Mapping) else None
        )
        return {
            "availability": availability,
            "last_attempt_at": last_attempt_at if isinstance(last_attempt_at, str) else None,
            "last_success_at": last_success_at if isinstance(last_success_at, str) else None,
            "failure_categories": ["unknown"],
            "retryable": False,
            "message": (
                CATALOG_DEGRADED_MESSAGE
                if availability == "degraded"
                else CATALOG_UNAVAILABLE_MESSAGE
            ),
            "providers": safe_providers,
            "updated_at": last_attempt_at if isinstance(last_attempt_at, str) else None,
        }
