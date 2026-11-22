"""The Redis counters a command keeps over a window: what they count, and how long
their window has left."""

import pytest

from app.core.cache import increment_redis_key_with_expiration, read_redis_counter

pytestmark = pytest.mark.unit

KEY = "window:test:555"
HOUR = 60 * 60


def test_a_counter_reads_its_count_and_the_seconds_its_window_has_left(deps):
    increment_redis_key_with_expiration(KEY, 1, HOUR)
    deps.redis_client.advance(600)
    increment_redis_key_with_expiration(KEY, 1, HOUR)

    assert read_redis_counter(KEY) == (2, HOUR - 600)


def test_a_counter_never_counted_or_whose_window_ended_reads_nothing(deps):
    assert read_redis_counter(KEY) == (0, 0)

    increment_redis_key_with_expiration(KEY, 1, HOUR)
    deps.redis_client.advance(HOUR)

    assert read_redis_counter(KEY) == (0, 0)


def test_a_counter_that_never_expires_has_no_window_left(deps):
    deps.redis_client.set(KEY, "4")

    assert read_redis_counter(KEY) == (4, 0)
