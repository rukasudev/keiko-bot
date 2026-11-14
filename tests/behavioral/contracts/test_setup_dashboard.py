"""The /setup screen: the first thing most servers see of Keiko.

It was a dense embed: one bold line per feature, the command on the next line,
and a row of buttons far from the lines they belonged to. Its first card
redesign still read as busy: every row said its status three times (a coloured
circle, a word, the button) over three lines with three emoji. Then it listed
every feature on one card, which no longer fit Discord's forty components
once an eighth feature came (PR15).

Guaranteed: a Components V2 card with Keiko's picture beside a dog-titled
header of two lines (the title and a one-sentence intro); its first page has
the server settings row, then one row per category in the order /configurar
declares, each a one-line brief and some of its commands beside a primary
Configurar; a category page lists only that category's features in declared
order, every row its name plus one subtext line with what it does and its
command, with no status circles; its status reads in its title; the button
reads Manage once set up and Set up otherwise; a server with everything set
up is congratulated in the intro; the card stays inside Discord's limits in both
locales; a row button opens the feature through the setup_dashboard source
with an ending of its own; and the greeting's dashboard button sends the same
card, beside its language select and its Help button, while the feature
buttons of older greetings keep answering.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.constants import Commands, KeikoIcons
from app.core.utils import ml
from tests.mocks.database import fake_store
from tests.behavioral.contracts.test_components_v2_limits import _assert_within_limits
from tests.behavioral.harness.locators import dispatch_click, walk_items
from tests.behavioral.golden.paths.common import seed_document, seed_history
from tests.behavioral.golden.paths.block_links import ENABLED as BLOCK_LINKS
from tests.behavioral.golden.paths.default_roles import ENABLED as DEFAULT_ROLES
from tests.behavioral.golden.paths.notifications_twitch import ENABLED as TWITCH
from tests.behavioral.golden.paths.welcome_messages import ENABLED as WELCOME
from tests.generators.moderations import recorded_server, seed_cog

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("setup_dashboard")]

GUILD_ID = "123456789"
BASE = "commands.commands.setup.embed"


def seed(deps, enabled, paused=()):
    """What a setup and a pause leave: each feature's document."""
    for key in enabled:
        seed_cog(
            deps.mongo_client,
            key,
            {"guild_id": GUILD_ID, Commands.ENABLED_KEY: key not in paused},
        )


@pytest.fixture
def configured(deps):
    """Welcome messages enabled, block links paused, the rest never set up."""
    seed(
        deps,
        enabled=(Commands.WELCOME_MESSAGES_KEY, Commands.BLOCK_LINKS_KEY),
        paused=(Commands.BLOCK_LINKS_KEY,),
    )


def speaks(locale):
    """The server's saved language, which every /configurar page is drawn in."""
    from app.data.guild import settings as guild_settings
    from app.core import cache

    recorded_server(fake_store(), GUILD_ID, language=locale)
    cache.reset()


async def dashboard(locale="pt-br"):
    from app.services.setup import setup_dashboard

    speaks(locale)
    return await setup_dashboard(GUILD_ID)


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


def feature(command_key):
    return next(spec for spec in Commands.SETUP_FEATURES if spec["command_key"] == command_key)


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
    header = header_of(await dashboard())

    assert [child.content for child in header.children] == [
        "## 🐶 Configuração do Servidor",
        "Escolha o que você quer ajustar e eu te guio passo a passo!",
    ]
    assert header.accessory.media.url == KeikoIcons.IMAGE_01


@pytest.mark.parametrize("locale,expected", [
    (
        "pt-br",
        [
            "## 🐶 Configuração do Servidor",
            "Escolha o que você quer ajustar e eu te guio passo a passo!",
        ],
    ),
    (
        "en-us",
        ["## 🐶 Server Setup", "Pick what you want to set up and I'll guide you step by step!"],
    ),
])
async def test_the_header_is_the_title_and_the_intro(configured, locale, expected):
    """Lucas (beta.2 review): the note under the intro repeated what the card
    and its Comandos button already say, so the header keeps two lines."""
    lines = [child.content for child in header_of(await dashboard(locale)).children]

    assert lines == expected, "title and intro, no note"


async def page(category, locale="pt-br"):
    from app.services.setup import category_page

    speaks(locale)
    return await category_page(GUILD_ID, category)


