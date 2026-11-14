"""A counting window closes a fixed time after its first count, however busy it is.

Broke as: the increment helper sent EXPIRE with every INCRBY, so each attempt pushed the
end of the window another 24 hours away. A server that opened a feature at least once a
day never saw its window close, and the log footnote kept telling the operator "the 9th
run in the last 24h" about runs spread over a week.

Shared behaviour: `cache.increment_redis_key_with_expiration`, the one helper every
expiring counter of Redis goes through.
Exposed by: `analytics.count_attempt`, the attempt footnote on a command's log message.

Guaranteed: the TTL is set by the first count only; a later count adds to the window
without moving its end, and the first count after the end starts a new window.
"""

import pytest

from app.constants import Commands
from app.core import analytics, cache

pytestmark = pytest.mark.unit

GUILD_ID = "80"
FEATURE = "block_links"
WINDOW = Commands.ANALYTICS_ATTEMPTS_WINDOW_SECONDS
HOUR = 60 * 60


def test_the_attempt_window_ends_a_day_after_the_first_attempt(deps):
    assert analytics.count_attempt(GUILD_ID, FEATURE) == 1

    deps.redis_client.advance(WINDOW - HOUR)
    second = analytics.count_attempt(GUILD_ID, FEATURE)
    assert analytics.describe_attempt(second, FEATURE) == (
        f"⚠️ 2º run of `{FEATURE}` by this guild in the last 24h"
    )

    deps.redis_client.advance(HOUR)
    after_the_window = analytics.count_attempt(GUILD_ID, FEATURE)

    assert after_the_window == 1, "the second attempt pushed the window's end a day away"
    assert analytics.describe_attempt(after_the_window, FEATURE) is None


def test_a_later_count_keeps_the_ttl_the_first_one_set(deps):
    key = Commands.REDIS_ATTEMPTS_WINDOW.format(guild_id=GUILD_ID, feature=FEATURE)

    cache.increment_redis_key_with_expiration(key, 1, WINDOW)
    deps.redis_client.advance(HOUR)
    cache.increment_redis_key_with_expiration(key, 2, WINDOW)

    assert deps.redis_client.get(key) == "3"
    assert deps.redis_client.ttl(key) == WINDOW - HOUR
