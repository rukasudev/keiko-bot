"""The /setup screen: the first thing most servers see of Keiko.

It was a dense embed: one bold line per feature, the command on the next line,
and a row of buttons far from the lines they belonged to. Its first card
redesign still read as busy: every row said its status three times (a coloured
circle, a word, the button) over three lines with three emoji. Then it listed
every feature on one card, which no longer fit Discord's forty components
once an eighth feature came (PR15): the card is now paged by category,
`ViewConstants.SETUP_PAGE_SIZE` features a page.

Guaranteed: a Components V2 card with Keiko's picture beside a dog-titled
header of two lines (the title and a one-sentence intro); its first page has
the server settings row, then one row per category in the order /configurar
declares, each a one-line brief and some of its commands beside a primary
Configurar; a category page lists only that category's features in declared
order, every row its name plus one subtext line with what it does and its
command, with no status circles; its status reads in its title; the button
reads Manage once set up and Set up otherwise; a server with everything set
up is congratulated in the intro; every page, even with thirteen paused
features in one category, stays inside Discord's limits in both locales with
room to spare; a row button opens the feature through the setup_dashboard
source and a setup saved from it ends on its category page; and the
greeting's dashboard button sends the same card, beside its language select
and its Help button, while the feature buttons of older greetings keep
answering.
"""
from dataclasses import replace
from itertools import cycle, islice

import discord
import pytest

from app.commands.help.service import ExecuteCommandButton, send_help
from app.commands.setup import service as setup
from app.commands.setup.service import (
    GreetingsView,
    LegacyGreetingButtons,
    category_page,
    command_facts,
    greeting_views,
    open_setup_dashboard,
    permission_report,
    setup_dashboard,
)
from app.constants import Commands, KeikoIcons, ViewConstants
from app.core import analytics, i18n
from app.core.commands import PARENT_GROUPS, setup_features
from app.core.manager import change_label
from app.core.utils import command_text, fill, join_names, ml
from app.ui.embed import card_copy
from tests.behavioral.flows import birthdays
from tests.behavioral.flows.block_links import ENABLED as BLOCK_LINKS
from tests.behavioral.flows.common import seed_document, seed_history
from tests.behavioral.flows.roles import ENABLED as DEFAULT_ROLES
from tests.behavioral.flows.twitch import ENABLED as TWITCH
from tests.behavioral.flows.welcome import ENABLED as WELCOME
from tests.behavioral.harness.copy_keys import copy_text
from tests.behavioral.harness.locators import walk_items
from tests.generators.moderations import recorded_server, seed_cog
from tests.helpers.components import button_labels, card_texts
from tests.helpers.discord_limits import MAX_COMPONENTS, assert_within_limits
from tests.helpers.permissions import EVERYTHING, guild
from tests.helpers.slash_tree import synced_tree
from tests.mocks.database import fake_store
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("setup_dashboard")]

GUILD_ID = "123456789"
LOCALES = ["pt-br", "en-us"]
CROWDED = 13
TEXTS = "setup.ui.texts"
HELP_LINES = "help.ui.records.commands.lines"


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
        enabled=("welcome_messages", "block_links"),
        paused=("block_links",),
    )


def listed(monkeypatch, count, state):
    """The first `count` features moved into the moderations category, each in `state`."""
    rows = [
        (replace(spec, category="moderations"), state)
        for spec in islice(cycle(setup_features()), count)
    ]

    async def states(guild_id):
        return rows

    monkeypatch.setattr(setup, "_states", states)
    return rows


@pytest.fixture
def crowded(monkeypatch):
    """Thirteen features in the moderations category, every one paused."""
    return listed(monkeypatch, CROWDED, "paused")


def speaks(locale):
    """The server's saved language, which every /configurar page is drawn in."""
    from app.core import cache

    recorded_server(fake_store(), GUILD_ID, language=locale)
    cache.reset()


async def dashboard(locale="pt-br"):
    speaks(locale)
    return await setup_dashboard(GUILD_ID)


async def page(category, locale="pt-br", number=0):
    speaks(locale)
    return await category_page(GUILD_ID, category, number)


def rows(view):
    return [
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Section)
        and isinstance(item.accessory, discord.ui.Button)
    ]


def text_of(section):
    return "\n".join(
        child.content
        for child in section.children
        if isinstance(child, discord.ui.TextDisplay)
    )


def feature(command_key):
    return next(spec for spec in setup_features() if spec.key == command_key)


def header_of(view):
    return next(
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Section)
        and isinstance(item.accessory, discord.ui.Thumbnail)
    )


def header(locale, all_configured=False):
    """The two lines of the first page's header, as its copy writes them."""
    facts = command_facts(all_configured=all_configured)
    copy = card_copy("setup.ui.cards.dashboard", locale, facts)
    return [f"## {copy.title}", copy.description]


def label(spec, locale="pt-br"):
    return command_text(spec, "command.setup.label", locale)


def slash(spec, locale):
    """The path a member types to reach `spec`'s command, in `locale`."""
    group = ml(f"texts.groups.{spec.group}", locale)
    subgroup = command_text(spec, "command.slash.subgroup", locale)
    name = command_text(spec, "command.slash.name", locale)
    return f"/{group} {subgroup} {name}"


