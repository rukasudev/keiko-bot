"""The /admin analytics screens.

The reason these are worth pinning: with fewer than a hundred guilds a
percentage without its population is misleading, and the churn screen exists
precisely to compare two tiny groups. The guard rails are part of the feature.
"""
import pytest

from app.data import analytics as analytics_data
from app.services import admin, admin_analytics, analytics, analytics_sink
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
import discord
from tests.mocks.discord import create_guild, create_member, MockInteraction
from app.constants import Commands

pytestmark = pytest.mark.unit


def flush():
    analytics.flush()


def complete_setup(guild_id, feature, session="s"):
    analytics.emit(
        "feature.setup_opened", guild_id=guild_id, user_id="9", feature=feature,
        source="slash", session_id=session,
    )
    analytics.emit(
        "setup.completed", guild_id=guild_id, user_id="9", feature=feature,
        source="slash", session_id=session, duration_ms=1000, steps_viewed=3,
    )


def test_the_funnel_screen_shows_absolute_numbers_next_to_percentages():
    complete_setup("1", "block_links")
    analytics.record_value("1", "block_links")
    flush()

    embed = admin_analytics.build_funnel_embed("block_links")
    assert "Setup opened** — 1" in embed.description
    assert "Setup completed** — 1 (100%)" in embed.description
    assert "Delivered value** — 1 (100%)" in embed.description


def test_the_funnel_screen_survives_a_feature_nobody_touched():
    embed = admin_analytics.build_funnel_embed("welcome_messages")
    assert "Setup opened** — 0" in embed.description


def test_the_friction_screen_ranks_the_steps_that_reject_people():
    for _ in range(3):
        analytics.emit(
            "setup.validation_failed", guild_id="2", user_id="9",
            feature="block_links", session_id="s", step_key="custom_link",
            validation="validate_link", error_key="link-not-recognized",
        )
    analytics.emit(
        "setup.validation_failed", guild_id="2", user_id="9",
        feature="reminders_birthday", session_id="s2", step_key="date",
        validation="validate_date", error_key="invalid-date",
    )
    flush()

    embed = admin_analytics.build_friction_embed()
    assert embed.description.index("custom_link") < embed.description.index("date")
    assert "link-not-recognized" in embed.description


def test_the_churn_screen_refuses_to_read_a_tiny_population_as_a_conclusion():
    complete_setup("3", "block_links")
    analytics.emit("guild.removed", guild_id="3", days_in_guild=5)
    flush()

    embed = admin_analytics.build_churn_embed()
    assert "N=1" in embed.description
    assert "too small" in embed.description


def test_the_churn_screen_never_claims_a_cause():
    embed = admin_analytics.build_churn_embed()
    assert "correlation, never cause" in embed.description


def test_the_guild_screen_tells_the_story_in_order():
    analytics.emit("guild.joined", guild_id="4", returning=False)
    analytics.emit(
        "guild.greeting_sent", guild_id="4", features_offered=4, channel_found=True
    )
    complete_setup("4", "welcome_messages")
    flush()

    embed = admin_analytics.build_guild_journey_embed("4")
    description = embed.description
    assert description.index("guild.joined") < description.index("guild.greeting_sent")
    assert description.index("guild.greeting_sent") < description.index("setup.completed")


def test_the_guild_screen_flags_a_greeting_nobody_could_see():
    analytics.emit(
        "guild.greeting_sent", guild_id="5", features_offered=4, channel_found=False
    )
    flush()

    embed = admin_analytics.build_guild_journey_embed("5")
    assert "no writable channel" in embed.description


def test_the_abandoned_screen_lists_configured_but_dead_features():
    complete_setup("6", "welcome_messages")
    flush()
    profile = analytics_data.find_profile("6")
    profile["features"]["welcome_messages"]["completed_at"] = (
        profile["features"]["welcome_messages"]["completed_at"].replace(year=2020)
    )

    embed = admin_analytics.build_abandoned_embed()
    assert "welcome_messages" in embed.description


def test_forgetting_a_guild_removes_every_trace_of_it():
    complete_setup("7", "block_links")
    analytics.record_value("7", "block_links")
    flush()

    assert analytics_data.find_profile("7")
    deleted = analytics_data.delete_analytics_by_guild("7")

    assert deleted["profile"] == 1
    assert analytics_data.find_profile("7") is None
    assert analytics_data.find_events_by_guild("7") == []
    assert analytics_data.find_month_buckets("7") == []


def test_forgetting_one_guild_leaves_the_others_alone():
    complete_setup("8", "block_links")
    complete_setup("9", "block_links")
    flush()

    analytics_data.delete_analytics_by_guild("8")

    assert analytics_data.find_profile("9")
    assert analytics_data.find_events_by_guild("9")


