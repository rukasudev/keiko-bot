"""Keiko's shared screen parts (`app/ui`): the grey Back every screen draws, the
closing line an embed ends with, and the preview a notice or a welcome opens.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.constants import Style
from app.core.commands import setup_features
from app.core.utils import ml
from app.forms.discord.views import embed_of
from app.forms.form.components import Field, Screen
from app.ui.buttons import BackButton
from app.ui.embed import standard_footer
from app.ui.message_preview import MessagePreviewView
from tests.behavioral.flows import block_links, roles
from tests.behavioral.harness.locators import walk_items
from tests.generators.moderations import recorded_server
from tests.mocks.database import fake_store
from tests.mocks.discord import MockInteraction, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("ui"),
]


GUILD_ID = "123456789"


def _backs(view, locale):
    back = ml("buttons.back.label", locale)
    return [
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Button) and item.label == back
    ]


def _assert_grey(view, locale, where):
    backs = _backs(view, locale)
    assert backs, f"{where}: no Voltar on screen"
    assert {item.style for item in backs} == {discord.ButtonStyle.secondary}, where


def _rows(count):
    return [(spec, "pending") for spec in setup_features()[:count]]


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
@pytest.mark.parametrize("pages", [1, 2])
def test_the_voltar_of_a_category_page_is_grey(locale, pages):
    """Regression: every Voltar Keiko draws is grey.

    What broke: the Voltar of a /configurar category page was blue (primary)
    whenever the page had no page turns beside it, the Voltar of the server
    settings screen was always blue, and so was the Voltar of the help, the one
    that goes back from a command to the list. The maintainer asked (PR15 round 5,
    k02) for every Voltar to be grey (secondary), always, like the form's Back.

    Shared behavior affected: the navigation buttons of the /configurar screens
    (`category_view` and `settings_screen`, app/commands/setup/service.py), the help's
    `BackButton` (app/ui/buttons.py, drawn by help's `HelpSelect`) and the form
    platform's Back (`back_button` and the pickers' `picker_back`).

    Exposed by: /configurar, on a category with one page.

    Must remain guaranteed: any Voltar on any of these screens is secondary.
    """
    from app.commands.setup.service import category_view

    view = category_view("moderations", _rows(2), 0, pages, locale)

    _assert_grey(view, locale, f"category page of {pages} page(s)")


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_voltar_of_the_settings_screen_is_grey(deps, locale):
    from app.commands.setup.service import settings_screen
    from app.core import cache

    recorded_server(fake_store(), GUILD_ID, language=locale)
    cache.reset()

    _assert_grey(await settings_screen(GUILD_ID), locale, "settings screen")


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_the_voltar_of_the_help_is_grey(locale):
    view = discord.ui.View()
    view.add_item(BackButton(view, discord.Embed(), locale))

    _assert_grey(view, locale, "help")


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_back_of_a_form_step_is_grey(scenario_factory, locale):
    scenario = await roles.open_card(scenario_factory, locale)
    await scenario.select_option("Member", target="default_roles")
    await scenario.click("done")

    _assert_grey(scenario.current_message.view, locale, "default roles, second step")
    await scenario.finish()


async def test_the_back_of_a_card_picker_is_grey(scenario_factory):
    scenario = await block_links.open_card(scenario_factory, "pt-br")
    await scenario.click("customize:exceptions")

    _assert_grey(scenario.current_message.view, "pt-br", "block links exceptions")
    await scenario.finish()


@pytest.mark.parametrize(
    "locale,line",
    [
        (
            "en-us",
            "-# • If you need help, don't hesitate to call us on my official server "
            "with the `/support` command.",
        ),
        (
            "pt-br",
            "-# • Se precisar de ajuda, é só me chamar no meu servidor oficial com o "
            "comando `/suporte`.",
        ),
    ],
)
def test_the_report_dm_closes_with_its_support_line_under_the_ticket(locale, line):
    """The closing subtext line of an embed with fields is its last line, under them.

    Broke as: `with_footer` (`app/ui/embed.py`) appended the closing line to the
    description, which Discord draws above the fields, so the link check (once its
    /report line left the raw embed footer) read "-# • Use the `/report` command ..."
    under its title and above its three fields, the /report DM opened with "If you need
    help ..." above the ticket's fields, and a form step showing its pick drew its
    /report line above the "Selected" field. Shared behaviour: `with_footer`, which
    `response_embed` and every form screen (`embed_of`) close with. Consumers that
    exposed it: the link check, the /report DM. Guaranteed: an embed with fields closes
    with the line as subtext in a last, full-width field under them; an embed without
    fields still closes its description with it.
    """
    from app.commands.report.service import ticket_embed

    embed = ticket_embed(
        locale=locale, ticket="KEIKO-7", description="It broke", command="/help"
    )

    assert embed.description is None, "nothing between the title and the ticket"
    *ticket, closing = embed.fields
    assert len(ticket) == 3
    assert all(field.inline for field in ticket)
    assert (closing.name, closing.value, closing.inline) == (
        Style.BLANK_FIELD_NAME,
        line,
        False,
    )


@pytest.mark.parametrize("locale", ["en-us", "pt-br"])
def test_a_form_screen_with_fields_closes_with_its_line_under_them(locale):
    footer = standard_footer(locale)
    picked = Screen(
        title="Match", description="Pick one", footer=footer, fields=(Field("#1", "A"),)
    )
    plain = Screen(title="Match", description="Pick one", footer=footer)

    embed = embed_of(picked)

    assert embed.description == "Pick one"
    assert [(field.name, field.value) for field in embed.fields] == [
        ("#1", "A"),
        (Style.BLANK_FIELD_NAME, f"-# {footer}"),
    ]
    assert embed_of(plain).description == f"Pick one\n\n-# {footer}"


@pytest.mark.parametrize(
    "locale,line",
    [
        (
            "en-US",
            "-# • Use the `/help` command to better understand how to use the "
            "available commands.",
        ),
        (
            "pt-BR",
            "-# • Use o comando `/ajuda` para entender melhor como usar os comandos "
            "disponíveis.",
        ),
    ],
)
async def test_support_closes_with_its_help_line_as_subtext(guild, locale, line):
    """/support closes with its `/help` line as subtext, where the backticks draw code.

    Broke as: /support built its embed by hand and set its closing line, "Use the `/help`
    command ...", as the embed footer, which Discord draws without markdown, so the
    backticks showed around `/help`. Shared behaviour: the closing line of
    `response_embed` (`app/ui/embed.py`), drawn through `with_footer` as the last line of
    the embed, under its fields. Consumer that exposed it: `Support.support`
    (`app/commands/support/cog.py`). Guaranteed: the answer carries no embed footer, and
    it ends, under its how-to field, with the closing line as `-# • ` subtext, the slash
    command in backticks, in both locales.
    """
    from app.commands.support.cog import Support

    interaction = MockInteraction(user=create_member(guild), guild=guild, locale=locale)
    interaction.command = SimpleNamespace(qualified_name="support")

    await Support.support.callback(Support(SimpleNamespace()), interaction)

    embed = interaction.get_last_embed()
    assert embed.footer.text is None, "an embed footer draws the backticks raw"
    assert "-# " not in embed.description, "the line stays under the how-to field"
    assert len(embed.fields) == 2
    assert (embed.fields[-1].name, embed.fields[-1].value) == ("\u200b", line)


def _interaction():
    interaction = MagicMock()
    interaction.locale = "pt-br"
    interaction.followup.send = AsyncMock()
    return interaction


async def test_a_preview_with_one_message_never_sends_a_none_view():
    """A preview with a single written message still opens.

    On the local bot (2026-09-16) previewing a YouTube notification failed with
    "expected view parameter to be of type View or LayoutView, not NoneType":
    with nothing to page through, the sender passed `view=None`, which discord.py
    refuses. Guaranteed: a preview never hands discord.py a None view.
    """
    interaction = _interaction()

    await MessagePreviewView(["está ao vivo!"], "pt-br").send(interaction)

    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs.get("view", discord.utils.MISSING) is not None
    assert kwargs["content"] == "está ao vivo!"


async def test_the_welcome_preview_with_one_message_never_sends_a_none_view():
    from app.commands.welcome.service import send_welcome_message_preview
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    member = create_member(guild, id=555, name="Tester")
    member._user = MagicMock(id=555)
    interaction = _interaction()
    interaction.user = member
    interaction.guild = guild

    await send_welcome_message_preview(
        interaction,
        [
            {"key": "welcome_messages_title", "value": "Chegou gente nova!"},
            {"key": "welcome_messages", "value": "Oi {user}!"},
            {"key": "welcome_messages_footer", "value": "Divirta-se!"},
            {"key": "welcome_design", "value": "custom_only"},
            {
                "key": "welcome_custom_image",
                "value": "https://cdn.discordapp.com/attachments/1/2/banner.png",
            },
        ],
    )

    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs.get("view", discord.utils.MISSING) is not None


async def test_a_preview_with_nothing_written_still_says_something():
    """Broke as: Discord refused the preview with "Cannot send an empty
    message" when the feature handed it no text at all."""
    interaction = _interaction()

    await MessagePreviewView([], "pt-br").send(interaction)

    assert interaction.followup.send.await_args.kwargs["content"].strip()