def category_row(category, specs, locale):
    """A category's row on the first page: its name, its brief, then up to three of
    its commands and how many it left out."""
    shown = specs[: ViewConstants.SETUP_CATEGORY_EXAMPLES]
    lines = [
        f"{PARENT_GROUPS[category]} **{ml(f'{TEXTS}.categories.{category}', locale)}**",
        ml(f"{TEXTS}.briefs.{category}", locale),
        "",
        ml(f"{TEXTS}.top_commands", locale),
        *[f"- `{slash(spec, locale)}`" for spec in shown],
    ]
    if len(specs) > len(shown):
        lines.append(f"- {fill(ml('texts.and_more', locale), count=len(specs) - len(shown))}")
    return "\n".join(lines)


def feature_row(spec, locale, state=None):
    """A feature's row on its category page: its name and status, then what it does
    and its command as subtext."""
    name = label(spec, locale)
    if state is not None:
        name = f"{name} {ml(f'{TEXTS}.states.{state}', locale)}"
    description = command_text(spec, "command.setup.description", locale)
    return f"{spec.emoji} **{name}**\n-# {description} · `{slash(spec, locale)}`"


def in_category(category):
    return [spec for spec in setup_features() if spec.category == category]


def admin(scenario_factory, locale="pt-br", administrator=True):
    """A scenario whose member is an admin of a server Keiko may do everything in."""
    server = create_guild()
    server.me.guild_permissions = discord.Permissions.all()
    member = create_member(server, id=555, name="Admin")
    member.guild_permissions = (
        discord.Permissions.all() if administrator else discord.Permissions.none()
    )
    return scenario_factory(locale=locale, guild=server, user=member)


async def configurar(scenario):
    """`/configurar` typed by the scenario's member."""
    return await scenario.run(open_setup_dashboard, "slash", name="/configurar")


def beside(scenario, command_key):
    """The button beside the row of `command_key` on the screen."""
    return next(
        row.accessory
        for row in rows(scenario.current_message.view)
        if getattr(row.accessory, "command_key", None) == command_key
    )


async def turn_to(scenario, command_key):
    """The category's pages turned until the row of `command_key` shows."""
    following = ml("setup.ui.buttons.next.label", scenario.locale_str)
    while not any(
        getattr(row.accessory, "command_key", None) == command_key
        for row in rows(scenario.current_message.view)
    ):
        await scenario.click(next(
            item
            for item in walk_items(scenario.current_message.view)
            if isinstance(item, discord.ui.Button) and item.label == following
        ))


def category_button(scenario, category):
    """The Configurar beside `category`'s row on the first page."""
    name = f"**{ml(f'{TEXTS}.categories.{category}', scenario.locale_str)}**"
    return next(
        row.accessory for row in rows(scenario.current_message.view) if name in text_of(row)
    )


def last_answer(scenario):
    return next(
        event
        for event in reversed(scenario.outputs)
        if event["actor"] == "bot" and event["kind"] not in ("defer", "delete")
    )


@pytest.mark.parametrize("locale", LOCALES)
async def test_the_setup_screen_is_a_components_v2_card_with_keiko_on_the_right(
    configured, locale
):
    """Lucas (beta.2 review): the note under the intro repeated what the card
    and its Comandos button already say, so the header keeps two lines."""
    top = header_of(await dashboard(locale))

    assert [child.content for child in top.children] == header(locale), (
        "title and intro, no note"
    )
    assert top.accessory.media.url == KeikoIcons.IMAGE_01


@pytest.mark.parametrize("locale", LOCALES)
async def test_the_first_page_lists_the_server_settings_then_each_category(
    configured, locale
):
    """Lucas (PR15 rounds 2 to 4): the server settings come first, and each
    category says what it does, then, after a blank line, names up to three of
    its most used commands; a category with every command listed has no
    closing line."""
    view = await dashboard(locale)

    assert text_of(rows(view)[0]).split("\n")[0] == ml(
        "setup.ui.cards.dashboard.rows.settings.title", locale
    )
    assert [text_of(row) for row in rows(view)[1:]] == [
        category_row(category, in_category(category), locale)
        for category in PARENT_GROUPS
        if in_category(category)
    ]
    start = ml("setup.ui.buttons.start.label", locale)
    assert all(
        row.accessory.label == start
        and row.accessory.style is discord.ButtonStyle.primary
        for row in rows(view)
    ), "every row opens through a primary Configurar"


@pytest.mark.parametrize("locale", LOCALES)
async def test_a_category_row_says_how_many_more_commands_it_has(
    deps, monkeypatch, locale
):
    """Lucas (PR15 round 4): "e mais" alone did not say how many commands
    were left out, and it closed even a category whose commands were all listed;
    since round 7 (ux-556) it reads as plain text with the words of
    `texts.and_more`."""
    moved = [spec for spec, _state in listed(monkeypatch, 5, "pending")]

    row = text_of(rows(await dashboard(locale))[1])

    assert row == category_row("moderations", moved, locale)
    assert row.split("\n")[-1] == f"- {fill(ml('texts.and_more', locale), count=2)}"


@pytest.mark.parametrize("locale", LOCALES)
async def test_one_more_command_is_told_in_the_singular(deps, monkeypatch, locale):
    listed(monkeypatch, 4, "pending")

    closing = text_of(rows(await dashboard(locale))[1]).split("\n")[-1]

    assert closing == f"- {fill(ml('texts.and_more', locale), count=1)}"


