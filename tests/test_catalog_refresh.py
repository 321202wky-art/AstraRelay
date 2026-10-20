import logging
import random
import threading
from contextlib import contextmanager

import pytest

from catalog_refresh import CatalogRefreshCoordinator, ProcessFileLock


class SequenceCatalog:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def refresh(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    def load(self):
        return {
            "availability": "unavailable",
            "last_attempt_at": None,
            "last_success_at": None,
            "failure_categories": [],
            "retryable": False,
            "message": "safe",
            "providers": [],
            "updated_at": None,
        }


def _catalog(availability, retryable=False):
    return {
        "availability": availability,
        "retryable": retryable,
        "failure_categories": ["transport"] if retryable else [],
        "providers": [],
    }


def _run_and_record_delays(outcomes, stop_after):
    delays = []

    def wait(delay):
        delays.append(delay)
        return len(delays) >= stop_after

    service = SequenceCatalog(outcomes)
    coordinator = CatalogRefreshCoordinator(
        service,
        logging.getLogger("catalog-refresh-test"),
        wait=wait,
        jitter=lambda delay: delay,
    )

    coordinator.run_forever()

    return service, delays


def test_fresh_catalog_uses_the_normal_refresh_interval():
    service, delays = _run_and_record_delays([_catalog("fresh")], stop_after=1)

    assert service.calls == 1
    assert delays == [3600]


def test_retryable_failure_uses_four_bounded_delays_then_normal_interval():
    service, delays = _run_and_record_delays(
        [_catalog("degraded", retryable=True) for _ in range(5)],
        stop_after=5,
    )

    assert service.calls == 5
    assert delays == [30, 120, 600, 1800, 3600]


def test_non_retryable_failure_skips_fast_retries():
    service, delays = _run_and_record_delays(
        [_catalog("unavailable", retryable=False)], stop_after=1
    )

    assert service.calls == 1
    assert delays == [3600]


def test_default_retry_jitter_is_bounded_to_twenty_percent(monkeypatch):
    bounds = []

    def uniform(lower, upper):
        bounds.append((lower, upper))
        return 27

    monkeypatch.setattr(random, "uniform", uniform)
    service = SequenceCatalog([_catalog("degraded", retryable=True)])
    delays = []
    coordinator = CatalogRefreshCoordinator(
        service,
        logging.getLogger("catalog-refresh-jitter-test"),
        wait=lambda delay: delays.append(delay) or True,
    )

    coordinator.run_forever()

    assert bounds == [(24, 36)]
    assert delays == [27]


def test_success_resets_the_fast_retry_sequence():
    service, delays = _run_and_record_delays(
        [
            _catalog("degraded", retryable=True),
            _catalog("degraded", retryable=True),
            _catalog("fresh"),
            _catalog("degraded", retryable=True),
        ],
        stop_after=4,
    )

    assert service.calls == 4
    assert delays == [30, 120, 3600, 30]


def test_refresh_once_serializes_concurrent_manual_and_background_calls():
    entered = threading.Event()
    release = threading.Event()

    class BlockingCatalog:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.calls = 0
            self.lock = threading.Lock()

        def refresh(self):
            with self.lock:
                self.calls += 1
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            entered.set()
            assert release.wait(1)
            with self.lock:
                self.active -= 1
            return _catalog("fresh")

    service = BlockingCatalog()
    coordinator = CatalogRefreshCoordinator(
        service,
        logging.getLogger("catalog-refresh-lock-test"),
        wait=lambda _delay: True,
        jitter=lambda delay: delay,
    )
    first = threading.Thread(target=coordinator.refresh_once)
    second = threading.Thread(
        target=lambda: coordinator.refresh_once(manual=True)
    )

    first.start()
    assert entered.wait(0.5)
    second.start()
    release.set()
    first.join(1)
    second.join(1)

    assert service.calls == 2
    assert service.max_active == 1


def test_unexpected_refresh_error_logs_only_a_safe_category(caplog):
    private_detail = "private URL and credential detail"
    service = SequenceCatalog([RuntimeError(private_detail)])
    coordinator = CatalogRefreshCoordinator(
        service,
        logging.getLogger("catalog-refresh-safe-log-test"),
        wait=lambda _delay: True,
        jitter=lambda delay: delay,
    )

    with caplog.at_level(logging.WARNING):
        result = coordinator.refresh_once()

    assert result["availability"] == "unavailable"
    assert result["failure_categories"] == ["unknown"]
    assert result["retryable"] is False
    assert "category=unknown" in caplog.text
    assert private_detail not in caplog.text


def test_refresh_log_rejects_non_allowlisted_category_text(caplog):
    private_detail = "private URL and response detail"
    unsafe_catalog = _catalog("degraded", retryable=True)
    unsafe_catalog["failure_categories"] = [private_detail]
    coordinator = CatalogRefreshCoordinator(
        SequenceCatalog([unsafe_catalog]),
        logging.getLogger("catalog-refresh-category-filter-test"),
        wait=lambda _delay: True,
        jitter=lambda delay: delay,
    )

    with caplog.at_level(logging.INFO):
        coordinator.refresh_once()

    assert "categories=unknown" in caplog.text
    assert private_detail not in caplog.text


def test_process_file_lock_allows_only_one_holder(tmp_path):
    first = ProcessFileLock(tmp_path / "catalog-refresh.lock")
    second = ProcessFileLock(tmp_path / "catalog-refresh.lock")

    with first.acquire() as first_acquired:
        with second.acquire() as second_acquired:
            assert first_acquired is True
            assert second_acquired is False

    with second.acquire() as acquired_after_release:
        assert acquired_after_release is True


def test_process_file_lock_releases_after_caller_exception(tmp_path):
    lock = ProcessFileLock(tmp_path / "catalog-refresh.lock")

    with pytest.raises(RuntimeError, match="caller failure"):
        with lock.acquire() as acquired:
            assert acquired is True
            raise RuntimeError("caller failure")

    with lock.acquire() as acquired_after_failure:
        assert acquired_after_failure is True


def test_busy_process_lock_skips_duplicate_refresh():
    class BusyLock:
        @contextmanager
        def acquire(self):
            yield False

    service = SequenceCatalog([_catalog("fresh")])
    coordinator = CatalogRefreshCoordinator(
        service,
        logging.getLogger("catalog-refresh-process-lock-test"),
        wait=lambda _delay: True,
        jitter=lambda delay: delay,
        process_lock=BusyLock(),
    )

    result = coordinator.refresh_once()

    assert service.calls == 0
    assert result["availability"] == "unavailable"
