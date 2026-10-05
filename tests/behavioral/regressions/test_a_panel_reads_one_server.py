"""Opening a panel reads one server's last day, and never on the event loop.

Broke as: every form that opened (a slash command, a `/setup` button, a manager
panel) asked `journey.recent_attempts` for the footnote of its log message, and
that ran `analytics_reports.setup_sessions(feature)`: seven queries that brought
every setup event of the feature, from every server, for the whole 90 days the
events live, and threw away all but one server's last 24 hours in Python. It ran
synchronously inside `Runtime.open_feature`, so the bot's loop stood still for
the whole read, inside the three seconds Discord gives the first screen, and the
cost grew with the activity of every server at once.

Shared behaviour: `journey.open_journey` and `recent_attempts` (every form the
platform opens), `analytics_reports.setup_sessions` (also the `/admin insights`
drop-off report). Consumer that exposed it: the review of v1 (finding 3.12),
on block_links.

Guaranteed: the footnote still lists what this server tried on this feature in
the last 24 hours, read from this server's events of that window only, so the
read is bounded by the window and not by the whole bot, and the read runs in a
worker thread whenever a loop runs, so opening a panel never waits on it.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.data import analytics as analytics_data
from app.services import analytics, journey
from tests.behavioral.regressions.test_event_loop_is_never_blocked import (
    MIN_TICKS,
    TICK_SECONDS,
    blocking,
    ticks_while,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("journey"),
]

FEATURE = "block_links"
HERE = "1"
ELSEWHERE = "2"


@pytest.fixture(autouse=True)
def journey_isolation():
    journey.clear()
    journey.set_publisher(None)
    yield
    journey.clear()
    journey.set_publisher(None)


def attempt(session, guild, step="mode"):
    analytics.emit(
        "feature.setup_opened", guild_id=guild, user_id="9", feature=FEATURE,
        source="slash", session_id=session,
    )
    analytics.emit(
        "setup.step_viewed", guild_id=guild, user_id="9", feature=FEATURE,
        source="slash", session_id=session, step_key=step,
        step_action="options", step_index=1,
    )


def age(deps, session, hours):
    moment = datetime.now(timezone.utc) - timedelta(hours=hours)
    for document in deps.mongo_client.guild.analytics_events._data:
        if document.get("session_id") == session:
            document["ts"] = moment


def documents_read(deps, monkeypatch):
    """Every event document a query on the collection handed back."""
    collection = deps.mongo_client.guild.analytics_events
    original = collection.find
    read = []

    def find(filter_dict=None, projection=None):
        cursor = original(filter_dict, projection)
        read.extend(cursor)
        return cursor

    monkeypatch.setattr(collection, "find", find)
    return read


def open_panel(session="s-new"):
    return journey.open_journey(
        session, "moderations block links",
        guild_id=HERE, user_id="9", feature=FEATURE, source="slash",
    )


def test_opening_a_panel_reads_only_this_servers_last_day(deps, monkeypatch):
    attempt("s-recent", HERE)
    attempt("s-elsewhere", ELSEWHERE)
    attempt("s-old", HERE, step="permissions")
    analytics.flush()
    age(deps, "s-old", hours=48)
    read = documents_read(deps, monkeypatch)

    story = open_panel()

    assert story.footnote and "this guild" in story.footnote
    assert story.footnote.count("\n") == 1, "one earlier attempt in the last day"
    assert read, "the history was read"
    assert {document["guild_id"] for document in read} == {HERE}, (
        "another server's events were brought back to be thrown away"
    )
    assert "s-old" not in {document["session_id"] for document in read}, (
        "events older than the 24 hours on the message were read"
    )


async def test_opening_a_panel_never_waits_on_its_history(deps, monkeypatch):
    earlier = datetime.now(timezone.utc) - timedelta(minutes=5)
    stored = [
        {
            "event": "feature.setup_opened", "session_id": "s-recent",
            "guild_id": HERE, "user_id": "9", "feature": FEATURE,
            "source": "slash", "props": {}, "ts": earlier,
        },
    ]
    monkeypatch.setattr(analytics_data, "find_events", blocking(stored))

    async def open_and_read():
        story = open_panel()
        while story.footnote is None:
            await asyncio.sleep(TICK_SECONDS)
        return story

    ticks = await ticks_while(asyncio.wait_for(open_and_read(), timeout=5))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times while the panel read its history"
    )
