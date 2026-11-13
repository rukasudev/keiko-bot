"""`/admin configs` changes the channel Keiko uses now and after a restart, and shows it.

Reported in the database PR 1 inventory: `/admin configs update` saved the id as text
(`"ID" in config` never matched the lower-case keys) and set a lower-case attribute
nobody reads, so the running Keiko kept logging to the old channel until a restart;
`/admin configs show` called `bot.config.get_db_configs()`, which does not exist, so the
operator got no answer.

Shared behaviour: `update_admin_configs` and `get_admin_configs` (`app/services/admin.py`)
over keiko.settings (`app/data/keiko/settings.py`), and `AppConfig.load_db_configs`.
Exposed by: the inventory of the writers of `configs.admin`.

Guaranteed: an updated operator id is a number in the running config at once and in the
saved settings a restart reads; `show` answers with what is saved and what runs.
"""

from types import SimpleNamespace

import pytest

from app.config import AppConfig
from tests.mocks.discord import MockInteraction, create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

OPERATOR = {
    "admin_guild_id": 10,
    "admin_reports_channel_id": 11,
    "admin_logs_channel_id": 12,
    "admin_logs_command_call_id": 13,
    "admin_logs_error_channel_id": 14,
    "admin_logs_files_channel_id": 15,
    "admin_dump_channel_id": 16,
}


@pytest.fixture
def keiko(deps):
    deps.mongo_client.keiko.settings.insert_one({
        "_id": "keiko",
        "options": {
            "presence": {"status": "online", "activity": 0, "description": "/help"},
            "prefix": "ke!",
            "owner_id": None,
            "operator": dict(OPERATOR),
        },
    })
    deps.mongo_client.keiko.integrations.insert_one(
        {"_id": "notion", "options": {"enabled": False}}
    )
    config = AppConfig.__new__(AppConfig)
    config.load_db_configs()
    return SimpleNamespace(config=config)


def _cog(bot):
    from app.cogs.admin.configs import Configs

    cog = Configs.__new__(Configs)
    cog.bot = bot
    return Configs, cog


def _interaction():
    guild = create_guild(id=4242, name="Operator")
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.command = SimpleNamespace(qualified_name="admin configs")
    return interaction


async def test_an_updated_channel_is_the_one_keiko_uses_now_and_after_a_restart(keiko):
    configs, cog = _cog(keiko)

    await configs.update_config.callback(
        cog, _interaction(), config="admin_logs_channel_id", value="999"
    )

    assert keiko.config.ADMIN_LOGS_CHANNEL_ID == 999
    restarted = AppConfig.__new__(AppConfig)
    restarted.load_db_configs()
    assert restarted.ADMIN_LOGS_CHANNEL_ID == 999
    from app.data.keiko.settings import find_keiko_settings

    assert find_keiko_settings()["operator"]["admin_logs_channel_id"] == 999


async def test_an_id_that_is_not_a_number_is_refused_and_nothing_changes(keiko):
    configs, cog = _cog(keiko)
    interaction = _interaction()

    await configs.update_config.callback(
        cog, interaction, config="admin_logs_channel_id", value="the logs"
    )

    assert keiko.config.ADMIN_LOGS_CHANNEL_ID == 12
    assert "not" in interaction.get_last_response()["content"]


async def test_show_answers_with_the_saved_and_the_running_configs(keiko):
    configs, cog = _cog(keiko)
    interaction = _interaction()

    await configs.show_config.callback(cog, interaction)

    content = interaction.get_last_response()["content"]
    saved, running = content.split("Configs in Keiko")
    assert "admin_logs_channel_id: 12" in saved
    assert "ADMIN_LOGS_CHANNEL_ID: 12" in running
