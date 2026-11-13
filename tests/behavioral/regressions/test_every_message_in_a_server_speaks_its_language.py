"""Inside a server, every message of Keiko speaks the server's saved language.

Broke as: PR15 gave the server a language but only the public messages read it;
every answer only its clicker sees (a form, a panel and its side actions, an error,
the help) followed the clicker's Discord, so an admin whose Discord is in English
configured a Portuguese server in English (f01 on the PR15 test page).

Shared behaviour: `interaction_language` (`app/core/servers.py`), the one
helper that sets an interaction's locale to the language Keiko answers it in, and
`speak` (`app/core/utils.py`); the seams that call them before anything reads
the locale: `KeikoTree.interaction_check` (`app/bot.py`) for every app command,
slash or context menu, which keeps the reader's own language for the translation;
the form adapter's opening, clicks and expired sessions
(`app/settings/discord/callbacks.py`), `GenericButton`, `Select`, the greeting's
buttons and `ConfirmActionView`. Consumers: every slash command, every form and
panel, the records browser, the greeting.

Guaranteed: a member whose Discord says one language sees a form, a panel, a side
action, an error, the help and a button or select's answer in the server's other
one, both ways; a translation goes into the reader's own language, told in the
server's; in a DM, and in a server whose settings are not saved yet, Keiko answers
in the person's own language.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.decorators import keiko_admin_only
from app.core.servers import interaction_language
from app.core.utils import ml
from app.settings.form.form_yaml import registry
from tests.generators.moderations import recorded_server
from tests.mocks.discord import (
    MockInteraction,
    create_guild,
    create_member,
    create_message,
    the_tree_checks,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("server_settings"),
]

OTHER = {"pt-br": "en-us", "en-us": "pt-br"}
DISCORD = {"pt-br": "pt-BR", "en-us": "en-US"}
ROLES = {"enabled": True, "default_roles": {"style": "role", "values": ["201"]}}
LANGUAGES = pytest.mark.parametrize("server", ["pt-br", "en-us"])
HELP_TITLE = {"pt-br": "📚 Comandos disponíveis", "en-us": "📚 Available commands"}
LATENCY = {
    "pt-br": "Minha latência atual é de **42 ms** 🐾",
    "en-us": "My current latency is **42 ms** 🐾",
}


def _member_scenario(scenario_factory, deps, server):
    """A member whose Discord speaks the other language, in a server saved in `server`."""
    scenario = scenario_factory(locale=OTHER[server])
    recorded_server(deps.mongo_client, scenario.guild.id, language=server)
    return scenario


@LANGUAGES
async def test_a_form_speaks_the_servers_language(scenario_factory, deps, server):
    scenario = _member_scenario(scenario_factory, deps, server)

    await scenario.start("default_roles")

    intro = registry.get("default_roles").steps[0].title
    assert intro.get(server) in scenario.transcript
    assert intro.get(OTHER[server]) not in scenario.transcript
    assert scenario.session.origin.locale == server


@LANGUAGES
async def test_a_panel_and_its_side_actions_speak_the_servers_language(
    scenario_factory, deps, server
):
    scenario = _member_scenario(scenario_factory, deps, server)
    await scenario.start_manager("default_roles", dict(ROLES))

    await scenario.click(ml("buttons.stats.label", server))
    stats = scenario.expect_message(
        title_contains=ml("commands.commands.commons.stats.title.default-roles", server)
    )
    await scenario.click(ml("buttons.help.label", server))
    help_screen = scenario.expect_message(title_contains=ml("buttons.help.label", server))

    assert stats["ephemeral"] is True and help_screen["ephemeral"] is True
    assert ml("buttons.history.label", OTHER[server]) not in scenario.transcript


@LANGUAGES
async def test_an_error_speaks_the_servers_language(deps, server):
    guild = create_guild(id=4242)
    recorded_server(deps.mongo_client, guild.id, language=server)
    member = create_member(guild, id=555, name="Member")
    member.guild_permissions = discord.Permissions.none()
    interaction = MockInteraction(user=member, guild=guild, locale=DISCORD[OTHER[server]])

    @keiko_admin_only
    async def command(_cog, _interaction):
        pytest.fail("only an admin reaches the command")

    await the_tree_checks(interaction)
    await command(None, interaction)

    answer = interaction.get_last_response()
    assert answer["ephemeral"] is True
    assert answer["embed"].title == ml("buttons.setup.admin-only.title", server)


@LANGUAGES
async def test_the_help_speaks_the_servers_language(deps, server):
    from app.views.greetings import CommandsButton

    guild = create_guild(id=4242)
    recorded_server(deps.mongo_client, guild.id, language=server)
    interaction = MockInteraction(
        user=create_member(guild, id=555), guild=guild, locale=DISCORD[OTHER[server]]
    )
    interaction.client = SimpleNamespace(app_commands=[])
    interaction.edit_original_response = AsyncMock()

    await CommandsButton("📚").callback(interaction)

    assert interaction.get_last_response()["ephemeral"] is True
    drawn = interaction.edit_original_response.await_args.kwargs["embed"]
    assert drawn.title == HELP_TITLE[server]


@LANGUAGES
async def test_a_direct_message_keeps_the_persons_own_language(server):
    person = SimpleNamespace(guild_id=None, locale=discord.Locale(DISCORD[server]))

    assert await interaction_language(person) == server
    assert person.locale == discord.Locale(DISCORD[server])


def _ping(deps):
    from app.cogs.base.ping import Ping

    deps.bot.latency = 0.0421
    return Ping.ping.callback, Ping(deps.bot)


async def _slash_ping(deps, interaction):
    callback, cog = _ping(deps)
    interaction.command = None
    await the_tree_checks(interaction)
    await callback(cog, interaction)
    return interaction.get_last_response()["embed"].description


@LANGUAGES
async def test_a_slash_command_answers_in_the_servers_language(deps, server):
    guild = create_guild(id=4242)
    recorded_server(deps.mongo_client, guild.id, language=server)
    interaction = MockInteraction(
        user=create_member(guild, id=555), guild=guild, locale=DISCORD[OTHER[server]]
    )

    assert await _slash_ping(deps, interaction) == LATENCY[server]


@LANGUAGES
async def test_a_direct_message_command_answers_in_the_persons_own_language(deps, server):
    interaction = MockInteraction(
        user=create_member(create_guild(), id=555), guild=create_guild(), locale=DISCORD[server]
    )
    interaction.guild = None
    interaction.guild_id = None

    description = await _slash_ping(deps, interaction)

    assert description == LATENCY[server]


@LANGUAGES
async def test_a_server_whose_settings_are_not_saved_yet_hears_the_persons_language(
    deps, server
):
    """The seconds between Keiko joining and saving its guess: no settings to read."""
    guild = create_guild(id=4343)
    interaction = MockInteraction(
        user=create_member(guild, id=555), guild=guild, locale=DISCORD[server]
    )

    description = await _slash_ping(deps, interaction)

    assert description == LATENCY[server]


@LANGUAGES
async def test_a_translation_goes_into_the_readers_language_told_in_the_servers(
    deps, monkeypatch, server
):
    from app.cogs.base.translate import Translate
    from app.integrations.google_translate import GoogleTranslate

    guild = create_guild(id=4242)
    recorded_server(deps.mongo_client, guild.id, language=server)
    reader = discord.Locale(DISCORD[OTHER[server]])
    interaction = MockInteraction(
        user=create_member(guild, id=555), guild=guild, locale=DISCORD[OTHER[server]]
    )
    asked = []

    def translate(content, locale):
        asked.append(locale)
        return {"src": "🇧🇷", "dest": "🇺🇸", "original": content, "translated_message": "hi"}

    monkeypatch.setattr(GoogleTranslate, "translate", staticmethod(translate))
    message = create_message(content="oi", author=interaction.user, channel=guild.text_channels[0])
    message.jump_url = "https://discord.com/channels/1/2/3"

    await the_tree_checks(interaction)
    await Translate.translate_message(SimpleNamespace(), interaction, message)

    assert asked == [reader]
    title = interaction.get_last_response()["embed"].title
    assert title == ml("commands.commands.translate-message.response.title", server)


def _member_interaction(deps, server):
    guild = create_guild(id=4242)
    recorded_server(deps.mongo_client, guild.id, language=server)
    return MockInteraction(
        user=create_member(guild, id=555), guild=guild, locale=DISCORD[OTHER[server]]
    )


async def _answer_in_its_locale(interaction):
    await interaction.response.send_message(ml("buttons.help.label", interaction.locale))


@LANGUAGES
async def test_a_generic_button_answers_in_the_servers_language(deps, server):
    from app.components.buttons import GenericButton

    interaction = _member_interaction(deps, server)
    button = GenericButton("Go", _answer_in_its_locale, discord.ButtonStyle.grey)

    await button.callback(interaction)

    assert interaction.get_last_response()["content"] == ml("buttons.help.label", server)


@LANGUAGES
async def test_a_select_answers_in_the_servers_language(deps, server):
    from app.components.select import Select

    interaction = _member_interaction(deps, server)
    select = Select("Pick", {"first": "First"}, custom_callback=_answer_in_its_locale)
    discord.ui.View().add_item(select)
    select._values = ["first"]

    await select.callback(interaction)

    assert interaction.get_last_response()["content"] == ml("buttons.help.label", server)


@LANGUAGES
async def test_a_confirmation_answers_in_the_language_its_screen_was_drawn_in(deps, server):
    from app.views.confirm_action import ConfirmActionView

    interaction = _member_interaction(deps, server)
    view = ConfirmActionView(_answer_in_its_locale, server)

    await view.confirm.callback(interaction)

    assert view.confirm.label == ml("buttons.confirm.label", server)
    assert interaction.get_last_response()["content"] == ml("buttons.help.label", server)


async def test_a_failed_language_read_reaches_the_error_handler_even_without_a_command(
    monkeypatch,
):
    """When the server's language cannot be read, the tree hands the error report an
    AppCommandError, also for an interaction that names no command (it used to raise
    an AttributeError while building the wrapper, and the person got no answer)."""
    from app.core import servers
    from tests.mocks.discord import _keiko

    async def broken(_interaction):
        raise RuntimeError("mongo is down")

    monkeypatch.setattr(servers, "interaction_language", broken)
    interaction = SimpleNamespace(extras={}, locale=discord.Locale("pt-BR"), command=None)

    with pytest.raises(discord.app_commands.AppCommandError, match="mongo is down"):
        await _keiko().tree.interaction_check(interaction)
