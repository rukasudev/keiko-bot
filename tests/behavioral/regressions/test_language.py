"""Which language Keiko speaks and how his copy resolves: what a server reads is in
the language its settings saved, every text resolves to its own words and never to a
key, and the wording rules (Keiko in the masculine, the team's ticket in English).
"""
import ast
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import requests
import yaml

from app.constants import Style
from app.core import i18n
from app.core.commands import form_files
from app.core.servers import guess_server_settings
from app.core.utils import ml
from app.integrations.stream_elements import StreamElementsClient
from tests.behavioral.flows import welcome
from tests.behavioral.harness.locators import walk_items
from tests.generators.moderations import recorded_server
from tests.helpers.components import card_texts
from tests.mocks.discord import (
    MockInteraction,
    create_guild,
    create_member,
    create_message,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("i18n"),
]


APP = Path(__file__).resolve().parents[3] / "app"


OTHER = {"pt-br": "en-us", "en-us": "pt-br"}


DISCORD_LOCALE = {
    "pt-br": discord.Locale.brazil_portuguese,
    "en-us": discord.Locale.american_english,
}


STREAM_ELEMENTS = "stream_elements_commands"


def opening(key, language):
    """The words a copy starts with, before its first placeholder."""
    return re.split(r"[$\{]", ml(key, language))[0]


@pytest.fixture
def server(deps, mock_cache, monkeypatch):
    """A server whose admin set up StreamElements for `gaules`, Keiko seeing only it."""
    guild = create_guild(id=5001, name="Server", channels=["general"])
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    deps.twitch.add_user("gaules", user_id="123")

    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {"streamer": "gaules", "channel_id": "se-gaules", "enabled": True}
        return {}

    mock_cache.side_effect = configured
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(lambda channel_id: [{"command": "mouse", "reply": "G Pro", "enabled": True}]),
    )
    channel = guild.text_channels[0]
    channel._fetch_message.side_effect = lambda message_id: next(
        (sent for sent in channel._sent_messages if str(sent.id) == str(message_id)), None
    )
    guild.cache = mock_cache
    return guild


def twitch_notice(guild):
    channel = guild.text_channels[0]
    guild_data = {"guild_id": str(guild.id)}
    notification = {
        "channel": {"value": str(channel.id)},
        "streamer": {"value": "gaules"},
        "notification_messages": {"value": "{streamer} is live!"},
    }
    stream_info = {"title": "LOUD vs FURIA", "game_name": "CS2"}
    user_info = {"login": "gaules", "description": "Gaules"}
    return channel, guild_data, notification, stream_info, user_info


async def twitch_live(deps, guild):
    from app.commands.twitch.service import announce_live

    channel, *notice = twitch_notice(guild)
    await announce_live(*notice)
    return channel.get_last_embed().fields[0].name, (
        "notifications_twitch.ui.announcement.fields.game"
    )


async def twitch_status(deps, guild):
    from app.commands.twitch.service import announce_live, show_status

    channel, guild_data, notification, stream_info, user_info = twitch_notice(guild)
    await announce_live(guild_data, notification, stream_info, user_info)
    await show_status(guild_data, notification, "gaules", "offline", "2h")
    return channel.get_last_embed().footer.text, (
        "notifications_twitch.ui.announcement.states.offline"
    )


async def youtube_video(deps, guild):
    from app.commands.youtube.service import VideoAnnouncement, deliver_video

    channel = guild.text_channels[0]
    announcement = VideoAnnouncement(
        video={"id": "video-1", "snippet": {"title": "A video", "description": "About it"}},
        channel={"title": "PewDiePie"},
        youtuber="pewdiepie",
        followers=[],
    )
    notification = {
        "youtuber": {"value": "pewdiepie"},
        "channel": {"value": str(channel.id)},
        "notification_messages": {"value": "{youtuber} posted {video_link}"},
    }
    await deliver_video(announcement, {"guild_id": str(guild.id)}, notification)
    return channel.get_last_embed().fields[0].name, (
        "notifications_youtube_video.ui.announcement.fields.description"
    )