@pytest.mark.parametrize("locale", LOCALES)
async def test_a_category_with_every_command_listed_has_no_closing_line(
    deps, monkeypatch, locale
):
    moved = [spec for spec, _state in listed(monkeypatch, 3, "pending")]

    closing = text_of(rows(await dashboard(locale))[1]).split("\n")[-1]

    assert closing == f"- `{slash(moved[-1], locale)}`"


@pytest.mark.parametrize("locale", LOCALES)
async def test_every_category_names_one_to_three_of_its_commands(configured, locale):
    heading = ml(f"{TEXTS}.top_commands", locale)
    for row in rows(await dashboard(locale))[1:]:
        _name, _brief, blank, title, *examples = text_of(row).split("\n")
        assert (blank, title) == ("", heading)
        named = [line for line in examples if line.startswith("- `/")]
        assert 1 <= len(named) <= 3, examples
        assert all(line.endswith("`") for line in named)
        assert len(examples) - len(named) <= 1, "at most one closing line"


@pytest.mark.parametrize("locale", LOCALES)
async def test_the_settings_row_says_what_the_screen_is_not_its_values(
    configured, locale
):
    """Lucas (PR15 round 3, item 1): the row reads like the category briefs,
    one line on what the screen is; the values live on the screen itself."""
    base = "setup.ui.cards.dashboard.rows.settings"

    assert text_of(rows(await dashboard(locale))[0]) == (
        f"{ml(f'{base}.title', locale)}\n{ml(f'{base}.description', locale)}"
    )


