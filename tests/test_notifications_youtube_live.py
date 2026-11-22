"""A YouTube live reaches every server that follows its channel's lives: once, when it
starts, whether the hub says so or its scheduled time comes; a scheduled live is read
again at its time and a few times after, never polled; and the notice turns ended,
with how long the live ran, only for a live Keiko announced.

Driven through the entry points YouTube and the bot use: the hub's signed notice on
`/v1/webhooks/youtube`, with the Data API answered from memory, and the pass of the
loop that hands the due schedules to their command.
"""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.constants import Commands
from app.core.jobs import hand_due_schedules
from app.core.utils import fill, ml
from app.data import data
from app.data.keiko.schedules import schedule_id
from tests.behavioral.harness.copy_keys import copy_text
from tests.behavioral.flows import youtube_live
from tests.behavioral.flows.common import seed_document
from tests.generators.moderations import recorded_server
from tests.helpers.youtube import PEWDIEPIE, atom, notify, on_fake_google, settle, sign
from tests.mocks import create_guild

pytestmark = pytest.mark.behavioral

ANNOUNCEMENT = "notifications_youtube_live.ui.announcement"
LIVE = "live-1"
LINK = f"https://www.youtube.com/watch?v={LIVE}"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
async def youtube(deps, monkeypatch):
    """YouTube's hub and Data API behind the real webhook and client."""
    return on_fake_google(deps, monkeypatch)


@pytest.fixture
def servers(deps):
    """Two servers, in English, following PewDiePie's lives."""
    first = create_guild(id=5001, name="First", channels=["lives"])
    second = create_guild(id=5002, name="Second", channels=["streams"])
    known = {first.id: first, second.id: second}
    deps.bot.get_guild = lambda guild_id: known.get(int(guild_id))
    for guild in known.values():
        recorded_server(deps.mongo_client, guild.id, language="en-us")
        notice = youtube_live.notification(str(guild.text_channels[0].id), "pewdiepie")
        seed_document(deps, youtube_live.FORM, {
            **youtube_live.ENABLED,
            "guild_id": str(guild.id),
            "notifications": {"style": "composition", "values": [notice]},
        })
    return SimpleNamespace(first=first, second=second, both=(first, second))


def on_youtube(youtube, broadcast, video_id=LIVE, **times):
    """What YouTube says of `video_id` of PewDiePie: its broadcast and its times."""
    youtube.google.videos[video_id] = PEWDIEPIE
    details = {name: moment.isoformat() for name, moment in times.items()}
    youtube.google.lives[video_id] = (broadcast, details)


def on_air(youtube, video_id=LIVE, started=NOW - timedelta(minutes=1)):
    on_youtube(youtube, "live", video_id, actualStartTime=started)


def scheduled(youtube, at, video_id=LIVE):
    on_youtube(youtube, "upcoming", video_id, scheduledStartTime=at)


def finished(youtube, started, ended, video_id=LIVE):
    on_youtube(youtube, "none", video_id, actualStartTime=started, actualEndTime=ended)


async def hub_notice(youtube, video_id=LIVE):
    """The hub's signed notice about `video_id`, and what the bot did with it."""
    body = atom(video_id)
    response = notify(youtube, body, sign(body))
    await settle()
    return response


def notices(guild):
    return [
        message
        for channel in guild.text_channels
        for message in channel.get_all_messages()
    ]


def footer(status, duration=None):
    """The status line of a live notice, as the shared copy writes it in English."""
    if status == "online":
        line = copy_text("commands.live_notice.footer", "en-us")
    elif duration:
        line = copy_text("commands.live_notice.overrides.0.footer", "en-us", duration=duration)
    else:
        line = copy_text("commands.live_notice.overrides.1.footer", "en-us")
    return f"• {line}"


async def schedule_of(video_id=LIVE):
    return await data().keiko_schedules.find_schedule(schedule_id("youtube_live", video_id))


async def test_a_live_that_starts_is_announced_in_every_server_that_follows_it(
    deps, youtube, servers
):
    """The notice names the channel and the live, links to both, shows when the live
    started (Discord writes it in each reader's time) and is kept to be marked ended
    later."""
    started = NOW - timedelta(minutes=3)
    on_air(youtube, started=started)

    response = await hub_notice(youtube)

    assert response.status_code == 204
    for guild in servers.both:
        [notice] = notices(guild)
        embed = notice.embeds[0]
        assert notice.content.endswith(f"pewdiepie está ao vivo! {LINK}")
        assert (embed.url, embed.timestamp) == (LINK, started)
        assert embed.footer.text == footer("online")
        assert (embed.author.name, embed.author.url) == (
            "PewDiePie", "https://www.youtube.com/@pewdiepie",
        )
        saved = await data().guild_messages.find_message(
            guild.id, guild.text_channels[0].id, youtube_live.FORM, LIVE
        )
        assert saved["message_id"] == str(notice.id)