def test_every_section_of_insights_builds_without_data(deps):
    """The menu must open on a fresh install, not only once data exists."""
    for section in admin_analytics.insight_sections():
        embed = section["build"]()
        assert embed.title
        assert embed.description


def test_the_sections_cover_the_commands_that_were_collapsed():
    keys = {section["key"] for section in admin_analytics.insight_sections()}
    assert {"dropoff", "friction", "abandoned", "funnel", "churn", "pipeline"} <= keys
    assert {"config", "timing"} <= keys, "the two new reports must be reachable"


def test_the_funnel_section_lists_every_feature_that_had_activity():
    complete_setup("20", "block_links")
    complete_setup("21", "welcome_messages")
    flush()

    description = admin_analytics.build_all_funnels_embed().description
    assert "block_links" in description
    assert "welcome_messages" in description
    assert "notifications_twitch" not in description


def test_the_settings_section_names_what_nobody_fills(deps):
    for index in range(6):
        deps.mongo_client.guild["welcome_messages"].insert_one({
            "guild_id": str(index),
            "welcome_messages_channel": {"values": ["1"]},
            "welcome_custom_image": None,
        })

    description = admin_analytics.build_config_usage_embed().description
    assert "welcome_custom_image" in description
    assert "0** of **6" in description


def test_the_timing_section_states_what_the_number_is_not():
    analytics.emit(
        "setup.step_completed", guild_id="1", user_id="9", feature="block_links",
        source="slash", session_id="s", step_key="answer", step_action="modal",
        ms_on_step=45000,
    )
    flush()

    embed = admin_analytics.build_step_timing_embed()
    assert "My response when blocking" in embed.description, (
        "reports must name the step the user saw, not its YAML key"
    )
    assert "not attention" in embed.description


def test_the_pipeline_screen_reports_what_was_dropped():
    analytics.emit("nope.not_real", value=1)
    embed = admin_analytics.build_pipeline_embed()
    assert "Unknown events: **1**" in embed.description


class Cache(list):
    """Discord's cache of servers, noting the thread of everyone who walks it."""

    def __init__(self, guilds, walkers):
        super().__init__(guilds)
        self.walkers = walkers

    def __iter__(self):
        self.walkers.append(threading.current_thread())
        return super().__iter__()


@pytest.fixture
def stores(monkeypatch):
    monkeypatch.setattr(admin, "mongo_client", SimpleNamespace(guild=SimpleNamespace(
        moderations=SimpleNamespace(aggregate=lambda pipeline: [])
    )))
    monkeypatch.setattr(admin, "redis_client", SimpleNamespace(
        scan_iter=lambda pattern: [], get=lambda key: None, dbsize=lambda: 0
    ))
    monkeypatch.setattr(admin, "build_product_summary", lambda: "")


@pytest.mark.behavioral
@pytest.mark.regression
async def test_the_overview_walks_discords_cache_only_on_the_loop(deps, stores):
    """`/admin overview` reads Discord's cache only on the event loop that owns it.

    Reported in the v1 review of the loop and runtime PR: once the overview moved off the
    loop, the whole of it ran on a `keiko-io` thread, its walk over `bot.guilds` and each
    guild's channels included. discord.py changes that cache from the loop, so a server
    joining or a channel created during the walk could stop it with "dictionary changed size
    during iteration".

    Shared behaviour: `get_overview_data` (`app/services/admin.py`), behind `/admin overview`.
    Exposed by: the review of what moved to a thread.

    Guaranteed: the overview counts servers, members and channels on the loop, and hands only
    the Mongo, Redis and Twitch reads to a thread.
    """
    from app.cogs.admin import Admin

    walkers = []
    guild = create_guild(id=4242, name="Busy server")
    server = SimpleNamespace(
        id=4242, name="Busy server", member_count=10, text_channels=[], voice_channels=[]
    )
    bot = SimpleNamespace(
        ready_time=datetime.now() - timedelta(hours=1),
        latency=0.05,
        status=discord.Status.online,
        activity=SimpleNamespace(name="Keiko"),
        guilds=Cache([server], walkers),
        extensions={},
        get_guild=lambda guild_id: None,
        twitch=SimpleNamespace(get_subscriptions=lambda: {"data": []}),
        config=SimpleNamespace(ENVIRONMENT="test"),
    )
    cog = Admin.__new__(Admin)
    cog.bot = bot
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.command = SimpleNamespace(qualified_name="admin overview")

    await Admin.show_overview.callback(cog, interaction)

    loop = threading.current_thread()
    assert walkers and all(walker is loop for walker in walkers), [w.name for w in walkers]
    assert interaction.get_last_response()["type"] == "followup_send"