async def test_the_twitch_preview_carries_the_embed_the_notification_sends(deps):
    """Lucas: the preview should be the very embed the server gets, not the
    text alone. The live's own title and category fill it when there is one."""
    from app.commands.twitch.service import send_notification_preview

    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.set_stream_online("gaules", game="CS2", title="Bom dia!")
    interaction = _interaction()

    await send_notification_preview(
        interaction,
        [
            {"key": "streamer", "value": "gaules"},
            {"key": "notification_messages", "value": "está ao vivo!"},
        ],
    )

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "Bom dia!"
    assert [field.value for field in embed.fields] == ["CS2", "gaules"]
    assert embed.thumbnail.url, "the streamer picture belongs on the embed"
    assert embed.description == "Descrição da live aqui", "tw-7: never the channel bio"


async def test_the_twitch_preview_shows_an_example_when_nobody_is_live(deps):
    """Lucas: show something where the live picture goes, so the space it takes
    is visible. A streamer who is offline has no title, category or picture of
    the moment, so the example lines and the last stream's picture stand in."""
    from app.commands.twitch.service import send_notification_preview
    from app.core.utils import ml

    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.set_last_video(
        "gaules", "https://static-cdn.jtvnw.net/vod/%{width}x%{height}.jpg"
    )
    interaction = _interaction()

    await send_notification_preview(
        interaction,
        [
            {"key": "streamer", "value": "gaules"},
            {"key": "notification_messages", "value": "está ao vivo!"},
        ],
    )

    embed = interaction.followup.send.await_args.kwargs["embed"]
    example = ml(
        "notifications_twitch.ui.announcement.samples.title", locale="pt-br"
    )
    assert embed.title == example
    assert embed.image.url == "https://static-cdn.jtvnw.net/vod/1280x720.jpg"
    assert embed.description == "Descrição da live aqui"