@pytest.mark.parametrize("category", ["moderations", "notifications"])
async def test_a_category_page_lists_only_its_features_in_declared_order(
    configured, category
):
    """Lucas (PR15 round 4, item 2): birthdays belong to the notifications,
    while their command stays under /moderações; read page after page."""
    declared = in_category(category)
    pages = -(-len(declared) // ViewConstants.SETUP_PAGE_SIZE)
    shown = [
        row.accessory.command_key
        for number in range(pages)
        for row in rows(await page(category, number=number))
    ]

    assert shown == [spec.key for spec in declared]
    assert "reminders_birthday" in [spec.key for spec in in_category("notifications")]


async def test_every_row_is_its_name_and_one_subtext_line_with_its_command(configured):
    for category in PARENT_GROUPS:
        for section in rows(await page(category)):
            spec = feature(section.accessory.command_key)
            name_line, subtext, *rest = text_of(section).split("\n")
            assert label(spec) in name_line
            assert subtext.startswith("-# "), "what it does reads as quiet subtext"
            assert command_text(spec, "command.setup.description", "pt-br") in subtext
            assert "`/" in subtext, "the command still teaches the shortcut"
            assert rest == [], "no health line on the row"


async def test_no_row_repeats_its_status_as_a_coloured_circle(configured):
    rendered = "\n".join(card_texts(await page("moderations")))

    assert not [circle for circle in ("🔴", "🟢", "⚪", "🟡") if circle in rendered]


@pytest.mark.parametrize("locale", LOCALES)
async def test_each_feature_says_its_status_in_its_title(configured, locale):
    """Lucas (PR15 round 3): the status reads in the title as an emoji, ✅ when
    on and ⏸️ when paused; a feature not set up has none, and the subtext is
    only what it does and its command."""
    by_key = {row.accessory.command_key: row for row in rows(await page("moderations", locale))}
    expected = {
        "welcome_messages": feature_row(feature("welcome_messages"), locale, "configured"),
        "default_roles": feature_row(feature("default_roles"), locale),
        "block_links": feature_row(feature("block_links"), locale, "paused"),
    }

    assert {key: text_of(by_key[key]) for key in expected} == expected
    assert ml(f"{TEXTS}.states.configured", locale) == "✅"
    assert ml(f"{TEXTS}.states.paused", locale) == "⏸️"


async def test_the_button_reads_manage_when_set_up_and_set_up_otherwise(configured):
    by_key = {
        row.accessory.command_key: row.accessory
        for row in rows(await page("moderations"))
    }
    start = ml("setup.ui.buttons.start.label", "pt-br")
    manage = ml("setup.ui.buttons.manage.label", "pt-br")

    assert by_key["default_roles"].label == start
    assert by_key["default_roles"].style is discord.ButtonStyle.primary
    assert by_key["welcome_messages"].label == manage
    assert by_key["welcome_messages"].style is discord.ButtonStyle.secondary
    assert by_key["block_links"].label == manage, "a paused feature is still set up"


@pytest.mark.parametrize("locale", LOCALES)
async def test_a_server_with_everything_set_up_is_congratulated(deps, locale):
    """Lucas (PR15 round 3): once every feature is set up, paused ones included,
    the intro congratulates the admin instead of inviting a first setup."""
    seed(deps, enabled=[spec.key for spec in setup_features()], paused=("block_links",))

    intro = [child.content for child in header_of(await dashboard(locale)).children][1]

    assert intro == header(locale, all_configured=True)[1]
    assert intro != header(locale, all_configured=False)[1]


async def test_a_server_with_one_feature_left_keeps_the_first_setup_intro(deps):
    every_key = [spec.key for spec in setup_features()]
    seed(deps, enabled=[key for key in every_key if key != "default_roles"])

    view = await dashboard()

    assert [child.content for child in header_of(view).children] == header("pt-br")


@pytest.mark.parametrize("locale", LOCALES)
async def test_every_page_of_a_crowded_category_fits_discord(crowded, deps, locale):
    pages = -(-CROWDED // ViewConstants.SETUP_PAGE_SIZE)
    counts = []
    for number in range(pages):
        view = await page("moderations", locale, number)
        assert_within_limits(view, f"moderations page {number + 1} {locale}")
        counts.append(len(list(walk_items(view))))

    assert max(counts) <= MAX_COMPONENTS - 5, f"room to spare: {counts}"


@pytest.mark.parametrize("locale", LOCALES)
async def test_the_first_page_of_a_crowded_server_fits_discord(crowded, deps, locale):
    assert_within_limits(await dashboard(locale), f"first page {locale}")


@pytest.mark.parametrize("locale", LOCALES)
async def test_the_category_page_a_setup_ends_on_fits_discord(crowded, deps, locale):
    speaks(locale)
    view = await category_page(GUILD_ID, "moderations", activated="reminders_birthday")

    assert_within_limits(view, f"category page after a setup {locale}")
    assert len(list(walk_items(view))) <= MAX_COMPONENTS - 5


@pytest.mark.parametrize("locale", LOCALES)
async def test_the_settings_screen_fits_discord(deps, locale):
    from app.commands.setup.service import settings_screen

    speaks(locale)
    assert_within_limits(await settings_screen(GUILD_ID), f"settings {locale}")


def test_every_category_a_feature_declares_is_listed_with_its_emoji():
    declared = {command.group for command in setup_features()}

    assert declared <= set(PARENT_GROUPS)
    assert all(PARENT_GROUPS.values())


async def test_a_row_button_opens_the_feature_through_the_setup_dashboard_source(
    scenario_factory, deps, analytics_events
):
    scenario = await configurar(admin(scenario_factory))
    await scenario.click(category_button(scenario, "moderations"))

    await scenario.click(beside(scenario, "default_roles"))

    scenario.expect_mode("setup")
    scenario.expect_message(title_contains=label(feature("default_roles")))
    opened = {
        event["type"]: event["props"].get("source")
        for event in analytics_events
        if event["type"] in ("command.invoked", "feature.setup_opened")
    }
    assert opened == {
        "command.invoked": "setup_dashboard",
        "feature.setup_opened": "setup_dashboard",
    }
    await scenario.finish()


@pytest.mark.parametrize(
    "permissions",
    [
        pytest.param(discord.Permissions(administrator=True), id="administrator"),
        pytest.param(discord.Permissions(manage_guild=True), id="manage-server"),
    ],
)
async def test_whoever_may_configure_keiko_sets_a_feature_up_from_configurar(
    configured, scenario_factory, permissions
):
    """Broke as: a moderator with Manage Server and no Administrator got "⚠️ Apenas
    Admins" on /configurar and on every button of it (PR9, D9-04).

    Shared behaviour: `is_guild_admin` (`app/core/utils.py`), the one rule of who
    configures Keiko, read by /configurar and by every button of its card. Consumer
    that exposed it: /configurar opened by a moderator with Manage Server, on the PR9
    front-door review.

    Guaranteed: whoever holds Administrator or Manage Server opens /configurar, a
    category of it and a feature's setup from that category.
    """
    scenario = admin(scenario_factory)
    scenario.user.guild_permissions = permissions
    await configurar(scenario)
    await scenario.click(category_button(scenario, "moderations"))

    await scenario.click(beside(scenario, "default_roles"))

    scenario.expect_mode("setup")


async def test_a_birthdays_setup_from_configurar_ends_on_the_notifications(
    scenario_factory, deps
):
    scenario = await configurar(admin(scenario_factory))
    await scenario.click(category_button(scenario, "notifications"))
    await turn_to(scenario, birthdays.FORM)
    await scenario.click(beside(scenario, birthdays.FORM))

    await scenario.confirm()
    await birthdays.complete_settings_card(scenario)
    await scenario.confirm()
    await scenario.finish()

    shown = card_texts(scenario.current_message.view)
    name = ml(f"{TEXTS}.categories.notifications", "pt-br")
    assert shown[0] == f"## {PARENT_GROUPS['notifications']} {name}"
    row = text_of(
        next(
            row
            for row in rows(scenario.current_message.view)
            if row.accessory.command_key == birthdays.FORM
        )
    )
    assert row == feature_row(feature(birthdays.FORM), "pt-br", "activated")
    assert scenario.live_messages == [scenario.current_message], "one message"


@pytest.mark.parametrize(
    "permissions",
    [
        pytest.param(discord.Permissions(administrator=True), id="administrator"),
        pytest.param(discord.Permissions(manage_guild=True), id="manage-server"),
    ],
)
async def test_the_greeting_dashboard_button_sends_the_same_screen(
    configured, scenario_factory, permissions
):
    """A moderator with Manage Server and no Administrator opens it too (PR9, D9-04)."""
    scenario = admin(scenario_factory)
    scenario.user.guild_permissions = permissions
    scenario.post(GreetingsView("pt-br"))

    await scenario.click("greetings:setup:dashboard")

    answer = last_answer(scenario)
    assert card_texts(scenario.current_message.view)[:2] == header("pt-br")
    assert answer["ephemeral"] is True
    assert answer["components_v2"] and "embed" not in answer, (
        "a Components V2 message carries no embed"
    )


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
        (ml("buttons.help.label", "pt-br"), "📚"),
        (ml("buttons.history.label", "pt-br"), "📜"),
        (ml("setup.ui.buttons.permissions.label", "pt-br"), "🩺"),
        (ml("buttons.support.label", "pt-br"), "💬"),
    ]
    support = buttons[-1]
    assert support.style is discord.ButtonStyle.link
    assert support.url == Commands.SUPPORT_SERVER_URL


async def test_stream_elements_commands_is_listed_on_setup(deps):
    """Broke as: the StreamElements integration was a command nobody found."""
    view = await page("integrations")
    by_key = {row.accessory.command_key: row for row in rows(view)}

    row = by_key["stream_elements_commands"]
    assert text_of(row) == feature_row(feature("stream_elements_commands"), "pt-br")


async def help_opened(scenario):
    """`/ajuda` typed by the scenario's member: the command list on screen."""
    await scenario.run(send_help, name="/ajuda")
    return scenario


async def help_for(scenario_factory, permissions):
    """`/ajuda` typed by a member holding `permissions`: the command list on screen."""
    scenario = admin(scenario_factory)
    scenario.user.guild_permissions = permissions
    return await help_opened(scenario)


async def listed_command(**wanted):
    """The first command a reader is listed in pt-br with every attribute of `wanted`."""
    return next(
        command
        for command in (await synced_tree()).listed["pt-br"]
        if all(getattr(command, name) == value for name, value in wanted.items())
    )


async def page_listing(scenario, command):
    """The pages of the command list turned until one lists `command`."""
    kind = "message_command" if command.menu else "slash_command"
    line = fill(ml(f"{HELP_LINES}.{kind}", "pt-br"), name=command.name)
    while not any(
        line in field["value"] for field in last_answer(scenario)["embed"]["fields"]
    ):
        await scenario.click(">")


async def test_help_mentions_setup_and_its_button_opens_the_dashboard_for_admins(
    configured, scenario_factory
):
    """Broke as: /help never told an admin that /setup holds every feature."""
    scenario = await help_opened(admin(scenario_factory))
    listing = last_answer(scenario)["embed"]
    dashboard_label = ml("setup.ui.buttons.dashboard.label", "pt-br")
    offered = [
        item
        for item in scenario.current_message.registered_items
        if isinstance(item, discord.ui.Button) and item.label == dashboard_label
    ]

    assert "`/configurar`" in listing["description"]
    assert len(offered) == 1
    await scenario.click(offered[0])
    sent = last_answer(scenario)
    assert card_texts(scenario.current_message.view)[0] == header("pt-br")[0]
    assert sent["ephemeral"] is True


@pytest.mark.parametrize(
    "permissions,offered",
    [
        (discord.Permissions(administrator=True), True),
        (discord.Permissions(manage_guild=True), True),
        (discord.Permissions.none(), False),
    ],
    ids=["administrator", "manage-server", "member"],
)
async def test_the_help_offers_configurar_only_to_whoever_configures_keiko(
    configured, scenario_factory, permissions, offered
):
    """A member's /ajuda shows no Configurar, a button that could only tell them they
    need the Manage Server permission (PR9, D9-06 A), and its intro neither points
    them at /configurar nor promises to run the command they pick."""
    scenario = await help_for(scenario_factory, permissions)

    intro = "description" if offered else "overrides.0.description"
    description = last_answer(scenario)["embed"]["description"]
    assert description == copy_text(f"help.ui.records.commands.{intro}", "pt-br")
    setup_command = f"`/{ml('setup.command.slash.name', 'pt-br')}`"
    assert (setup_command in description) is offered
    labels = button_labels(scenario.current_message)
    assert (ml("setup.ui.buttons.dashboard.label", "pt-br") in labels) is offered


async def test_a_picked_command_shows_its_description_and_a_way_back(
    scenario_factory,
):
    personal = "reminders_birthday.command.slash.commands.personal.name"
    birthday = await listed_command(english=f"/{ml(personal, 'en-us')}")
    scenario = await help_for(scenario_factory, discord.Permissions.none())

    await scenario.select_option(birthday.name)

    [field] = last_answer(scenario)["embed"]["fields"]
    assert field["name"] == fill(ml(f"{HELP_LINES}.picked", "pt-br"), name=birthday.name)
    assert field["value"] == birthday.description
    assert button_labels(scenario.current_message) == [ml("buttons.back.label", "pt-br")]


async def test_a_picked_message_menu_says_where_to_find_it(scenario_factory):
    menu = await listed_command(menu=True, configures=False)
    scenario = await help_for(scenario_factory, discord.Permissions.none())
    await page_listing(scenario, menu)

    await scenario.select_option(menu.name)

    [field] = last_answer(scenario)["embed"]["fields"]
    assert field["name"] == fill(ml(f"{HELP_LINES}.picked", "pt-br"), name=menu.name)
    assert field["value"].splitlines() == [
        ml(f"{HELP_LINES}.menus_path", "pt-br"),
        fill(ml(f"{HELP_LINES}.message_command", "pt-br"), name=menu.name),
    ]


async def test_a_picked_feature_offers_to_open_it_to_whoever_configures_keiko(
    scenario_factory,
):
    block_links = await listed_command(key="block_links")
    scenario = await help_for(scenario_factory, discord.Permissions(manage_guild=True))
    await page_listing(scenario, block_links)

    await scenario.select_option(block_links.name)

    assert button_labels(scenario.current_message) == [
        ml("buttons.back.label", "pt-br"),
        ml("help.ui.buttons.execute.label", "pt-br"),
    ]


def test_the_help_runs_a_command_from_a_blue_button_without_an_emoji():
    """A blue ▶️ inside a green button looked broken; the run button is blue with its label
    only (PR15 rounds 6 and 7, m03)."""
    button = ExecuteCommandButton("block_links", "pt-br")

    assert (button.label, button.emoji, button.style) == (
        ml("help.ui.buttons.execute.label", "pt-br"),
        None,
        discord.ButtonStyle.primary,
    )


async def history_of(scenario):
    """The /configurar History button pressed: the fields of the changes it lists."""
    await configurar(scenario)
    await scenario.click("history")
    return last_answer(scenario)["embed"]["fields"]


async def test_the_history_lists_every_feature_newest_first_and_names_each(
    deps, scenario_factory
):
    """The /setup History button: the server's recent changes across features.

    Each feature kept its own history behind its own panel, so an admin who wanted
    to know what changed in the server had to open six panels. Guaranteed: the
    history merges every feature's audit records, newest first, and every change
    is a numbered field named by the feature it happened in.
    """
    seed_history(deps, "welcome_messages", "enabled")
    seed_history(deps, "block_links", "enabled", "paused")

    fields = await history_of(admin(scenario_factory))

    names = [field["name"] for field in fields]
    paused = ml("commands.records.history.lines.paused", "pt-br")
    enabled = ml("commands.records.history.lines.enabled", "pt-br")
    assert names[0] == f"1. {change_label('block_links', 'pt-br')} · {paused}"
    assert [name.split(". ", 1)[0] for name in names] == ["1", "2", "3"]
    welcome = change_label("welcome_messages", "pt-br")
    assert any(name.endswith(f". {welcome} · {enabled}") for name in names)


async def test_the_history_lists_the_server_settings_changed_on_the_settings_screen(
    deps, scenario_factory
):
    """Broke as: a server setting changed on the /setup settings screen was written
    to `events.settings`, a collection nothing read, so the history of /setup never
    showed it. Guaranteed: the history reads the server's changes in one query, the
    settings' among them, each line named by what it changed."""
    seed_history(deps, "block_links", "enabled")
    analytics.record(
        "config.changed",
        guild_id=GUILD_ID,
        cog=Commands.SERVER_SETTINGS_KEY,
        actor=analytics.actor("user", "555"),
        props={"source": "setup_dashboard", "changed_keys": ["language"]},
    )
    await analytics.written()

    fields = await history_of(admin(scenario_factory))

    assert len(fields) == 2, "the setting and the feature, in one list"
    settings = ml("texts.server_settings", "pt-br")
    assert fields[0]["name"].startswith(f"1. {settings} · "), (
        "the settings row is named in plain text, like every feature's row"
    )
    assert fields[1]["name"].startswith(f"2. {change_label('block_links', 'pt-br')} · ")
    assert fields[0]["value"].startswith("<@555> · <t:")


PERMISSIONS_BASE = "setup.ui.responses.permissions"
PROBLEMS = "texts.permissions"


def configure(deps, *documents):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": GUILD_ID, **{form: True for form, _ in documents}}
    )
    for form, document in documents:
        seed_document(deps, form, document)