async def birthday(deps, guild):
    from app.commands.birthdays.celebration import celebrate_in_guild
    from app.commands.birthdays.service import save_birthday_config
    from app.core.servers import set_feature_enabled
    from tests.generators.birthdays import saved_birthday

    channel = guild.text_channels[0]
    create_member(guild, id=555, name="Tester")
    await save_birthday_config(str(guild.id), str(channel.id), False)
    saved_birthday(str(guild.id), "555", "05-12", reminder_id="4001")
    await set_feature_enabled(str(guild.id), "reminders_birthday", True)

    await celebrate_in_guild(
        str(guild.id), [{"user_id": "555", "date": "05-12"}], "05-12", Counter()
    )
    return channel.get_last_embed().title, "reminders_birthday.ui.announcement.defaults.title"


async def stream_elements_reply(deps, guild):
    from app.commands.stream_elements.service import check_message

    member = create_member(guild, id=111, name="Asker")
    message = create_message(content="ks!mouse", author=member, channel=guild.text_channels[0])
    await check_message(str(guild.id), message, "ks!")
    embed = message._reply.await_args.kwargs["embed"]
    return embed.footer.text, f"{STREAM_ELEMENTS}.ui.texts.reply_footer"


async def stream_elements_list(deps, guild):
    from app.commands.stream_elements.service import check_message

    member = create_member(guild, id=111, name="Asker")
    message = create_message(
        content="ks!commands", author=member, channel=guild.text_channels[0]
    )
    await check_message(str(guild.id), message, "ks!")
    embed = message._reply.await_args.kwargs["embed"]
    return embed.title, f"{STREAM_ELEMENTS}.ui.records.commands.title"


async def stream_elements_list_page(deps, guild):
    from app.commands.stream_elements.service import check_message

    member = create_member(guild, id=111, name="Asker")
    message = create_message(
        content="ks!commands", author=member, channel=guild.text_channels[0]
    )
    await check_message(str(guild.id), message, "ks!")
    footer = message._reply.await_args.kwargs["embed"].footer.text
    return footer, "texts.page"


async def greeting(deps, guild):
    from app.commands.setup.service import GreetingsView

    guild.me = SimpleNamespace()
    await GreetingsView().send(guild)
    card = guild.text_channels[0].get_last_message().view
    return "\n".join(card_texts(card)), "setup.ui.cards.greeting.title"


SITES = [
    twitch_live,
    twitch_status,
    youtube_video,
    birthday,
    stream_elements_reply,
    stream_elements_list,
    stream_elements_list_page,
    greeting,
]


@pytest.mark.parametrize("language", ["pt-br", "en-us"])
@pytest.mark.parametrize("site", SITES, ids=[site.__name__ for site in SITES])
async def test_every_public_site_speaks_the_saved_language_not_the_servers_locale(
    deps, server, site, language
):
    """What the whole server sees is written in the language its settings saved.

    Broke as: before PR15 every public message guessed the server's language from
    Discord's community locale on every send (`guild_locale`, `preferred_locale`), the
    birthday from the language its setup happened to run in, and the StreamElements
    command list's page footer was always English. A server whose admin saved another
    language kept reading the guess.

    Shared behaviour: `server_language` (`app/core/servers.py`), read off the loop
    by every public site: the Twitch notice and its status, the YouTube notice, the
    birthday celebration, the StreamElements reply and command list (with the page footer
    of `PaginationView`'s chat reply) and the greeting. Consumer that exposed it: the
    server settings of PR15.

    Guaranteed: each public site speaks the saved language when the server's Discord
    locale says the other one; the page footer follows the locale its view was built
    with; an answer only the clicker sees speaks the saved language too, once the seam
    every interaction passes (`interaction_language`) set it, since 2026-11-08 (it kept the
    clicker's own language before); nothing in
    `app/` guesses a language from `preferred_locale` outside the join's guess.
    """
    recorded_server(deps.mongo_client, server.id, language=language)
    server.preferred_locale = DISCORD_LOCALE[OTHER[language]]

    text, key = await site(deps, server)

    assert opening(key, language) in text
    assert opening(key, OTHER[language]) not in text


@pytest.mark.parametrize("language", ["pt-br", "en-us"])
def test_the_page_footer_follows_the_locale_its_view_was_built_with(language):
    from app.ui.pagination import PaginationView

    view = PaginationView(
        None, "Title", "Description", {"!mouse": "G Pro"}, locale=language
    )

    footer = view.create_embed(view.get_current_page_data()).footer.text

    assert footer == f"• {ml('texts.page', language)} 1 / 1"