async def test_the_youtube_preview_carries_the_embed_the_notification_sends(
    deps, monkeypatch
):
    """The channel is the real one, with its latest upload's picture; the video
    lines show an example, since the next video does not exist yet (yt-3).

    The answers are written here in the shape the real client returns: the
    channel with its snippet and uploads playlist, then the newest playlist
    item's snippet. Two calls, two quota units.
    """
    from app.commands.youtube import service as youtube
    from app.core.utils import ml

    channel = {
        "id": "UC1",
        "snippet": {
            "title": "Canal de Teste",
            "description": "O canal de testes\nsegunda linha",
            "customUrl": "@pewdiepie",
            "thumbnails": {"high": {"url": "https://yt3.ggpht.com/avatar.jpg"}},
        },
        "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}},
    }
    latest = {"thumbnails": {"high": {"url": "https://i.ytimg.com/vi/abc/hq.jpg"}}}
    asked = []
    monkeypatch.setattr(
        deps.youtube, "get_channel_by_handle", lambda name: asked.append(name) or channel
    )
    monkeypatch.setattr(
        deps.youtube, "get_latest_upload", lambda playlist: asked.append(playlist) or latest
    )
    interaction = _interaction()

    await youtube.send_notification_preview(
        interaction,
        [
            {"key": "youtuber", "value": "pewdiepie"},
            {"key": "notification_messages", "value": "postou vídeo novo!"},
        ],
    )

    embed = interaction.followup.send.await_args.kwargs["embed"]
    example = ml(
        "notifications_youtube_video.ui.announcement.samples.title", locale="pt-br"
    )
    assert embed.title == example
    assert embed.thumbnail.url == "https://yt3.ggpht.com/avatar.jpg"
    assert embed.description == "O canal de testes"
    assert embed.image.url == "https://i.ytimg.com/vi/abc/hq.jpg"
    assert [field.value for field in embed.fields] == ["Descrição do vídeo aqui"]
    assert asked == ["pewdiepie", "UU1"], "one channel call and one playlist call"


async def test_a_preview_with_several_messages_carries_the_next_button():
    interaction = _interaction()

    await MessagePreviewView(["um", "dois"], "pt-br").send(interaction)

    kwargs = interaction.followup.send.await_args.kwargs
    assert isinstance(kwargs["view"], MessagePreviewView)