async def report(server, locale="pt-br"):
    return await permission_report(
        guild=server, guild_id=GUILD_ID, user_id="555", locale=locale
    )


def field_name(command_key, locale="pt-br"):
    spec = feature(command_key)
    return f"{spec.emoji} {label(spec, locale)}"


def field_of(embed, command_key, locale="pt-br"):
    name = field_name(command_key, locale)
    return next(field.value for field in embed.fields if field.name == name)


def names(permission, locale="pt-br"):
    return ml(f"texts.permission_names.{permission}", locale)


def fine(locale="pt-br"):
    return ml(f"{PERMISSIONS_BASE}.lines.ok", locale)


@pytest.fixture
def everything_configured(deps):
    configure(
        deps,
        ("welcome_messages", WELCOME),
        ("default_roles", DEFAULT_ROLES),
        ("block_links", BLOCK_LINKS),
        ("notifications_twitch", TWITCH),
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

    checked = ["welcome_messages", "default_roles", "block_links", "notifications_twitch"]
    assert [(field.name, field.value) for field in embed.fields] == [
        (field_name(key), fine()) for key in checked
    ], "only configured features are checked, in declared order"
    declared = [spec.key for spec in setup_features()]
    assert checked == sorted(checked, key=declared.index)


async def test_a_missing_channel_permission_names_the_channel_and_the_permission(
    everything_configured,
):
    channels = {**FULL_CHANNELS, 101: EVERYTHING - {"embed_links"}}

    value = field_of(await report(guild(channels, LOW_ROLES)), "welcome_messages")

    assert value == fill(
        ml(f"{PROBLEMS}.channel", "pt-br"), permission=names("embed_links"), channel="<#101>"
    )


async def test_a_deleted_channel_is_reported(everything_configured):
    channels = {100: EVERYTHING, 101: EVERYTHING}

    value = field_of(await report(guild(channels, LOW_ROLES)), "notifications_twitch")

    assert value == ml(f"{PROBLEMS}.channel_gone", "pt-br")


async def test_a_role_above_keiko_and_a_missing_server_permission_are_reported(
    everything_configured,
):
    server = guild(FULL_CHANNELS, {201: 1, 202: 20}, server=EVERYTHING - {"manage_roles"})

    value = field_of(await report(server), "default_roles")

    assert value.split("\n") == [
        fill(ml(f"{PROBLEMS}.server", "pt-br"), permission=names("manage_roles")),
        fill(ml(f"{PROBLEMS}.role_above", "pt-br"), role="202"),
    ], "a role above mine is one I cannot give; 201 sits below it"


async def test_the_exempt_channels_and_roles_of_block_links_need_nothing(deps):
    configure(deps, ("block_links", BLOCK_LINKS))

    embed = await report(guild({100: set()}, {201: 99}))

    assert field_of(embed, "block_links") == fine()


async def test_block_links_without_manage_messages_is_reported(deps):
    configure(deps, ("block_links", BLOCK_LINKS))

    embed = await report(guild({100: set()}, server=EVERYTHING - {"manage_messages"}))

    assert names("manage_messages") in field_of(embed, "block_links")


async def test_a_server_with_nothing_set_up_has_nothing_to_check(deps):
    embed = await report(guild({}))

    assert embed.fields == []
    assert embed.description == ml(f"{PERMISSIONS_BASE}.empty", "pt-br")


def greeting_texts(locale):
    """Every text of the greeting card a server gets in `locale`, top to bottom."""
    return card_texts(GreetingsView(locale))


@pytest.mark.parametrize("locale", LOCALES)
def test_the_greeting_is_a_card_with_keiko_beside_its_title(locale):
    """#52 (D9-01 A, PR9): the greeting is drawn like /configurar, a card with
    Keiko's picture beside its title and intro, and today's text under it in three
    blocks, each under a separator: the tricks, how to start and where the help is.
    The embed had no closing line, and the card has none."""
    view = GreetingsView(locale)
    copy = card_copy("setup.ui.cards.greeting", locale)
    rows = "setup.ui.cards.greeting.rows"

    top = header_of(view)
    assert [child.content for child in top.children] == [f"## {copy.title}", copy.description]
    assert top.accessory.media.url == KeikoIcons.IMAGE_01
    card = view.children[0]
    assert [type(item) for item in card.children] == [
        discord.ui.Section,
        *[discord.ui.Separator, discord.ui.TextDisplay] * 3,
    ], "three blocks under separators, no closing line"
    assert greeting_texts(locale)[3:] == [
        ml(f"{rows}.start.description", locale),
        ml(f"{rows}.help.description", locale),
    ]


@pytest.mark.parametrize("locale", LOCALES)
def test_the_greeting_offers_the_language_then_setup_and_help(locale):
    """Lucas (beta.2 review, PR15 round 4): seven feature buttons crowded the
    greeting, which /setup already lists. Under its card the greeting offers the
    language select, then Configurar and Ajuda, the help button /configurar
    carries, both readable in the chosen language."""
    card, languages, buttons = GreetingsView(locale).children

    assert isinstance(card, discord.ui.Container)
    [select] = languages.children
    assert isinstance(select, discord.ui.Select)
    assert [(button.label, button.emoji.name) for button in buttons.children] == [
        (ml("setup.ui.buttons.dashboard.label", locale), "🔧"),
        (ml("buttons.help.label", locale), "📚"),
    ]


@pytest.mark.parametrize("locale", LOCALES)
def test_the_greeting_points_at_its_buttons_by_their_labels(locale):
    message = "\n".join(greeting_texts(locale))

    assert f"**{ml('setup.ui.buttons.dashboard.label', locale)}**" in message
    assert f"**{ml('buttons.help.label', locale)}**" in message


@pytest.mark.parametrize("locale", LOCALES)
def test_the_greeting_lists_each_category_with_its_configurar_features(locale):
    """Lucas (PR15 round 3 and round 4): the greeting led Integrations with 🎮
    while /configurar shows 🔌, listed birthdays under moderation and ended
    with a Base line of no feature. Each category line is now drawn from the
    features /configurar places in it, by their names, so the two cannot drift."""
    lines = "\n".join(greeting_texts(locale)).split("\n")

    for category, emoji in PARENT_GROUPS.items():
        name = ml(f"{TEXTS}.categories.{category}", locale)
        start = f"{emoji} **{name}:** "
        [line] = [line for line in lines if line.startswith(start)]
        features = join_names([label(spec, locale) for spec in in_category(category)], locale)
        assert line == f"{start}{features}."
    assert not any(
        line.startswith(("🎮", "🧰")) for line in lines
    ), "no emoji /configurar does not show, no line of no feature"


@pytest.mark.parametrize(
    "system, closed, lands",
    [
        pytest.param(2, False, 2, id="its-system-channel"),
        pytest.param(2, True, 0, id="system-channel-closed-to-keiko"),
        pytest.param(None, False, 0, id="no-system-channel"),
    ],
)
async def test_the_greeting_lands_in_the_system_channel_when_keiko_may_write_there(
    deps, system, closed, lands
):
    """D9-02 A (PR9): the greeting goes where Discord posts its own welcome
    messages, the server's system channel, when Keiko may write there; otherwise in
    the first channel he may write in, as before."""
    speaks("pt-br")
    server = create_guild()
    server.system_channel = None if system is None else server.text_channels[system]
    if closed:
        server.system_channel.denied.add("send_messages")

    await GreetingsView().send(server)

    posted = [len(channel.get_all_messages()) for channel in server.text_channels]
    assert posted == [int(index == lands) for index in range(len(posted))]


async def test_a_language_pick_turns_an_old_embed_greeting_into_the_card(
    scenario_factory, deps
):
    """A greeting posted before PR9 is an embed above the same select, which the
    registered card view answers: the first pick turns that message into the card
    in the language picked, with no embed left on it."""
    scenario = admin(scenario_factory)
    old = scenario.post(GreetingsView(), embed=discord.Embed(title="Hello, I'm Keiko"))

    await scenario.select_option("pt-BR")

    copy = card_copy("setup.ui.cards.greeting", "pt-br")
    assert old.embeds == []
    assert card_texts(old.view)[:2] == [f"## {copy.title}", copy.description]
    assert last_answer(scenario)["components_v2"]


async def test_an_old_greeting_discord_will_not_turn_into_the_card_answers_privately(
    scenario_factory, deps
):
    """Discord may refuse to turn an embed message into a Components V2 one: the
    clicker then gets the card in the language picked, only for them, and the old
    greeting stays as it was."""
    scenario = admin(scenario_factory)
    old = scenario.post(GreetingsView(), embed=discord.Embed(title="Hello, I'm Keiko"))
    scenario.discord_refuses("response.edit_message", times=1)

    await scenario.select_option("pt-BR")

    copy = card_copy("setup.ui.cards.greeting", "pt-br")
    answer = last_answer(scenario)
    assert (answer["kind"], answer["ephemeral"], answer["components_v2"]) == (
        "send",
        True,
        True,
    )
    assert card_texts(scenario.current_message.view)[:2] == [f"## {copy.title}", copy.description]
    assert old.embeds != []


@pytest.mark.parametrize("locale", LOCALES)
def test_a_refused_setting_has_a_message_for_each_server_setting_only(locale):
    """The mention left the server settings, and its refusal stayed behind."""
    prefix = "setup.ui.errors.refused_"
    refused = {
        key[len(prefix) :].split(".")[0] for key in i18n.keys(locale) if key.startswith(prefix)
    }

    assert sorted(refused) == ["language", "prefix", "timezone"]


@pytest.mark.shared_contract("setup_features")
def test_every_button_an_old_greeting_carries_still_answers():
    """Greetings already posted keep one button per feature: Discord sends back
    only the custom_id, so a button no registered view owns would fail with
    "This interaction failed". Guaranteed: the views registered at start-up own
    every id a greeting ever carried, and each feature id opens that feature."""
    owned = {
        item.custom_id: item
        for view in greeting_views()
        for item in walk_items(view)
        if isinstance(item, (discord.ui.Button, discord.ui.Select))
    }
    for spec in setup_features():
        button = owned[f"greetings:setup:{spec.key}"]
        assert button.command_key == spec.key
    for custom_id in (
        "greetings:setup:dashboard",
        "greetings:commands",
        "greetings:language_select",
    ):
        assert custom_id in owned, custom_id
    assert all(view.is_persistent() for view in greeting_views()), (
        "discord.py registers only a view with no timeout and an id on every control"
    )


async def test_the_greeting_help_button_opens_the_command_list(scenario_factory, deps):
    scenario = admin(scenario_factory)
    scenario.post(GreetingsView("pt-br"))

    await scenario.click("greetings:commands")

    answer = last_answer(scenario)
    assert answer["ephemeral"] is True
    assert answer["embed"]["title"] == ml("help.ui.records.commands.title", "pt-br")


async def test_an_old_greeting_feature_button_opens_its_feature(scenario_factory, deps):
    scenario = admin(scenario_factory)
    scenario.post(LegacyGreetingButtons())

    await scenario.click("greetings:setup:default_roles")

    answer = last_answer(scenario)
    scenario.expect_mode("setup")
    assert answer["ephemeral"] is True
    assert label(feature("default_roles")) in answer["embed"]["title"]
    assert [button["label"] for button in answer["components"]] == [
        ml("buttons.confirm.label", "pt-br"),
        ml("buttons.cancel.label", "pt-br"),
    ]


@pytest.mark.shared_contract("setup_features")
@pytest.mark.parametrize("locale", LOCALES)
def test_the_greeting_fits_discord_rows(locale):
    """Broke as: every button was pinned to row 0, which fits five. The card holds
    its select and its buttons in rows of their own, and stays inside Discord's
    forty components and 4000 characters with every feature /configurar offers."""
    assert_within_limits(GreetingsView(locale), f"greeting card ({locale})")


async def test_the_first_page_reads_each_feature_document_once(configured, monkeypatch):
    """The congratulation needs every feature's state, so the first page reads
    each feature document once, all of them together."""
    from app.data import data

    read = []
    cogs = data().guild_cogs
    original = cogs.find_cog_by_guild_id

    async def counted(guild_id, command_key):
        read.append(command_key)
        return await original(guild_id, command_key)

    monkeypatch.setattr(cogs, "find_cog_by_guild_id", counted)
    await setup_dashboard(GUILD_ID)

    assert sorted(read) == sorted(spec.key for spec in setup_features())
