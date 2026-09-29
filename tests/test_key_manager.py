import time

from forge.models.key_manager import KeyManager, NoKeysAvailable
import pytest


def test_round_robin_rotation():
    km = KeyManager(["a", "b", "c"])
    labels = [km.acquire().key for _ in range(6)]
    assert labels == ["a", "b", "c", "a", "b", "c"]


def test_duplicate_and_cap():
    km = KeyManager(["a", "a", "b", "c", "d", "e", "f"])
    assert len(km) == 5


def test_rate_limited_key_is_skipped():
    km = KeyManager(["a", "b"])
    first = km.acquire()
    km.report_rate_limit(first, retry_after=30)
    assert km.acquire().key == "b"


def test_failure_backoff_then_recovery():
    km = KeyManager(["a"])
    state = km.acquire()
    km.report_failure(state, "boom")
    assert state.failures == 1
    km.report_success(state)
    assert state.failures == 0 and state.last_error == ""


def test_no_keys_raises():
    with pytest.raises(NoKeysAvailable):
        KeyManager([]).acquire()


def test_all_cooling_down_waits_but_returns():
    km = KeyManager(["a"])
    state = km.acquire()
    km.report_rate_limit(state, retry_after=0.01)
    time.sleep(0.02)
    assert km.acquire().key == "a"