async def test_two_notices_of_the_same_live_announce_it_once(deps, youtube, servers):
    on_air(youtube)

    await hub_notice(youtube)
    await hub_notice(youtube)

    assert [len(notices(guild)) for guild in servers.both] == [1, 1]


async def test_a_live_that_outlives_its_claim_is_not_announced_again(
    deps, youtube, servers
):
    """A live on air for weeks (a 24/7 stream) outlives the 7 days its claim is kept:
    a notice after that finds the message already posted and posts nothing more."""
    on_air(youtube)
    await hub_notice(youtube)
    deps.redis_client.delete(Commands.REDIS_YOUTUBE_LIVE_CLAIM.format(video_id=LIVE))

    await hub_notice(youtube)

    assert [len(notices(guild)) for guild in servers.both] == [1, 1]


async def test_a_live_youtube_does_not_show_yet_is_announced_when_the_hub_retries(
    deps, youtube, servers
):
    """Broke as: the webhook answered 204 for a video the Data API could not see once
    its notice came more than 5 minutes after the video was published, so a private
    scheduled live made public when it started, while the API still lagged, was
    dropped and never announced.

    Guaranteed: a notice whose video YouTube does not show yet is delivered again by
    the hub however old the video is, and the live is announced once YouTube shows
    it."""
    body = atom(LIVE, edited_after=timedelta(hours=3))

    first = notify(youtube, body, sign(body))
    on_air(youtube)
    retry = notify(youtube, body, sign(body))
    await settle()

    assert (first.status_code, retry.status_code) == (503, 204)
    assert [len(notices(guild)) for guild in servers.both] == [1, 1]


async def test_a_scheduled_live_waits_for_its_time_and_is_announced_once_then(
    deps, youtube, servers
):
    """Nothing is posted when the live is scheduled, nothing is read before its time,
    and the hub's own notice of the start does not post it a second time."""
    starts = NOW + timedelta(hours=2)
    scheduled(youtube, starts)

    assert (await hub_notice(youtube)).status_code == 204
    saved = await schedule_of()
    assert (saved["due_at"], saved["attempt"]) == (starts, 0)
    await hand_due_schedules(starts - timedelta(seconds=1))
    assert youtube.google.reads.count("videos") == 1, "only the notice's own read"

    on_air(youtube, started=starts)
    await hand_due_schedules(starts)
    await hub_notice(youtube)

    assert [len(notices(guild)) for guild in servers.both] == [1, 1]
    assert await schedule_of() is None


async def test_a_later_notice_of_a_scheduled_live_moves_its_time(deps, youtube, servers):
    scheduled(youtube, NOW + timedelta(hours=1))
    await hub_notice(youtube)
    moved = NOW + timedelta(hours=3)
    scheduled(youtube, moved)

    await hub_notice(youtube)

    saved = await schedule_of()
    assert (saved["due_at"], saved["attempt"]) == (moved, 0)


async def test_a_live_youtube_moved_later_waits_for_its_new_time(deps, youtube, servers):
    first = NOW - timedelta(minutes=1)
    scheduled(youtube, first)
    await hub_notice(youtube)
    moved = NOW + timedelta(hours=1)
    scheduled(youtube, moved)

    await hand_due_schedules(first)

    saved = await schedule_of()
    assert (saved["due_at"], saved["attempt"]) == (moved, 0)
    assert notices(servers.first) == []


async def test_a_late_live_is_read_again_six_times_then_given_up(deps, youtube, servers):
    """Past its time and still to come, a live is read again 2, 5, 10, 20, 40 and 80
    minutes after it, once a step, and then forgotten."""
    starts = NOW - timedelta(minutes=1)
    scheduled(youtube, starts)
    await hub_notice(youtube)
    reads = youtube.google.reads.count("videos")

    due_times = []
    for _step in Commands.YOUTUBE_LIVE_RETRY_MINUTES:
        await hand_due_schedules((await schedule_of())["due_at"])
        due_times.append((await schedule_of())["due_at"])
    await hand_due_schedules(due_times[-1])

    assert due_times == [
        starts + timedelta(minutes=minutes)
        for minutes in Commands.YOUTUBE_LIVE_RETRY_MINUTES
    ]
    assert await schedule_of() is None
    assert youtube.google.reads.count("videos") - reads == 7
    assert notices(servers.first) == []


@pytest.mark.parametrize("ending", ["finished", "gone", "a video"])
async def test_a_due_live_that_is_over_gone_or_no_live_is_dropped(
    deps, youtube, servers, ending
):
    starts = NOW - timedelta(minutes=1)
    scheduled(youtube, starts)
    await hub_notice(youtube)
    if ending == "finished":
        finished(youtube, starts, NOW)
    elif ending == "gone":
        del youtube.google.videos[LIVE]
    else:
        youtube.google.lives.pop(LIVE)

    await hand_due_schedules(starts)

    assert await schedule_of() is None
    assert notices(servers.first) == []