STAYED = "6001"


LEFT = "6002"


FLAGS_AS_MONGO_SUMS_THEM = {
    "guild_status": [{"_id": True, "count": 1}],
    "top_owner": [],
    "feature_adoption": [{
        "_id": None,
        Commands.BLOCK_LINKS_KEY: 1,
        Commands.DEFAULT_ROLES_KEY: 1,
        Commands.WELCOME_MESSAGES_KEY: 0,
    }],
    "newest_guild": [],
    "monthly_growth": [],
}


def server(guild_id):
    return SimpleNamespace(
        id=int(guild_id), name=f"Server {guild_id}", member_count=10,
        text_channels=[], voice_channels=[],
    )


@pytest.fixture
def bot():
    def no_twitch():
        raise RuntimeError("Twitch is not reached offline")

    return SimpleNamespace(
        ready_time=datetime.now() - timedelta(hours=1),
        latency=0.05,
        status=discord.Status.online,
        activity=SimpleNamespace(name="Keiko"),
        guilds=[server(STAYED)],
        extensions={},
        get_guild=lambda guild_id: None,
        twitch=SimpleNamespace(get_subscriptions=no_twitch),
    )


@pytest.fixture
def adoption_stores(deps, monkeypatch):
    monkeypatch.setattr(admin, "mongo_client", SimpleNamespace(guild=SimpleNamespace(
        moderations=SimpleNamespace(aggregate=lambda pipeline: [FLAGS_AS_MONGO_SUMS_THEM])
    )))
    monkeypatch.setattr(admin, "redis_client", SimpleNamespace(
        scan_iter=lambda pattern: [], get=lambda key: None, dbsize=lambda: 0
    ))
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": STAYED,
        "is_bot_online": True,
        Commands.BLOCK_LINKS_KEY: True,
        Commands.DEFAULT_ROLES_KEY: True,
        Commands.WELCOME_MESSAGES_KEY: False,
    })
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": LEFT, "is_bot_online": True, Commands.WELCOME_MESSAGES_KEY: True}
    )
    feature = deps.mongo_client.guild
    feature[Commands.BLOCK_LINKS_KEY].insert_one({"guild_id": STAYED, "enabled": False})
    feature[Commands.DEFAULT_ROLES_KEY].insert_one({"guild_id": STAYED})
    feature[Commands.WELCOME_MESSAGES_KEY].insert_one({"guild_id": STAYED, "enabled": True})
    feature[Commands.WELCOME_MESSAGES_KEY].insert_one({"guild_id": LEFT, "enabled": True})


async def test_a_feature_counts_where_its_saved_settings_say_on_in_a_server_keiko_is_in(
    bot, adoption_stores
):
    """The overview's feature adoption counts a feature as on by the record the bot runs on.

    Reported in the v1 review: `/admin overview` summed the old moderations flags of the
    servers marked online, which wave 1 stopped trusting (`is_feature_on` is the one source
    of "on"). A feature disabled while its flag stayed on was counted, one saved without the
    flag (StreamElements, welcome after the v1 migration) was not, and a server Keiko had
    left but whose record still said online counted too.

    Shared behaviour: `get_overview_data` (`app/services/admin.py`). Exposed by: the wave 1
    review of every reader of the old flags.

    Guaranteed: each feature counts the servers Keiko is in whose saved settings
    `is_feature_on` calls on, a document saved before `enabled` following its old flag.
    The Mongo aggregation the overview runs cannot run offline, so the test answers it the
    way Mongo does for the flags below and checks the adoption does not come from it.
    """
    adoption = (await admin.get_overview_data(bot))["feature_adoption"]

    assert adoption[Commands.WELCOME_MESSAGES_KEY] == 1, "on without the old flag, once"
    assert adoption[Commands.BLOCK_LINKS_KEY] == 0, "disabled while its flag stayed on"
    assert adoption[Commands.DEFAULT_ROLES_KEY] == 1, "saved before `enabled`: its flag"


async def test_the_overview_reads_two_fields_of_each_feature_document(
    bot, adoption_stores, deps, monkeypatch
):
    """A feature document holds whole lists (links, birthdays, messages); the adoption
    needs to know only whose it is and whether it is on."""
    asked = []
    for key in Commands.COMMANDS_LIST:
        collection = deps.mongo_client.guild[key]
        monkeypatch.setattr(
            collection, "find",
            lambda query, projection, read=collection.find: (asked.append(projection), read(query))[1],
        )

    await admin.get_overview_data(bot)

    assert asked and all(
        projection == {"_id": False, "guild_id": True, "enabled": True} for projection in asked
    )
