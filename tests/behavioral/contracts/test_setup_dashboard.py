"""The /setup screen: the first thing most servers see of Keiko.

It was a dense embed: one bold line per feature, the command on the next line,
and a row of buttons far from the lines they belonged to. Its first card
redesign still read as busy: every row said its status three times (a coloured
circle, a word, the button) over three lines with three emoji.

Guaranteed: a Components V2 card with Keiko's picture beside a dog-titled
header of three lines (the title, a one-sentence intro, and a note on what these
features are and where /help lives) so no blank space sits under the picture;
features grouped under "to set up" first and "configured" second, with their
counts, in declared order inside each group; every row is its name plus one
subtext line with what it does and its command, with no status circles; a
paused feature says so in its subtext; the button reads Manage in the first
group and Set up in the second; a server with everything set up sees no second
group; the card stays inside Discord's limits in both locales; a row button
opens the feature through the setup_dashboard source; and the greeting's
dashboard button sends the same card.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.constants import Commands, KeikoIcons
from app.services.utils import ml
from tests.behavioral.contracts.test_components_v2_limits import _assert_within_limits
from tests.behavioral.harness.locators import walk_items

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("setup_dashboard")]

GUILD_ID = "123456789"
BASE = "commands.commands.setup.embed"


def seed(deps, enabled, paused=()):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": GUILD_ID, **{key: True for key in enabled}}
    )
    for key in paused:
        deps.mongo_client.guild[key].insert_one(
            {"guild_id": GUILD_ID, Commands.ENABLED_KEY: False}
        )


@pytest.fixture
def configured(deps):
    """Welcome messages enabled, block links paused, the rest never set up."""
    seed(
        deps,
        enabled=(Commands.WELCOME_MESSAGES_KEY, Commands.BLOCK_LINKS_KEY),
        paused=(Commands.BLOCK_LINKS_KEY,),
    )


async def dashboard(locale="pt-br"):
    from app.views.setup import setup_dashboard

    return await setup_dashboard(GUILD_ID, locale)


def rows(view):
    return [
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Section)
        and isinstance(item.accessory, discord.ui.Button)
    ]


def texts(view):
    return [
        item.content for item in walk_items(view) if isinstance(item, discord.ui.TextDisplay)
    ]


def text_of(section):
    return "\n".join(
        child.content
        for child in section.children
        if isinstance(child, discord.ui.TextDisplay)
    )


def heading(key, count, locale="pt-br"):
    return "### " + ml(f"{BASE}.{key}", locale).replace("{count}", str(count))


def feature(command_key):
    return next(f for f in Commands.SETUP_FEATURES if f["command_key"] == command_key)


def header_of(view):
    return next(
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Section)
        and isinstance(item.accessory, discord.ui.Thumbnail)
    )


async def test_the_setup_screen_is_a_components_v2_card_with_keiko_on_the_right(
    configured,
):
    view = await dashboard()

    assert isinstance(view, discord.ui.LayoutView)
    assert header_of(view).accessory.media.url == KeikoIcons.IMAGE_01


@pytest.mark.parametrize("locale,help_command", [("pt-br", "/ajuda"), ("en-us", "/help")])
async def test_the_header_fills_the_space_beside_keikos_picture(
    configured, locale, help_command
):
    """Reported: with a title and one sentence, the section was shorter than
    the picture and left a blank band under the intro."""
    lines = [child.content for child in header_of(await dashboard(locale)).children]

    assert len(lines) == 3, "title, intro and note fill the picture's height"
    assert lines[0].startswith("## 🐶 "), "Keiko's own emoji leads the title"
    assert lines[1] == ml(f"{BASE}.desc", locale)
    assert lines[2].startswith("-# ") and help_command in lines[2], (
        "the note says where the rest of the commands and the help are"
    )


async def test_features_are_grouped_by_whether_they_are_set_up(configured):
    view = await dashboard()

    headings = [text for text in texts(view) if text.startswith("### ")]
    assert headings == [heading("pending", 4), heading("configured", 2)]
    assert [row.accessory.command_key for row in rows(view)] == [
        Commands.DEFAULT_ROLES_KEY,
        Commands.NOTIFICATIONS_TWITCH_KEY,
        Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        Commands.REMINDERS_BIRTHDAY_KEY,
        Commands.WELCOME_MESSAGES_KEY,
        Commands.BLOCK_LINKS_KEY,
    ], "what is left to set up comes first, each group in declared order"


async def test_every_row_is_its_name_and_one_subtext_line_with_its_command(configured):
    for section in rows(await dashboard()):
        spec = feature(section.accessory.command_key)
        name_line, subtext = text_of(section).split("\n")
        assert ml(f"buttons.setup.{spec['button_key']}.label", "pt-br") in name_line
        assert subtext.startswith("-# "), "what it does reads as quiet subtext"
        assert ml(f"{BASE}.features.{spec['button_key']}", "pt-br") in subtext
        assert "`/" in subtext, "the command still teaches the shortcut"


async def test_no_row_repeats_its_status_as_a_coloured_circle(configured):
    rendered = "\n".join(texts(await dashboard()))

    assert "🔴" not in rendered and "🟢" not in rendered
    assert ml(f"{BASE}.enabled", "pt-br") not in rendered


async def test_a_paused_feature_says_so_in_its_subtext(configured):
    by_key = {row.accessory.command_key: row for row in rows(await dashboard())}

    paused = ml(f"{BASE}.paused", "pt-br")
    assert paused in text_of(by_key[Commands.BLOCK_LINKS_KEY]).split("\n")[1]
    assert paused not in text_of(by_key[Commands.WELCOME_MESSAGES_KEY])


async def test_the_button_reads_manage_when_set_up_and_set_up_otherwise(configured):
    by_key = {row.accessory.command_key: row.accessory for row in rows(await dashboard())}
    start = ml("buttons.setup.start.label", "pt-br")
    manage = ml("buttons.setup.manage.label", "pt-br")

    assert by_key[Commands.DEFAULT_ROLES_KEY].label == start
    assert by_key[Commands.DEFAULT_ROLES_KEY].style is discord.ButtonStyle.success
    assert by_key[Commands.WELCOME_MESSAGES_KEY].label == manage
    assert by_key[Commands.WELCOME_MESSAGES_KEY].style is discord.ButtonStyle.secondary
    assert by_key[Commands.BLOCK_LINKS_KEY].label == manage, "a paused feature is still set up"


async def test_a_server_with_everything_set_up_sees_no_second_group(deps):
    seed(deps, enabled=[spec["command_key"] for spec in Commands.SETUP_FEATURES])

    view = await dashboard()

    headings = [text for text in texts(view) if text.startswith("### ")]
    assert headings == [heading("configured", len(Commands.SETUP_FEATURES))]
    assert ml(f"{BASE}.all-configured", "pt-br") in texts(view)


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_setup_screen_stays_inside_discord_limits_in_both_locales(
    configured, locale
):
    _assert_within_limits(await dashboard(locale), f"/setup {locale}")


async def test_a_row_button_opens_the_feature_through_the_setup_dashboard_source(
    configured, monkeypatch
):
    from app.components import buttons

    opened = AsyncMock()
    monkeypatch.setattr(buttons, "run_feature_command", opened)
    by_key = {row.accessory.command_key: row.accessory for row in rows(await dashboard())}
    interaction = SimpleNamespace()

    await by_key[Commands.DEFAULT_ROLES_KEY].callback(interaction)

    opened.assert_awaited_once_with(
        interaction, Commands.DEFAULT_ROLES_KEY, "setup_dashboard"
    )


async def test_the_greeting_dashboard_button_sends_the_same_screen(configured):
    from app.views.greetings import DashboardButton

    sent = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=True)),
        locale=discord.Locale.brazil_portuguese,
        guild=SimpleNamespace(id=int(GUILD_ID)),
        response=SimpleNamespace(send_message=sent),
    )

    await DashboardButton(label="Configurar", locale="pt-br").callback(interaction)

    kwargs = sent.await_args.kwargs
    assert isinstance(kwargs["view"], discord.ui.LayoutView)
    assert kwargs.get("embed") is None, "a Components V2 message carries no embed"


def action_row_buttons(view):
    rows = [item for item in walk_items(view) if isinstance(item, discord.ui.ActionRow)]
    assert len(rows) == 1, "one row of buttons closes the card"
    return list(rows[0].children)


async def test_the_button_row_sits_below_the_card_not_inside_it(configured):
    view = await dashboard()

    top_level = list(view.children)
    assert [type(item) for item in top_level] == [
        discord.ui.Container,
        discord.ui.ActionRow,
    ], "the card first, then its buttons outside the coloured frame"
    inside = list(walk_items(top_level[0]))
    assert not any(isinstance(item, discord.ui.ActionRow) for item in inside)
    assert inside[-1].content.startswith("-# • "), "the footer still closes the card"


async def test_the_card_ends_with_commands_history_permissions_and_support(configured):
    buttons = action_row_buttons(await dashboard())

    assert [button.label for button in buttons] == [
        ml("buttons.setup.commands.label", "pt-br"),
        ml("buttons.history.label", "pt-br"),
        ml("buttons.setup.permissions.label", "pt-br"),
        ml("buttons.setup.support.label", "pt-br"),
    ]
    support = buttons[-1]
    assert support.style is discord.ButtonStyle.link
    assert support.url == Commands.SUPPORT_SERVER_URL


@pytest.mark.parametrize("position,module,function", [
    (0, "app.services.help", "send_help"),
    (1, "app.services.setup", "send_history"),
    (2, "app.services.setup", "send_permissions"),
])
async def test_each_card_button_opens_its_screen(
    configured, monkeypatch, position, module, function
):
    import importlib

    opened = AsyncMock()
    monkeypatch.setattr(importlib.import_module(module), function, opened)
    interaction = SimpleNamespace()

    await action_row_buttons(await dashboard())[position].callback(interaction)

    assert opened.await_args.args[0] is interaction