async def test_an_answer_only_the_clicker_sees_speaks_the_saved_language(
    scenario_factory, deps, monkeypatch
):
    monkeypatch.setattr(
        StreamElementsClient, "get_chat_commands", staticmethod(lambda channel_id: [])
    )
    scenario = scenario_factory(locale="pt-br")
    recorded_server(deps.mongo_client, scenario.guild.id, language="en-us")
    await scenario.start_manager(
        "stream_elements_commands",
        {"streamer": "gaules", "channel_id": "se-gaules", "enabled": True},
    )

    await scenario.click("Commands")

    answer = scenario.expect_message(ephemeral=True)
    assert answer["embed"]["title"] == "🎮 StreamElements Commands"


def test_no_module_guesses_a_servers_language_from_its_discord_locale():
    import app.core.utils as utils

    guessing = sorted(
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if "preferred_locale" in path.read_text(encoding="utf-8")
    )

    assert not hasattr(utils, "guild_locale")
    assert [
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if "guild_locale" in path.read_text(encoding="utf-8")
    ] == []
    assert guessing == ["core/servers.py"], "only the join's guess reads it"


def _guild(db, preferred_locale):
    channel = SimpleNamespace(
        permissions_for=lambda _member: SimpleNamespace(send_messages=True),
        send=AsyncMock(),
    )
    guild = SimpleNamespace(
        id=987,
        preferred_locale=preferred_locale,
        system_channel=None,
        text_channels=[channel],
        me=SimpleNamespace(),
    )
    recorded_server(db, guild.id, **guess_server_settings(guild))
    return guild, channel


GREETING = {
    "pt-br": {
        "buttons": ["Configurar", "Ajuda"],
        "language": "🇧🇷 Português",
        "category": "🔔 **Notificações:** Twitch Lives, YouTube Vídeos e Lembretes de Aniversário.",
    },
    "en-us": {
        "buttons": ["Setup", "Help"],
        "language": "🇺🇸 English",
        "category": "🔔 **Notifications:** Twitch Lives, YouTube Videos and Birthday Reminders.",
    },
}


@pytest.mark.parametrize(
    "preferred_locale,locale",
    [
        (discord.Locale.brazil_portuguese, "pt-br"),
        (discord.Locale.american_english, "en-us"),
        (discord.Locale.japanese, "en-us"),
    ],
)
async def test_the_greeting_is_sent_in_the_servers_language(deps, preferred_locale, locale):
    """The greeting a new server gets is written in the server's language.

    Broke as: in beta.2 every greeting came out in English. The greeting compared the
    server's locale, lowercased to `pt-br`, with `supported_locales`, which spells it
    `pt-BR`, so a Brazilian server never matched and fell back to `en-us`. Shared
    behaviour: the language a new server's settings get from the join's guess
    (`guess_server_settings`), which every server-facing message reads
    (`server_language`). Consumer that exposed it: the onboarding greeting.
    Guaranteed: a server whose preferred locale is Brazilian Portuguese reads the
    greeting, its buttons and its language select in pt-br, and any other server keeps
    English.
    """
    from app.commands.setup.service import GreetingsView

    guild, channel = _guild(deps.mongo_client, preferred_locale)

    await GreetingsView().send(guild)

    view = channel.send.await_args.kwargs["view"]
    shown = card_texts(view)
    assert shown[0] == f"## {ml('setup.ui.cards.greeting.title', locale)}"
    assert f"\n{GREETING[locale]['category']}\n" in "\n".join(shown)
    buttons = [item for item in walk_items(view) if isinstance(item, discord.ui.Button)]
    selects = [item for item in walk_items(view) if isinstance(item, discord.ui.Select)]
    assert [button.label for button in buttons] == GREETING[locale]["buttons"]
    assert len(selects) == 1
    assert selects[0].placeholder == GREETING[locale]["language"]