async def test_the_first_page_lists_the_server_settings_then_each_category(configured):
    """Lucas (PR15 rounds 2 to 4): the server settings come first, and each
    category says what it does, then, after a blank line, names up to three of
    its most used commands; a category with every command listed has no
    closing line."""
    view = await dashboard()

    lines = [text_of(row).split("\n") for row in rows(view)]
    assert lines[0][0] == "⚙️ **Configurações do Servidor**"
    assert [text_of(row) for row in rows(view)[1:]] == [
        "🛡️ **Moderação**\n"
        "Recebo novos membros, dou cargos a eles e cuido dos links do servidor\n"
        "\n"
        "Comandos mais usados:\n"
        "- `/moderações mensagens boas-vindas`\n"
        "- `/moderações cargos padrão`\n"
        "- `/moderações bloquear links`",
        "🔔 **Notificações**\n"
        "Aviso o servidor quando seus criadores favoritos entram ao vivo ou postam, e quando é aniversário de algum membro\n"
        "\n"
        "Comandos mais usados:\n"
        "- `/notificações twitch lives`\n"
        "- `/notificações youtube vídeos`\n"
        "- `/moderações aniversários lembretes`",
        "🔌 **Integrações**\n"
        "Conecto o servidor a outras plataformas, como o StreamElements\n"
        "\n"
        "Comandos mais usados:\n"
        "- `/integrações comandos streamelements`",
    ]
    assert all(
        row.accessory.label == "Configurar"
        and row.accessory.style is discord.ButtonStyle.primary
        for row in rows(view)
    ), "every row opens through a primary Configurar"


def _moderations(monkeypatch, count):
    from app.services import setup

    listed = [
        ({**spec, "category": "moderations"}, "pending")
        for spec in Commands.SETUP_FEATURES[:count]
    ]

    async def states(guild_id):
        return listed

    monkeypatch.setattr(setup, "_states", states)


@pytest.mark.parametrize("locale,count,expected", [
    (
        "pt-br",
        5,
        "🛡️ **Moderação**\n"
        "Recebo novos membros, dou cargos a eles e cuido dos links do servidor\n"
        "\n"
        "Comandos mais usados:\n"
        "- `/moderações mensagens boas-vindas`\n"
        "- `/moderações cargos padrão`\n"
        "- `/moderações bloquear links`\n"
        "- e mais 2",
    ),
    (
        "en-us",
        5,
        "🛡️ **Moderation**\n"
        "I welcome new members, give them roles and keep an eye on links in the server\n"
        "\n"
        "Most used commands:\n"
        "- `/moderations welcome messages`\n"
        "- `/moderations default roles`\n"
        "- `/moderations block links`\n"
        "- and 2 more",
    ),
])
async def test_a_category_row_says_how_many_more_commands_it_has(
    deps, monkeypatch, locale, count, expected
):
    """Lucas (PR15 round 4): "e mais" alone did not say how many commands
    were left out, and it closed even a category whose commands were all listed;
    since round 7 (ux-556) it reads as plain text with the words of
    `commands.resume.more`."""
    _moderations(monkeypatch, count)

    assert text_of(rows(await dashboard(locale))[1]) == expected


@pytest.mark.parametrize("locale,closing", [("pt-br", "- e mais 1"), ("en-us", "- and 1 more")])
async def test_one_more_command_is_told_in_the_singular(deps, monkeypatch, locale, closing):
    _moderations(monkeypatch, 4)

    assert text_of(rows(await dashboard(locale))[1]).split("\n")[-1] == closing


@pytest.mark.parametrize("locale,last", [
    ("pt-br", "- `/moderações bloquear links`"),
    ("en-us", "- `/moderations block links`"),
])
async def test_a_category_with_every_command_listed_has_no_closing_line(
    deps, monkeypatch, locale, last
):
    _moderations(monkeypatch, 3)

    assert text_of(rows(await dashboard(locale))[1]).split("\n")[-1] == last


@pytest.mark.parametrize("locale,heading", [
    ("pt-br", "Comandos mais usados:"),
    ("en-us", "Most used commands:"),
])
async def test_every_category_names_one_to_three_of_its_commands(configured, locale, heading):
    for row in rows(await dashboard(locale))[1:]:
        _name, _brief, blank, title, *examples = text_of(row).split("\n")
        assert (blank, title) == ("", heading)
        listed = [line for line in examples if line.startswith("- `/")]
        assert 1 <= len(listed) <= 3, examples
        assert all(line.endswith("`") for line in listed)
        assert len(examples) - len(listed) <= 1, "at most one closing line"


