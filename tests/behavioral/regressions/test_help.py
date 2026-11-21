"""/help (`/ajuda`): the list of Keiko's commands, answered only to whoever asked, with
the slash commands and the message apps everyone can run first and, for whoever takes
care of the server, what configures Keiko."""
import discord
import pytest

from app.commands.help.service import ExecuteCommandButton, send_help
from app.core.commands import PARENT_GROUPS
from app.core.utils import fill, ml
from app.forms.features import feature_keys
from tests.helpers.slash_tree import synced_tree
from tests.mocks.discord import create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("help"),
]

LINES = "help.ui.records.commands.lines"


def reader(scenario_factory, permissions, locale="pt-br"):
    """A scenario whose member holds `permissions` in a server Keiko may do anything in."""
    server = create_guild()
    server.me.guild_permissions = discord.Permissions.all()
    member = create_member(server, id=555, name="Reader")
    member.guild_permissions = permissions
    return scenario_factory(locale=locale, guild=server, user=member)


def shown_commands(value, listed):
    """The listed command each line of a field names, or the line itself when it names
    none of them."""
    return [
        next(
            (
                command.name
                for command in listed
                if f"`{command.name}`" in line or f"**{command.name}**" in line
            ),
            line,
        )
        for line in value.splitlines()
    ]


async def every_page(scenario, listed):
    """`/ajuda` typed by the scenario's member, every page turned: each field's title
    with the commands it names, the titles of each page, and the first answer."""
    await scenario.run(send_help, name="/ajuda")
    answer = scenario.outputs[-1]
    fields = {}
    pages = []
    while True:
        shown = scenario.outputs[-1]["embed"]["fields"]
        pages.append([field["name"] for field in shown])
        for field in shown:
            fields[field["name"]] = shown_commands(field["value"], listed)
        if scenario.component(">").disabled:
            return fields, pages, answer
        await scenario.click(">")


def group_title(group, locale):
    """A group's field title: its emoji and its name as members type it, capitalized."""
    name = ml(f"texts.groups.{group}", locale).capitalize()
    return fill(ml(f"{LINES}.group", locale), emoji=PARENT_GROUPS[group], name=name)


def under(group, listed, locale):
    """The commands a reader types under `group`."""
    path = f"/{ml(f'texts.groups.{group}', locale)} "
    return [command.name for command in listed if command.name.startswith(path)]


def menus_field(fields, locale):
    """The message apps field: its first line, then the menus it names."""
    first, *menus = fields[ml(f"{LINES}.menus", locale)]
    return first, sorted(menus)


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_help_lists_what_a_member_can_run(scenario_factory, locale):
    """Broke as: /help walked the bot's own list of slash commands and kept only those
    under /moderations, /notifications or /help, so a member never saw /birthday,
    /ping, /report, /support or the Translate Message menu (a menu was never in that
    list), got the groups' commands that only configure Keiko, and the list was posted
    for the whole channel.

    Shared behaviour: `listed_commands` (`app/core/app_commands.py`), the commands of
    the tree Keiko syncs, read by `send_help`. Consumer that exposed it: /ajuda typed by
    a member, on the PR9 front-door review.

    Guaranteed: a member's /help, answered only to them, lists every slash command
    anyone can run under the general commands on the first page, then every message
    app anyone can run under how to reach them on a page of their own, and nothing
    that configures Keiko.
    """
    listed = (await synced_tree()).listed[locale]
    anyone = [command for command in listed if not command.configures]

    fields, pages, answer = await every_page(
        reader(scenario_factory, discord.Permissions.none(), locale), listed
    )

    general = ml(f"{LINES}.general", locale)
    assert pages == [[general], [ml(f"{LINES}.menus", locale)]]
    assert sorted(fields[general]) == sorted(
        command.name for command in anyone if not command.menu
    )
    assert menus_field(fields, locale) == (
        ml(f"{LINES}.menus_path", locale),
        sorted(command.name for command in anyone if command.menu),
    )
    assert answer["ephemeral"] is True


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_help_adds_what_configures_keiko_for_whoever_manages_the_server(
    scenario_factory, locale
):
    """Broke as: /help listed no /setup, no /integrations command and no message menu
    to anyone, and gave a moderator the same list as a member.

    Shared behaviour: `listed_commands` (`app/core/app_commands.py`) and the one rule
    of who configures Keiko, `is_guild_admin` (`app/core/utils.py`), read by
    `send_help`. Consumer that exposed it: /ajuda typed by a moderator with Manage
    Server, on the PR9 front-door review.

    Guaranteed: whoever holds Manage Server gets, after the general commands, every
    message app, the link check among them, then the slash commands that configure
    Keiko outside a group under setup, then one field per parent group in /setup's
    order, titled by its emoji and its name, each listing every command under it,
    two fields a page, privately.
    """
    listed = (await synced_tree()).listed[locale]
    grouped = {group: under(group, listed, locale) for group in PARENT_GROUPS}
    in_a_group = {name for names in grouped.values() for name in names}

    fields, pages, answer = await every_page(
        reader(scenario_factory, discord.Permissions(manage_guild=True), locale), listed
    )

    titles = [
        ml(f"{LINES}.general", locale),
        ml(f"{LINES}.menus", locale),
        ml(f"{LINES}.setup", locale),
        *(group_title(group, locale) for group in PARENT_GROUPS),
    ]
    assert pages == [titles[index : index + 2] for index in range(0, len(titles), 2)]
    assert menus_field(fields, locale) == (
        ml(f"{LINES}.menus_path", locale),
        sorted(command.name for command in listed if command.menu),
    )
    assert sorted(fields[ml(f"{LINES}.setup", locale)]) == sorted(
        command.name
        for command in listed
        if command.configures and not command.menu and command.name not in in_a_group
    )
    for group, names in grouped.items():
        assert names and sorted(fields[group_title(group, locale)]) == sorted(names), group
    assert answer["ephemeral"] is True


async def features_offered(scenario):
    """`/ajuda` typed by the scenario's member, every command of every page picked:
    the features its Executar button opens, by the command's name."""
    await scenario.run(send_help, name="/ajuda")
    offered = {}
    while True:
        [select] = [
            item
            for item in scenario.current_message.registered_items
            if isinstance(item, discord.ui.Select)
        ]
        for name in [option.label for option in select.options]:
            await scenario.select_option(name)
            offered[name] = [
                item.command_key
                for item in scenario.current_message.registered_items
                if isinstance(item, ExecuteCommandButton)
            ]
            await scenario.click(ml("buttons.back.label", scenario.locale))
        if scenario.component(">").disabled:
            return offered
        await scenario.click(">")


async def test_every_feature_opens_from_the_help_for_whoever_configures_keiko(
    scenario_factory,
):
    """Broke as: picking /moderações aniversários lembretes in /ajuda showed no
    Executar button: the help took a command's feature from the name of the Python
    method behind it (`reminders`), while the birthdays feature is
    `reminders_birthday`.

    Shared behaviour: the feature `listed_commands` (`app/core/app_commands.py`)
    gives each listed command, read by the help's command select. Consumer that
    exposed it: /ajuda by a Manage Server moderator, on the PR9 round 4 review.

    Guaranteed: whoever configures Keiko finds every feature /setup offers behind
    exactly one command of /ajuda, whose Executar opens it; no other command offers
    Executar.
    """
    scenario = reader(scenario_factory, discord.Permissions(manage_guild=True))

    offered = await features_offered(scenario)

    opened = [key for keys in offered.values() for key in keys]
    assert sorted(opened) == sorted(feature_keys())
    assert all(len(keys) <= 1 for keys in offered.values())