async def test_an_en_us_admin_sees_english_fields_after_a_pt_br_run(
    scenario_factory, deps
):
    """A server recorded again with another language is answered in that language at once.

    Broke as: database PR 1 (#61) kept a server's settings in a process memo
    (`server_settings` and `_server_setting`, app/core/servers.py, through
    `recalled`/`remembered` in app/core/cache.py) that every product writer forgets
    (`join_guild`, `leave_guild`, `save_server_setting`), but the test seed
    `recorded_server` (tests/generators/moderations.py) wrote guild.settings without
    forgetting it. The memory soak runs every golden path in one process, so
    `welcome_messages/setup_happy.en-us`, run right after its pt-br twin on the same
    server, read the memo's pt-br and its modal showed the Portuguese fields.

    Shared behaviour: the server settings memo and the seed every scenario starts
    from (`FormScenario.__init__`, tests/behavioral/harness/driver.py). Consumers:
    every golden path, every behavioral scenario and the soak (`make soak`).

    Guaranteed: a server recorded in en-us after a pt-br run in the same process
    shows an en-us admin the English modal, as it would after a real save.
    """
    with welcome.banner_stub():
        first = await welcome.open_card(scenario_factory, "pt-br")
        await welcome.submit_messages(first, "pt-br")
        await first.click("done")
        await first.confirm()
    deps.mongo_client.guild.cogs.delete_many({})
    deps.redis_client.flushdb()

    scenario = await welcome.open_card(scenario_factory, "en-us")
    await scenario.click("customize:messages")

    assert welcome.TITLE_FIELD["en-us"] in scenario.pending_modal_fields()


LOCALES = ("en-us", "pt-br")


@pytest.mark.parametrize(
    "locale,title",
    [("en-us", "🚨 Default Roles Disabled"), ("pt-br", "🚨 Cargos Padrão Desativados")],
)
async def test_the_roles_sync_of_a_server_without_the_feature_shows_its_real_title(
    mock_cache, locale, title
):
    """An error embed never shows a copy key: each error it names has a title and a message.

    Broke as: pressing the default roles sync button while the feature is off answered a
    red embed titled `🚨 errors.command-default-roles-disactivated.title` (after the copy
    moved into the command, `🚨 default_roles.errors.disactivated.title`, since renamed
    `deactivated`): the error wrote
    only its `message`, and `response_error_embed` reads `<error>.title` and
    `<error>.message`, so the missing title came back as its own key. Shared behaviour:
    `response_error_embed` (`app/ui/embed.py`), which every command and the form adapter
    call with an error key. Consumer that exposed it: `set_on_default_roles_sync` in
    `app/commands/roles/service.py`. Guaranteed: that embed shows its real title in both
    locales, and every error key `app/` passes to `response_error_embed` as a literal, or
    under a literal prefix, resolves both its title and its message in both locales.
    """
    from app.commands.roles import service

    mock_cache.return_value = None
    interaction = MagicMock(guild=MagicMock(id=10), locale=locale)
    interaction.followup.send = AsyncMock()

    await service.set_on_default_roles_sync(interaction)

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == title
    assert "default_roles" not in embed.title and ".title" not in embed.title
    assert embed.color.value == int(Style.RED_COLOR, 16)
    message = ml("default_roles.ui.errors.deactivated.message", locale)
    assert embed.description.startswith(f"{message}\n")


def _error_keys():
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else ""
            name = func.id if isinstance(func, ast.Name) else name
            if name != "response_error_embed":
                continue
            where = f"{path.relative_to(APP.parent)}:{node.lineno}"
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                yield where, argument.value, False
            elif isinstance(argument, ast.JoinedStr) and isinstance(
                argument.values[0], ast.Constant
            ):
                yield where, argument.values[0].value, True


def _blocks(key, prefixed, locale):
    if not prefixed:
        return [key if "." in key else f"errors.{key}"]
    loaded = i18n.keys(locale)
    return sorted(
        {
            key + found[len(key) :].split(".")[0]
            for found in loaded
            if found.startswith(key)
        }
    )


ERROR_KEYS = list(_error_keys())


def test_the_scan_finds_the_error_keys_of_the_commands():
    found = {key for _where, key, _prefixed in ERROR_KEYS}
    assert "default_roles.ui.errors.deactivated" in found
    assert "setup.ui.errors.refused_" in found


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize(
    "where,key,prefixed", ERROR_KEYS, ids=[where for where, _key, _p in ERROR_KEYS]
)
def test_every_error_key_resolves_its_title_and_its_message(
    where, key, prefixed, locale
):
    loaded = i18n.keys(locale)
    blocks = _blocks(key, prefixed, locale)
    missing = [
        f"{block}.{part}"
        for block in blocks
        for part in ("title", "message")
        if not isinstance(loaded.get(f"{block}.{part}"), str)
    ]

    assert blocks, f"{where}: no error is written under {key}"
    assert missing == [], f"{where}: write {missing} in {locale}"