@pytest.mark.parametrize("locale,expected", [
    (
        "pt-br",
        "⚙️ **Configurações do Servidor**\n"
        "Aqui você escolhe o idioma, o fuso horário e o prefixo que eu uso neste servidor",
    ),
    (
        "en-us",
        "⚙️ **Server Settings**\n"
        "Here you pick the language, the time zone and the prefix I use in this server",
    ),
])
async def test_the_settings_row_says_what_the_screen_is_not_its_values(
    configured, locale, expected
):
    """Lucas (PR15 round 3, item 1): the row reads like the category briefs,
    one line on what the screen is; the values live on the screen itself."""
    assert text_of(rows(await dashboard(locale))[0]) == expected


@pytest.mark.parametrize("category,keys", [
    (
        "moderations",
        [Commands.WELCOME_MESSAGES_KEY, Commands.DEFAULT_ROLES_KEY, Commands.BLOCK_LINKS_KEY],
    ),
    (
        "notifications",
        [
            Commands.NOTIFICATIONS_TWITCH_KEY,
            Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
            Commands.REMINDERS_BIRTHDAY_KEY,
        ],
    ),
])
async def test_a_category_page_lists_only_its_features_in_declared_order(
    configured, category, keys
):
    """Lucas (PR15 round 4, item 2): birthdays belong to the notifications,
    while their command stays under /moderações."""
    assert [row.accessory.command_key for row in rows(await page(category))] == keys


async def test_every_row_is_its_name_and_one_subtext_line_with_its_command(configured):
    for category in Commands.SETUP_CATEGORIES:
        for section in rows(await page(category)):
            spec = feature(section.accessory.command_key)
            name_line, subtext, *rest = text_of(section).split("\n")
            assert ml(f"buttons.setup.{spec['button_key']}.label", "pt-br") in name_line
            assert subtext.startswith("-# "), "what it does reads as quiet subtext"
            assert ml(f"{BASE}.features.{spec['button_key']}", "pt-br") in subtext
            assert "`/" in subtext, "the command still teaches the shortcut"
            assert rest == [], "no health line on the row"


async def test_no_row_repeats_its_status_as_a_coloured_circle(configured):
    rendered = "\n".join(texts(await page("moderations")))

    assert not [circle for circle in ("🔴", "🟢", "⚪", "🟡") if circle in rendered]


@pytest.mark.parametrize("locale,expected", [
    ("pt-br", {
        Commands.WELCOME_MESSAGES_KEY: (
            "🎉 **Mensagens de Boas-Vindas ✅**\n"
            "-# Boas-vindas para novos membros · `/moderações mensagens boas-vindas`"
        ),
        Commands.DEFAULT_ROLES_KEY: (
            "👩‍🎓 **Cargos Padrão**\n"
            "-# Cargos automáticos para novos membros · `/moderações cargos padrão`"
        ),
        Commands.BLOCK_LINKS_KEY: (
            "🚫 **Bloquear Links ⏸️**\n"
            "-# Bloqueia links indesejados no servidor · `/moderações bloquear links`"
        ),
    }),
    ("en-us", {
        Commands.WELCOME_MESSAGES_KEY: (
            "🎉 **Welcome Messages ✅**\n"
            "-# A warm welcome for new members · `/moderations welcome messages`"
        ),
        Commands.DEFAULT_ROLES_KEY: (
            "👩‍🎓 **Default Roles**\n"
            "-# Roles given to new members automatically · `/moderations default roles`"
        ),
        Commands.BLOCK_LINKS_KEY: (
            "🚫 **Block Links ⏸️**\n"
            "-# Keeps unwanted links out of your server · `/moderations block links`"
        ),
    }),
])
async def test_each_feature_says_its_status_in_its_title(configured, locale, expected):
    """Lucas (PR15 round 3): the status reads in the title as an emoji, ✅ when
    on and ⏸️ when paused; a feature not set up has none, and the subtext is
    only what it does and its command."""
    by_key = {row.accessory.command_key: row for row in rows(await page("moderations", locale))}

    assert {key: text_of(by_key[key]) for key in expected} == expected


async def test_the_button_reads_manage_when_set_up_and_set_up_otherwise(configured):
    by_key = {
        row.accessory.command_key: row.accessory
        for row in rows(await page("moderations"))
    }
    start = "Configurar"
    manage = "Gerenciar"

    assert by_key[Commands.DEFAULT_ROLES_KEY].label == start
    assert by_key[Commands.DEFAULT_ROLES_KEY].style is discord.ButtonStyle.primary
    assert by_key[Commands.WELCOME_MESSAGES_KEY].label == manage
    assert by_key[Commands.WELCOME_MESSAGES_KEY].style is discord.ButtonStyle.secondary
    assert by_key[Commands.BLOCK_LINKS_KEY].label == manage, "a paused feature is still set up"