@pytest.mark.parametrize("count, reads", [(3, 1), (51, 2)])
async def test_the_due_lives_are_read_together_fifty_at_a_time(
    deps, youtube, servers, count, reads
):
    starts = NOW + timedelta(hours=1)
    for index in range(count):
        scheduled(youtube, starts, video_id=f"live-{index}")
        await hub_notice(youtube, f"live-{index}")
    before = youtube.google.reads.count("videos")

    for index in range(count):
        on_air(youtube, video_id=f"live-{index}")
    await hand_due_schedules(starts)

    assert youtube.google.reads.count("videos") - before == reads
    assert await data().keiko_schedules.find_schedules_by_kind("youtube_live") == []


async def test_youtube_not_answering_leaves_the_due_lives_for_their_next_step(
    deps, youtube, servers, caplog
):
    """A pass YouTube fails keeps every due live for a step later and says so once;
    a pass past that step announces them."""
    starts = NOW + timedelta(hours=1)
    for video_id in ("live-1", "live-2"):
        scheduled(youtube, starts, video_id=video_id)
        await hub_notice(youtube, video_id)
        on_air(youtube, video_id=video_id)
    youtube.google.outages["videos"] = 1

    with caplog.at_level(logging.WARNING):
        await hand_due_schedules(starts)

    left = await data().keiko_schedules.find_schedules_by_kind("youtube_live")
    assert len(left) == 2
    told = [record for record in caplog.records if "scheduled youtube lives" in record.getMessage()]
    assert len(told) == 1, "one line for the pass, not one per live"
    await hand_due_schedules(starts)
    assert [len(notices(guild)) for guild in servers.both] == [2, 2]


@pytest.mark.parametrize("outage", ["videos", "channels"], ids=["youtube", "its-channel"])
async def test_a_due_live_youtube_keeps_failing_on_waits_a_step_longer_each_time(
    deps, youtube, servers, outage
):
    """A day YouTube fails does not read and log a due live on every pass: each
    failed pass moves it up the steps of a late live, 2 to 80 minutes from the
    failure, nothing is read in between, and past the last step it is given up."""
    starts = NOW + timedelta(hours=1)
    scheduled(youtube, starts)
    await hub_notice(youtube)
    on_air(youtube)
    youtube.google.outages[outage] = len(Commands.YOUTUBE_LIVE_RETRY_MINUTES) + 1

    waits, due_at = [], starts
    for _step in Commands.YOUTUBE_LIVE_RETRY_MINUTES:
        failed_at = datetime.now(timezone.utc)
        await hand_due_schedules(due_at)
        saved = await schedule_of()
        due_at = saved["due_at"]
        waits.append((saved["attempt"], round((due_at - failed_at) / timedelta(minutes=1))))
        reads = len(youtube.google.reads)
        await hand_due_schedules(failed_at)
        assert len(youtube.google.reads) == reads, "nothing is read before its step"
    await hand_due_schedules(due_at)

    assert waits == [
        (attempt, minutes)
        for attempt, minutes in enumerate(Commands.YOUTUBE_LIVE_RETRY_MINUTES, start=1)
    ]
    assert await schedule_of() is None
    assert notices(servers.first) == []


async def test_the_end_of_an_announced_live_marks_its_notices_ended_with_how_long_it_ran(
    deps, youtube, servers
):
    started = NOW - timedelta(hours=2, minutes=5)
    on_air(youtube, started=started)
    await hub_notice(youtube)
    finished(youtube, started, started + timedelta(hours=2))

    assert (await hub_notice(youtube)).status_code == 204

    for guild in servers.both:
        [notice] = notices(guild)
        assert notice.embeds[0].footer.text == footer("offline", duration="2h")


async def test_a_finished_live_is_ended_once_however_many_notices_follow(
    deps, youtube, servers
):
    """Every later edit of a finished live arrives as its end again: after the first,
    each reads nothing more than its video and edits no notice."""
    started = NOW - timedelta(hours=2, minutes=5)
    on_air(youtube, started=started)
    await hub_notice(youtube)
    finished(youtube, started, started + timedelta(hours=2))
    await hub_notice(youtube)
    reads = len(youtube.google.reads)

    assert (await hub_notice(youtube)).status_code == 204

    assert youtube.google.reads[reads:] == ["videos"]
    for guild in servers.both:
        [notice] = notices(guild)
        assert notice.embeds[0].footer.text == footer("offline", duration="2h")


async def test_the_end_of_a_live_keiko_never_announced_changes_nothing(
    deps, youtube, servers
):
    """Any later edit of an old finished live arrives as its end too: it reads
    nothing more than its video and posts or edits nothing."""
    finished(youtube, NOW - timedelta(days=30, hours=2), NOW - timedelta(days=30))

    response = await hub_notice(youtube)

    assert response.status_code == 204
    assert youtube.google.reads == ["videos"]
    assert notices(servers.first) == [] and notices(servers.second) == []