def _command_error_blocks():
    from app.core.commands import commands

    loaded = i18n.keys("en-us")
    for command in commands():
        prefix = f"{command.key}.ui.errors."
        blocks = {key.rsplit(".", 1)[0] for key in loaded if key.startswith(prefix)}
        for block in sorted(blocks):
            yield block


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("block", list(_command_error_blocks()))
def test_every_error_a_command_writes_draws_its_real_title(block, locale):
    """Broke as: default roles filed the panel's lost-roles warning under
    `errors.missing-permissions`, an "error" with a message and no title, so any
    reader that drew it as the error it claims to be would show the admin
    `🚨 default_roles.errors.missing-permissions.title`. Shared behaviour: a
    command's `errors` block, which `response_error_embed`, the forms and the
    validators name by its block. Consumer that exposed it: the default roles
    panel (`DefaultRolesFeature.open`). Guaranteed: every block a command writes
    under `errors` is an error with a title and a message, so it draws with its
    real title; a text that is no error lives in the block that draws it.
    """
    from app.ui.embed import response_error_embed

    loaded = i18n.keys(locale)
    missing = [
        part for part in ("title", "message")
        if not isinstance(loaded.get(f"{block}.{part}"), str)
    ]
    embed = response_error_embed(block, locale)

    assert missing == [], f"{block} is no error: it writes no {missing}"
    assert block not in (embed.title or "") and ".title" not in (embed.title or "")


PLURAL_FORMS = {"zero", "one", "few", "many", "other"}


def _plural_blocks(node, path=""):
    if not isinstance(node, dict):
        return
    if len(PLURAL_FORMS & {str(key) for key in node}) >= 2:
        yield path
    for key, value in node.items():
        yield from _plural_blocks(value, f"{path}.{key}")


@pytest.mark.parametrize(
    "path,prefix",
    [
        pytest.param(path, prefix, id=f"{path.parent.name}/{path.name}")
        for path, prefix in i18n.files()
    ],
)
def test_every_text_of_a_copy_file_resolves_to_its_own_words(path, prefix):
    """No language block is read as a plural form, so every key under it resolves.

    Broke as: the /configurar health line showed
    `commands.commands.setup.health.delivered.block-links.many` instead of "✅ Bloqueei 3
    links nos últimos 7 dias". python-i18n's file loader stored a block with two or more
    of the keys `zero`, `one`, `few`, `many`, `other` as one plural value, so
    `ml("....one")` found nothing and returned its key. Shared behaviour: every language
    file, read through `ml`. Consumer that exposed it: the health line of a /configurar
    category page, which now closes each panel's statistics, its counts under each
    command's `command.stats.delivered`, a `{one, other}` block.
    Guaranteed: every text of a copy file resolves through `ml` to its own words in each
    locale, the keys of a `{one, other}` block included, since the copy loader adds each
    text under its full key; and no block of a `form.yml` has two plural-named keys.
    """
    unresolved = [
        f"{key} ({locale})"
        for key, words in i18n.written(path, prefix)
        for locale in i18n.locales()
        if ml(key, locale) != words[locale]
    ]

    assert unresolved == []


def test_a_plural_block_of_the_copy_resolves_each_of_its_forms():
    document = yaml.safe_load(
        (i18n.APP / "commands/block_links/copy.yml").read_text(encoding="utf-8")
    )

    assert list(_plural_blocks(document)) == [".command.stats.delivered"]
    assert ml("block_links.command.stats.delivered.one", "pt-br").startswith("✅ Bloqueei 1")
    assert "$count links" in ml("block_links.command.stats.delivered.other", "en-us")


@pytest.mark.parametrize(
    "path",
    sorted(form_files().values()),
    ids=lambda path: f"{path.parent.name}/{path.name}",
)
def test_no_form_block_has_two_plural_named_keys(path):
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert list(_plural_blocks(document)) == []