@pytest.mark.parametrize("locale,intro", [
    ("pt-br", "Parabéns, tudo configurado por aqui! Use os botões para ajustar o que quiser ✨"),
    (
        "en-us",
        "Congratulations, everything is set up around here! "
        "Use the buttons to fine-tune whatever you like ✨",
    ),
])
async def test_a_server_with_everything_set_up_is_congratulated(deps, locale, intro):
    """Lucas (PR15 round 3): once every feature is set up, paused ones included,
    the intro congratulates the admin instead of inviting a first setup."""
    every_key = [spec["command_key"] for spec in Commands.SETUP_FEATURES]
    seed(deps, enabled=every_key, paused=(Commands.BLOCK_LINKS_KEY,))

    view = await dashboard(locale)

    assert [child.content for child in header_of(view).children][1] == intro


async def test_a_server_with_one_feature_left_keeps_the_first_setup_intro(deps):
    every_key = [spec["command_key"] for spec in Commands.SETUP_FEATURES]
    seed(deps, enabled=[key for key in every_key if key != Commands.DEFAULT_ROLES_KEY])

    view = await dashboard()

    assert [child.content for child in header_of(view).children][1] == (
        "Escolha o que você quer ajustar e eu te guio passo a passo!"
    )


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
    by_key = {
        row.accessory.command_key: row.accessory
        for row in rows(await page("moderations"))
    }
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=int(GUILD_ID)), locale="pt-br"
    )

    await by_key[Commands.DEFAULT_ROLES_KEY].callback(interaction)

    opened.assert_awaited_once()
    args, kwargs = opened.await_args
    assert args == (interaction, Commands.DEFAULT_ROLES_KEY, "setup_dashboard")
    ending = await kwargs["ending"]()
    assert texts(ending)[0] == "## 🛡️ Moderação", "a setup from /configurar ends on its category"
    by_key = {row.accessory.command_key: text_of(row) for row in rows(ending)}
    assert by_key[Commands.DEFAULT_ROLES_KEY].split("\n")[0] == (
        "👩‍🎓 **Cargos Padrão (Recentemente Ativado 🎉)**"
    )


async def test_a_birthdays_setup_from_configurar_ends_on_the_notifications(
    configured, monkeypatch
):
    from app.components import buttons

    opened = AsyncMock()
    monkeypatch.setattr(buttons, "run_feature_command", opened)
    by_key = {
        row.accessory.command_key: row.accessory
        for row in rows(await page("notifications"))
    }
    interaction = SimpleNamespace(guild=SimpleNamespace(id=int(GUILD_ID)), locale="pt-br")

    await by_key[Commands.REMINDERS_BIRTHDAY_KEY].callback(interaction)

    ending = await opened.await_args.kwargs["ending"]()
    assert texts(ending)[0] == "## 🔔 Notificações"
    by_key = {row.accessory.command_key: text_of(row) for row in rows(ending)}
    assert by_key[Commands.REMINDERS_BIRTHDAY_KEY].split("\n")[0] == (
        "🎂 **Lembretes de Aniversário (Recentemente Ativado 🎉)**"
    )


async def test_the_greeting_dashboard_button_sends_the_same_screen(configured):
    from app.views.greetings import DashboardButton

    sent = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=555, guild_permissions=SimpleNamespace(administrator=True)),
        locale=discord.Locale.brazil_portuguese,
        guild_id=int(GUILD_ID),
        guild=SimpleNamespace(id=int(GUILD_ID)),
        response=SimpleNamespace(send_message=sent),
    )

    await DashboardButton(label="Configurar", locale="pt-br").callback(interaction)

    kwargs = sent.await_args.kwargs
    assert texts(kwargs["view"])[:2] == [
        "## 🐶 Configuração do Servidor",
        "Escolha o que você quer ajustar e eu te guio passo a passo!",
    ]
    assert kwargs["ephemeral"] is True
    assert kwargs.get("embed") is None, "a Components V2 message carries no embed"


def action_row_buttons(view):
    action_rows = [item for item in walk_items(view) if isinstance(item, discord.ui.ActionRow)]
    assert len(action_rows) == 1, "one row of buttons closes the card"
    return list(action_rows[0].children)


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


