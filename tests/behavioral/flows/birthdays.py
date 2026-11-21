"""Birthday reminders: the server with a second member, the saved settings and
birthdays, the cards' steps (the settings, a member's month and day), the setup
walked whole, and `/birthday` called by a member on a server of its own."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.commands.birthdays import service as birthdays_service
from app.commands.birthdays.service import save_birthday_config
from app.core.servers import set_feature_enabled
from app.data import wait
from tests.behavioral.flows.common import GUILD_ID
from tests.generators.birthdays import saved_birthday
from tests.generators.moderations import recorded_server
from tests.mocks.discord import create_guild, create_member

FORM = "reminders_birthday"

DAY_FIELD = {"pt-br": "Dia", "en-us": "Day"}


def birthday_guild():
    """A server with a second member, Amiga (777), beside the admin."""
    guild = create_guild()
    create_member(guild, id=777, name="Amiga", roles=["Member"])
    return guild


async def seed_config(deps, enabled=True, members=("555",)):
    """The settings saved, a birthday for each of `members`, the feature on or off."""
    await save_birthday_config(
        GUILD_ID,
        "100",
        "none",
        notification_time="08:00",
    )
    for index, user_id in enumerate(members):
        saved_birthday(GUILD_ID, user_id, f"{5 + index:02d}-12")
    await set_feature_enabled(GUILD_ID, FORM, enabled)


async def open_setup(scenario_factory, locale):
    """The command opened with nothing saved: the setup's intro."""
    return await scenario_factory(locale=locale, guild=birthday_guild()).start_command(
        FORM
    )


async def open_manager(scenario_factory, deps, locale, members=("555",)):
    """The settings and `members`' birthdays saved, then the panel opened."""
    await seed_config(deps, members=members)
    return await scenario_factory(locale=locale, guild=birthday_guild()).start_command(
        FORM
    )


async def complete_settings_card(scenario):
    """The settings card filled (channel, time zone, time) and Done."""
    await scenario.select_option("general", target="channel")
    await scenario.select_option("America/Sao_Paulo", target="timezone")
    await scenario.select_option("08:00", target="notification_time")
    await scenario.click("done")


async def type_day(scenario, day, locale="pt-br"):
    """The day typed in the modal of the card's day section."""
    await scenario.click("customize:day")
    await scenario.submit_modal({DAY_FIELD[locale]: day})


async def complete_member_card(scenario, locale, month="05", day="12"):
    """A member's card filled (month and day) and Done."""
    await scenario.select_option(month, target="month")
    await type_day(scenario, day, locale)
    await scenario.click("done")


async def to_review(scenario_factory, locale):
    """The setup walked to its review: the intro confirmed, the settings card filled."""
    scenario = await scenario_factory(locale=locale).start(FORM)
    await scenario.confirm()
    scenario.expect_message(components_v2=True)
    await complete_settings_card(scenario)
    scenario.expect_step("confirm")
    return scenario


async def setup(scenario_factory, locale):
    """The setup walked to its end and saved."""
    scenario = await to_review(scenario_factory, locale)
    await scenario.confirm()
    await scenario.finish()
    return scenario


async def to_member_card_from_the_panel(scenario_factory, scenario, locale):
    """The panel of `scenario`'s server opened again, adding the admin's birthday:
    the member card on screen."""
    panel = await scenario_factory(
        locale=locale, guild=scenario.guild, user=scenario.user
    ).start_command(FORM)
    await panel.click("add")
    await panel.select_option("Tester", target="user")
    return panel


PERSONAL_GUILD_ID = "1532291713580925048"
PERSONAL_USER_ID = 777


@pytest.fixture
def birthday_command_on(deps, monkeypatch):
    """A server where `/birthday` works: its settings saved, the feature on, and the
    reminders API answering."""
    recorded_server(deps.mongo_client, PERSONAL_GUILD_ID, timezone="UTC")
    wait(
        save_birthday_config(
            PERSONAL_GUILD_ID, channel_id="99", notification_time="14:00"
        )
    )
    wait(set_feature_enabled(PERSONAL_GUILD_ID, FORM, True))
    monkeypatch.setattr(
        birthdays_service.reminders_service, "create_reminder", lambda *a, **kw: "1"
    )
    monkeypatch.setattr(
        birthdays_service.reminders_service,
        "cleanup_reminder_if_unused",
        lambda *a, **kw: None,
    )


def personal_interaction(admin=False):
    """A member, Ana (777), calling `/birthday` on the personal server."""
    return SimpleNamespace(
        locale=discord.Locale.brazil_portuguese,
        command=SimpleNamespace(qualified_name="birthday"),
        guild=SimpleNamespace(id=int(PERSONAL_GUILD_ID)),
        guild_id=int(PERSONAL_GUILD_ID),
        user=SimpleNamespace(
            id=PERSONAL_USER_ID,
            display_name="Ana",
            guild_permissions=SimpleNamespace(administrator=admin),
        ),
        response=SimpleNamespace(
            send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def run_birthday_command(interaction, month, day):
    """`/birthday <month> <day>` answered through the cog."""
    from app.commands.birthdays.cog import Birthday

    await Birthday.birthday_personal.callback(SimpleNamespace(), interaction, month, day)