COMMANDS = Path(__file__).resolve().parents[3] / "app" / "commands"


FIRST_PERSON_FEMININE = re.compile(
    r"\b(?:estou|fico|fui|sou|estarei|ficarei|serei)\s+(?:\w+\s+)?"
    r"(?:pronta|ativa|sozinha|\w+ada)\b|\bobrigada\b",
    re.IGNORECASE,
)


def _pt_br_strings(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "en-us":
                continue
            yield from _pt_br_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _pt_br_strings(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def _files():
    yield from (path for path, _ in i18n.files())
    yield COMMANDS.parent / "forms" / "vocabulary.yml"


@pytest.mark.parametrize(
    "path", list(_files()), ids=lambda path: f"{path.parent.name}/{path.name}"
)
def test_no_pt_br_string_calls_keiko_feminine(path):
    """Keiko speaks of himself in the masculine in every pt-br string.

    Broke as: the welcome card said "eu já fico pronta", and the link check said "fui
    configurada", "Estou pausada" and "Estou ativa", while every other screen calls
    Keiko "o Keiko" and "animado". Shared behaviour: the pt-br language files, read by
    every screen. Consumer that exposed it: the welcome messages setup card in the
    beta.2 review. Guaranteed: no pt-br string puts a feminine adjective after a
    verb in Keiko's first person.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    found = [
        (where, match.group(0))
        for where, value in _pt_br_strings(document)
        for match in FIRST_PERSON_FEMININE.finditer(value)
    ]

    assert found == []


def timed_out(**_fields):
    raise requests.ReadTimeout("api.notion.com did not answer in 15 seconds")


async def _ticket(guild, locale, monkeypatch, attachment=None):
    from app.commands.report.cog import Report

    reports = MagicMock()
    reports.send = AsyncMock()
    bot = SimpleNamespace(
        notion=SimpleNamespace(create_report=timed_out),
        config=SimpleNamespace(ADMIN_REPORTS_CHANNEL_ID=1),
        get_channel=lambda channel_id: reports,
    )
    monkeypatch.setattr(Report, "_send_report_dm", AsyncMock())
    interaction = MockInteraction(user=create_member(guild), guild=guild, locale=locale)
    interaction.command = SimpleNamespace(qualified_name="report")

    await Report.report.callback(
        Report(bot), interaction, "Broken button", "It does nothing", "block_links",
        attachment,
    )
    return reports.send.await_args.kwargs["embed"]


@pytest.mark.parametrize("locale", ["pt-BR", "en-US"])
async def test_the_ticket_reads_in_english_whatever_the_authors_language(
    guild, locale, monkeypatch
):
    """The ticket /report posts in the team's reports channel is written in English.

    Broke as: `Report._create_report_embed` wrote the ticket the team reads in Portuguese
    literals ("📩 Novo ticket criado", "Título", "Sem anexo"), while the writing-style
    skill keeps what only whoever runs Keiko reads in English, with no `ml()` and no
    pt-br counterpart. Shared behaviour: none; the reports channel of the admin server is
    an operator surface, and the author's copy of the ticket (the DM) stays in the
    author's language. Consumer that exposed it: `/report`
    (`app/commands/report/cog.py`). Guaranteed: whatever the author's language, the
    ticket is titled "📩 New ticket created [#<id>]!" and its fields read Title,
    Description, Command, Author and Attachment, "No attachment" when none was sent.
    """
    ticket = await _ticket(guild, locale, monkeypatch)

    assert re.fullmatch(r"📩 New ticket created \[#KEIKO-\d{4}\]!", ticket.title)
    assert [field.name for field in ticket.fields] == [
        "Title", "Description", "Command", "Author", "Attachment",
    ]
    assert [field.value for field in ticket.fields][:3] == [
        "Broken button", "It does nothing", "block_links",
    ]
    assert ticket.fields[-1].value == "No attachment"


async def test_an_attached_file_is_linked_on_the_ticket(guild, monkeypatch):
    attachment = SimpleNamespace(url="https://cdn.discordapp.com/attachments/1/2/shot.png")

    ticket = await _ticket(guild, "pt-BR", monkeypatch, attachment)

    assert ticket.fields[-1].name == "Attachment"
    assert ticket.fields[-1].value == attachment.url