async def test_the_card_ends_with_help_history_permissions_and_support(configured):
    """Lucas (PR15 round 2, e09): the first button opens the same command list,
    now named Ajuda."""
    buttons = action_row_buttons(await dashboard())

    assert [(button.label, button.emoji.name) for button in buttons] == [
        ("Ajuda", "📚"),
        ("Histórico", "📜"),
        ("Permissões", "🩺"),
        ("Suporte", "💬"),
    ]
    support = buttons[-1]
    assert support.style is discord.ButtonStyle.link
    assert support.url == Commands.SUPPORT_SERVER_URL


async def test_stream_elements_commands_is_listed_on_setup(deps):
    """Broke as: the StreamElements integration was a command nobody found."""
    view = await page("integrations")
    by_key = {row.accessory.command_key: row for row in rows(view)}

    row = by_key[Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY]
    assert text_of(row) == (
        "🎮 **Comandos do StreamElements**\n"
        "-# Responde aos comandos do StreamElements no chat"
        " · `/integrações comandos streamelements`"
    )


class _StubSelect(discord.ui.Select):
    def update(self):
        return None


def _help_interaction(admin=True):
    return SimpleNamespace(
        client=SimpleNamespace(app_commands=[]),
        locale=discord.Locale.brazil_portuguese,
        guild_id=int(GUILD_ID),
        guild=SimpleNamespace(id=int(GUILD_ID)),
        user=SimpleNamespace(
            id=555, guild_permissions=SimpleNamespace(administrator=admin)
        ),
        extras={},
        response=SimpleNamespace(send_message=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


async def _help_setup_button(monkeypatch):
    from app.services import help as help_service

    monkeypatch.setattr(
        help_service,
        "HelpSelect",
        lambda placeholder, data: _StubSelect(
            placeholder=placeholder, options=[discord.SelectOption(label="x")]
        ),
    )
    interaction = _help_interaction()
    await help_service.send_help(interaction)
    view = interaction.response.send_message.await_args.kwargs["view"]
    embed = interaction.edit_original_response.await_args.kwargs["embed"]
    label = ml("buttons.setup.dashboard.label", "pt-br")
    buttons = [
        child
        for child in view.children
        if isinstance(child, discord.ui.Button) and child.label == label
    ]
    return embed, buttons


async def test_help_mentions_setup_and_its_button_opens_the_dashboard_for_admins(
    configured, monkeypatch
):
    """Broke as: /help never told an admin that /setup holds every feature."""
    embed, buttons = await _help_setup_button(monkeypatch)

    assert "`/configurar`" in embed.description
    assert len(buttons) == 1
    clicker = _help_interaction(admin=True)
    await buttons[0].callback(clicker)
    sent = clicker.response.send_message.await_args.kwargs
    assert texts(sent["view"])[0] == "## 🐶 Configuração do Servidor"
    assert sent.get("ephemeral") is True


async def test_the_help_setup_button_tells_a_member_it_needs_an_admin(
    configured, monkeypatch
):
    _embed, buttons = await _help_setup_button(monkeypatch)

    clicker = _help_interaction(admin=False)
    await buttons[0].callback(clicker)

    sent = clicker.response.send_message.await_args.kwargs
    assert sent["embed"].title == "⚠️ Apenas Admins"
    assert "view" not in sent


async def test_the_history_lists_every_feature_newest_first_and_names_each(deps):
    """The /setup History button: the server's recent changes across features.

    Each feature kept its own history behind its own panel, so an admin who wanted
    to know what changed in the server had to open six panels. Guaranteed: the
    history merges every feature's audit records, newest first, and every line
    names the feature it happened in.
    """
    from app.services.setup import guild_history, history_fields

    seed_history(deps, "welcome_messages", "enabled")
    seed_history(deps, "block_links", "enabled", "paused")

    records = await guild_history(GUILD_ID)

    assert (records[0]["cog"], records[0]["type"]) == ("block_links", "feature.paused")
    assert {record["cog"] for record in records} == {"welcome_messages", "block_links"}

    interaction = SimpleNamespace(
        locale="pt-br", guild=None, client=SimpleNamespace(user=SimpleNamespace(id=999))
    )
    rendered = "\n".join(history_fields(records, interaction, "pt-br").values())
    assert "**🚫 Bloquear Links**" in rendered
    assert "**🎉 Mensagens de Boas-Vindas**" in rendered


async def test_the_history_lists_the_server_settings_changed_on_the_settings_screen(deps):
    """Broke as: a server setting changed on the /setup settings screen was written
    to `events.settings`, a collection nothing read, so the history of /setup never
    showed it. Guaranteed: the history reads the server's changes in one query, the
    settings' among them, each line named by what it changed."""
    from app.core import analytics
    from app.services.setup import guild_history, history_fields

    seed_history(deps, "block_links", "enabled")
    analytics.record(
        "config.changed",
        guild_id=GUILD_ID,
        cog=Commands.SERVER_SETTINGS_KEY,
        actor=analytics.actor("user", "555"),
        props={"source": "setup_dashboard", "changed_keys": ["language"]},
    )
    await analytics.written()

    records = await guild_history(GUILD_ID)

    assert [record["cog"] for record in records] == [
        Commands.SERVER_SETTINGS_KEY, "block_links"
    ]
    interaction = SimpleNamespace(
        locale="pt-br", guild=None, client=SimpleNamespace(user=SimpleNamespace(id=999))
    )
    lines = list(history_fields(records, interaction, "pt-br").values())
    assert lines[0].startswith(ml("commands.commands.setup.settings.row", "pt-br"))
    assert "<@555>" in lines[0]


PERMISSIONS_BASE = "commands.commands.setup.permissions"


EVERYTHING = {
    "view_channel", "send_messages", "embed_links", "attach_files",
    "manage_roles", "manage_messages",
}


class Granted(SimpleNamespace):
    def __getattr__(self, name):
        return False


def guild(channels, roles=None, top_role=10, server=EVERYTHING):
    me = SimpleNamespace(
        top_role=SimpleNamespace(position=top_role),
        guild_permissions=Granted(**{name: True for name in server}),
    )

    def get_channel(channel_id):
        if channel_id not in channels:
            return None
        granted = channels[channel_id]
        return SimpleNamespace(
            id=channel_id,
            permissions_for=lambda member: Granted(**{name: True for name in granted}),
        )

    role_objects = [
        SimpleNamespace(id=role_id, name=f"role-{role_id}", position=position, managed=False)
        for role_id, position in (roles or {}).items()
    ]

    def get_role(role_id):
        return next((role for role in role_objects if role.id == role_id), None)

    return SimpleNamespace(
        id=int(GUILD_ID),
        me=me,
        roles=role_objects,
        get_channel=get_channel,
        get_role=get_role,
    )


def configure(deps, *documents):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": GUILD_ID, **{form: True for form, _ in documents}}
    )
    for form, document in documents:
        seed_document(deps, form, document)


async def report(server, locale="pt-br"):
    from app.services.setup import permission_report

    return await permission_report(server, GUILD_ID, "555", locale)


def field_of(embed, command_key, locale="pt-br"):
    spec = next(f for f in Commands.SETUP_FEATURES if f["command_key"] == command_key)
    label = ml(f"buttons.setup.{spec['button_key']}.label", locale)
    name = f"{spec['emoji']} {label}"
    return next(field.value for field in embed.fields if field.name == name)


def names(permission, locale="pt-br"):
    return ml(f"{PERMISSIONS_BASE}.names.{permission}", locale)


@pytest.fixture
def everything_configured(deps):
    configure(
        deps,
        (Commands.WELCOME_MESSAGES_KEY, WELCOME),
        (Commands.DEFAULT_ROLES_KEY, DEFAULT_ROLES),
        (Commands.BLOCK_LINKS_KEY, BLOCK_LINKS),
        (Commands.NOTIFICATIONS_TWITCH_KEY, TWITCH),
    )


FULL_CHANNELS = {100: EVERYTHING, 101: EVERYTHING, 102: EVERYTHING}


LOW_ROLES = {201: 1, 202: 2}


async def test_a_server_where_keiko_has_everything_reads_as_fine(everything_configured):
    """The /setup Permissions button: what Keiko is missing, feature by feature.

    A missing permission used to show up only when a feature failed: a welcome that
    never arrived, a role that was never given. The report reads each configured
    feature's saved answers through the platform (the channels and roles it names),
    checks what that feature needs there, and says it in the admin's language.

    Guaranteed: a server where Keiko has everything reads as fine for every
    configured feature, in declared order; a missing channel permission names the
    channel and the permission; a deleted channel is reported; a role above Keiko's
    own is reported for auto roles, and a missing server permission is reported;
    the exempt channels and roles of block links need nothing; and a server with
    nothing set up is told there is nothing to check yet.
    """
    embed = await report(guild(FULL_CHANNELS, LOW_ROLES))

    assert [(field.name, field.value) for field in embed.fields] == [
        ("🎉 Mensagens de Boas-Vindas", "✅ Tudo certo por aqui"),
        ("👩‍🎓 Cargos Padrão", "✅ Tudo certo por aqui"),
        ("🚫 Bloquear Links", "✅ Tudo certo por aqui"),
        ("📡 Twitch Lives", "✅ Tudo certo por aqui"),
    ], "only configured features are checked, in declared order"


async def test_a_missing_channel_permission_names_the_channel_and_the_permission(
    everything_configured,
):
    channels = {**FULL_CHANNELS, 101: EVERYTHING - {"embed_links"}}

    value = field_of(await report(guild(channels, LOW_ROLES)), Commands.WELCOME_MESSAGES_KEY)

    assert value == "⚠️ Preciso da permissão **Inserir links** em <#101>"


async def test_a_deleted_channel_is_reported(everything_configured):
    channels = {100: EVERYTHING, 101: EVERYTHING}

    value = field_of(await report(guild(channels, LOW_ROLES)), Commands.NOTIFICATIONS_TWITCH_KEY)

    assert value == "⚠️ Um canal configurado não existe mais"


async def test_a_role_above_keiko_and_a_missing_server_permission_are_reported(
    everything_configured,
):
    server = guild(FULL_CHANNELS, {201: 1, 202: 20}, server=EVERYTHING - {"manage_roles"})

    value = field_of(await report(server), Commands.DEFAULT_ROLES_KEY)

    assert value.split("\n") == [
        "⚠️ Preciso da permissão **Gerenciar cargos** no servidor",
        "⚠️ O cargo <@&202> está acima do meu, então não consigo atribuí-lo",
    ], "a role above mine is one I cannot give; 201 sits below it"


async def test_the_exempt_channels_and_roles_of_block_links_need_nothing(deps):
    configure(deps, (Commands.BLOCK_LINKS_KEY, BLOCK_LINKS))

    embed = await report(guild({100: set()}, {201: 99}))

    assert field_of(embed, Commands.BLOCK_LINKS_KEY) == "✅ Tudo certo por aqui"


async def test_block_links_without_manage_messages_is_reported(deps):
    configure(deps, (Commands.BLOCK_LINKS_KEY, BLOCK_LINKS))

    embed = await report(guild({100: set()}, server=EVERYTHING - {"manage_messages"}))

    assert names("manage_messages") in field_of(embed, Commands.BLOCK_LINKS_KEY)


async def test_a_server_with_nothing_set_up_has_nothing_to_check(deps):
    embed = await report(guild({}))

    assert embed.fields == []
    assert embed.description == ml(f"{PERMISSIONS_BASE}.nothing", "pt-br")


def _greeting(locale):
    from app.views.greetings import GreetingsView

    return GreetingsView(locale)


def _greeting_text(locale):
    from app.views.greetings import greeting_embed

    return greeting_embed(locale).description


@pytest.mark.parametrize("locale,labels", [
    ("pt-br", ("Configurar", "Ajuda")),
    ("en-us", ("Setup", "Help")),
])
def test_the_greeting_offers_the_language_then_setup_and_help(locale, labels):
    """Lucas (beta.2 review, PR15 round 4): seven feature buttons crowded the
    greeting, which /setup already lists. The greeting offers the language
    select on top, then Configurar and Ajuda, the help button /configurar
    carries, both readable in the chosen language."""
    view = _greeting(locale)

    select, setup, help_button = view.children
    assert isinstance(select, discord.ui.Select) and select.row == 0
    assert [(button.label, button.emoji.name, button.row) for button in (setup, help_button)] == [
        (labels[0], "🔧", 1),
        (labels[1], "📚", 1),
    ]


@pytest.mark.parametrize("locale,sentence", [
    ("pt-br", "Para ver todos os meus comandos, toque em **Ajuda**."),
    ("en-us", "To see every command I have, just tap **Help**."),
])
def test_the_greeting_points_at_its_buttons_by_their_labels(locale, sentence):
    message = _greeting_text(locale)

    assert f"**{ml('buttons.setup.dashboard.label', locale)}**" in message
    assert f"**{ml('buttons.help.label', locale)}**" in message
    assert sentence in message


@pytest.mark.parametrize("locale,tricks", [
    (
        "pt-br",
        "Aqui estão alguns truques que eu sei fazer:\n"
        "🛡️ **Moderação:** Mensagens de Boas-Vindas, Cargos Padrão e Bloquear Links.\n"
        "🔔 **Notificações:** Twitch Lives, YouTube Vídeos e Lembretes de Aniversário.\n"
        "🔌 **Integrações:** Comandos do StreamElements.\n"
        "🌐 **Utilitário:** Tradução de mensagens para qualquer idioma.\n"
        "\n",
    ),
    (
        "en-us",
        "Here are some tricks I know how to do:\n"
        "🛡️ **Moderation:** Welcome Messages, Default Roles and Block Links.\n"
        "🔔 **Notifications:** Twitch Lives, YouTube Videos and Birthday Reminders.\n"
        "🔌 **Integrations:** StreamElements Commands.\n"
        "🌐 **Utility:** Message translation to any language.\n"
        "\n",
    ),
])
def test_the_greeting_lists_each_category_with_its_configurar_features(locale, tricks):
    """Lucas (PR15 round 3 and round 4): the greeting led Integrations with 🎮
    while /configurar shows 🔌, listed birthdays under moderation and ended
    with a Base line of no feature. Each category line is now drawn from the
    features /configurar places in it, by their names, so the two cannot drift."""
    message = _greeting_text(locale)

    assert tricks in message
    assert "🎮" not in message and "🧰" not in message


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_a_refused_setting_has_a_message_for_each_server_setting_only(locale):
    """The mention left the server settings, and its refusal stayed behind."""
    import yaml

    path = f"app/languages/errors/errors.{locale}.yml"
    with open(path, encoding="utf-8") as errors:
        refused = yaml.safe_load(errors)[locale]["server-setting-refused"]

    assert sorted(refused) == ["language", "prefix", "timezone"]


@pytest.mark.shared_contract("setup_features")
def test_every_button_an_old_greeting_carries_still_answers():
    """Greetings already posted keep one button per feature: Discord sends back
    only the custom_id, so a button no registered view owns would fail with
    "This interaction failed". Guaranteed: the views registered at start-up own
    every id a greeting ever carried, and each feature id opens that feature."""
    from app.views.greetings import greeting_views

    owned = {
        item.custom_id: item for view in greeting_views() for item in view.children
    }
    for feature in Commands.SETUP_FEATURES:
        button = owned[f"greetings:setup:{feature['command_key']}"]
        assert button.command_key == feature["command_key"]
    for custom_id in (
        "greetings:setup:dashboard",
        "greetings:commands",
        "greetings:language_select",
    ):
        assert custom_id in owned, custom_id
    assert all(view.timeout is None for view in greeting_views()), "persistent"


def _admin(scenario_factory):
    from tests.mocks.discord import create_guild, create_member

    server = create_guild()
    admin = create_member(server, id=555, name="Admin")
    admin.guild_permissions = discord.Permissions.all()
    return scenario_factory(locale="pt-br", guild=server, user=admin)


async def test_the_greeting_help_button_opens_the_command_list(scenario_factory, deps):
    from app.views.greetings import GreetingsView

    scenario = _admin(scenario_factory)
    view = GreetingsView("pt-br")
    commands = next(item for item in view.children if item.custom_id == "greetings:commands")

    await dispatch_click(view, commands, scenario._mint())

    answer = scenario.outputs[-1]
    assert (answer["ephemeral"], answer["embed"]["title"]) == (True, "📚 Comandos disponíveis")


async def test_an_old_greeting_feature_button_opens_its_feature(scenario_factory, deps):
    from app.views.greetings import LegacyGreetingButtons

    scenario = _admin(scenario_factory)
    legacy = LegacyGreetingButtons()
    button = next(
        item
        for item in legacy.children
        if item.custom_id == f"greetings:setup:{Commands.DEFAULT_ROLES_KEY}"
    )

    await dispatch_click(legacy, button, scenario._mint())

    answer = scenario.outputs[-1]
    assert (answer["ephemeral"], answer["embed"]["title"]) == (True, "👩‍🎓 Cargos Padrão")
    assert [button["label"] for button in answer["components"]] == ["Confirmar", "Cancelar"]


@pytest.mark.shared_contract("setup_features")
@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_the_greeting_fits_discord_rows(locale):
    """Broke as: every button was pinned to row 0, which fits five."""
    view = _greeting(locale)

    rows = [item.row for item in view.children]
    assert len(view.children) <= 25, "Discord fits five rows of five"
    assert max(row for row in rows if row is not None) <= 4, rows


async def test_the_first_page_reads_each_feature_document_once(configured, monkeypatch):
    """The congratulation needs every feature's state, so the first page reads
    each feature document once, all of them together."""
    from app.services import setup

    read = []
    from app.data import data

    cogs = data().guild_cogs
    original = cogs.find_cog_by_guild_id

    async def counted(guild_id, command_key):
        read.append(command_key)
        return await original(guild_id, command_key)

    monkeypatch.setattr(cogs, "find_cog_by_guild_id", counted)
    await setup.setup_dashboard(GUILD_ID)

    assert sorted(read) == sorted(spec["command_key"] for spec in Commands.SETUP_FEATURES)
