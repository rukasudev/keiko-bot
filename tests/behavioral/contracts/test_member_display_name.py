"""Keiko keeps a copy of each birthday member's display name, and never trusts it.

Decided in the database plan: `guild.members` holds `state.display_name` next to the
birthday. The `/birthday` command, the admin add and the birthday post refresh it; it is
read only where the member cannot be fetched (the log of a post for a member who left),
so the panels keep their mentions and the goldens do not change.

Guaranteed: each of the three writers stores the name Discord shows; a post refreshes a
name that changed; a member Discord no longer has is named by the stored copy in the
log, and gets no post.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.constants import Commands
from app.core.servers import set_feature_enabled
from app.services.reminders_birthdays import find_birthday, save_birthday_config
from app.services import reminders_birthdays as birthdays_service
from app.webhooks import birthday_handler
from tests.behavioral.golden.paths.reminders_birthday import (
    _complete_member_card,
    _guild,
    _seed_config,
)
from tests.behavioral.regressions.test_birthday_personal_outcomes import (
    GUILD_ID as PERSONAL_GUILD,
)
from tests.behavioral.regressions.test_birthday_personal_outcomes import (
    USER_ID as PERSONAL_USER,
)
from tests.behavioral.regressions.test_birthday_personal_outcomes import (
    enabled,  # noqa: F401
    interaction_for,
    run,
)
from tests.generators.birthdays import saved_birthday
from tests.generators.moderations import recorded_server
from tests.mocks.discord import create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.shared_contract("birthday_reminders"),
]

GUILD = 1001
TIMEZONE = "America/Sao_Paulo"


def display_name(guild_id, user_id):
    return find_birthday(str(guild_id), str(user_id))["state"].get(
        "display_name"
    )


async def test_the_birthday_command_keeps_the_members_name(enabled):  # noqa: F811
    await run(interaction_for(), 3, 14)

    assert display_name(PERSONAL_GUILD, PERSONAL_USER) == "Ana"


async def test_the_admin_add_keeps_the_picked_members_name(scenario_factory, deps):
    _seed_config(deps)
    scenario = await scenario_factory(locale="pt-br", guild=_guild()).start_command(
        "reminders_birthday"
    )
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await _complete_member_card(scenario, "pt-br")

    assert display_name(scenario.guild.id, 777) == "Amiga"


@pytest.fixture
def birthday_server(deps, monkeypatch):
    class Clock:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 12, 8, 0, tzinfo=ZoneInfo(TIMEZONE)).astimezone(tz)

    guild = create_guild(id=GUILD, channels=["birthdays"])
    recorded_server(deps.mongo_client, GUILD, timezone=TIMEZONE)
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == GUILD else None
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    monkeypatch.setattr(birthday_handler, "datetime", Clock)
    save_birthday_config(str(GUILD), str(guild.text_channels[0].id), False)
    set_feature_enabled(str(GUILD), Commands.REMINDERS_BIRTHDAY_KEY, True)
    return guild


async def test_a_post_refreshes_a_name_that_changed(birthday_server):
    create_member(birthday_server, id=555, name="Bea")
    saved_birthday(str(GUILD), "555", "05-12", reminder_id="4001", display_name="Beatriz")

    await birthday_handler.process_birthday_webhook("4001", "05-12")

    assert len(birthday_server.text_channels[0]._sent_messages) == 1
    assert display_name(GUILD, 555) == "Bea"


async def test_a_member_discord_no_longer_has_is_named_by_the_copy(
    birthday_server, caplog
):
    saved_birthday(str(GUILD), "404", "05-12", reminder_id="4001", display_name="Ana")

    with caplog.at_level("INFO"):
        await birthday_handler.process_birthday_webhook("4001", "05-12")

    assert birthday_server.text_channels[0]._sent_messages == []
    assert "Member not found: Ana" in caplog.text


def test_a_save_without_a_name_keeps_the_stored_one(deps):
    saved_birthday("1", "555", "05-12", display_name="Ana")
    recorded_server(deps.mongo_client, "1", timezone=TIMEZONE)
    save_birthday_config("1", "9", False)

    birthdays_service.upsert_birthday("1", "555", "06-01")

    assert display_name(1, 555) == "Ana"
