"""Contract: the slash-command copy Discord receives satisfies Discord's API limits,
which are only enforced at tree-sync time (bot startup).

Incident (2026-07-27): the block-links `desc:` was rewritten past 100
characters; every test stayed green because the suites enter at the
service seam and never build the command tree — the bot then failed to
start with "description_localizations.*: Must be between 1 and 100 in
length" for every Discord locale.

The contract builds the tree the bot syncs at startup, with
`app/translator.py` set, and checks each name and description of every
command, group, option and choice in every locale it is sent in. The copy
lives in each command's `copy.yml` (`slash.*` and the blocks its cogs name) and in
`app/languages/commands.yml` (`groups.*`, the months).

Discord (and discord.py) validate a context menu's callback the same way, only at
tree build time, so the tree also carries every context menu as its cog registers it,
and what configures Keiko shows only to whoever has Manage Server.
"""
import discord
import pytest

from tests.helpers.slash_tree import built_tree, synced_tree

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("command_copy")]

DESCRIPTION_LIMIT = 100   # Discord: command/group/option descriptions are 1..100
NAME_LIMIT = 32           # Discord: command/group/option names are 1..32, no spaces
CHOICE_LIMIT = 100        # Discord: choice names are 1..100
# Context-menu commands (see app/commands/translate/cog.py, block_links/cog.py):
# Discord allows mixed case and spaces in THEIR names, and no description.
CONTEXT_MENUS = {discord.AppCommandType.user.value, discord.AppCommandType.message.value}


def _localized(node, field):
    yield "default", node.get(field) or ""
    yield from (node.get(f"{field}_localizations") or {}).items()


def _entries(node, path, menu):
    """Every (where, kind, text) of a payload node and of the nodes below it."""
    here = f"{path} {node['name']}".strip()
    for locale, name in _localized(node, "name"):
        yield f"{here} [{locale}]", "menu" if menu else "name", name
    if not menu:
        for locale, description in _localized(node, "description"):
            yield f"{here} [{locale}]", "description", description
    for option in node.get("options") or []:
        yield from _entries(option, here, False)
    for choice in node.get("choices") or []:
        for locale, name in _localized(choice, "name"):
            yield f"{here} choice [{locale}]", "choice", name


def _broken(kind, text):
    if kind == "description":
        return not 1 <= len(text) <= DESCRIPTION_LIMIT
    if kind == "choice":
        return not 1 <= len(text) <= CHOICE_LIMIT
    too_long = not 1 <= len(text) <= NAME_LIMIT
    return too_long or (kind == "name" and " " in text)


@pytest.fixture
async def payloads():
    return (await synced_tree()).payloads


async def test_the_tree_carries_every_command_in_both_locales(payloads):
    localized = [
        payload["name"]
        for payload in payloads
        if {"en-US", "pt-BR"} <= set(payload.get("name_localizations") or {})
    ]

    assert payloads and len(localized) == len(payloads)


async def test_a_members_birthday_is_added_only_from_the_panel(payloads):
    """PR8 removed `/moderations birthdays add`: the panel's Add and `/birthday` are
    the two ways a birthday is saved, so the group holds the panel's command alone."""
    moderations = next(payload for payload in payloads if payload["name"] == "moderations")
    birthdays = next(
        group for group in moderations["options"] if group["name"] == "birthdays"
    )

    assert [command["name"] for command in birthdays["options"]] == ["reminders"]


async def test_every_name_and_description_fits_discord_limits(payloads):
    violations = [
        (where, kind, text)
        for payload in payloads
        for where, kind, text in _entries(payload, "", payload.get("type") in CONTEXT_MENUS)
        if _broken(kind, text)
    ]

    assert not violations, (
        "slash copy breaking Discord's limits (the bot will fail to start on sync): "
        f"{violations}"
    )


async def test_building_the_tree_leaves_no_background_task_failing():
    """The cogs' loops wait for a ready that never comes and stop with their cogs.

    Broke as: the tree was built on a bot that was never initialised, so the
    `before_loop` of every loop the cogs started (`wait_until_ready`) raised "Client
    has not been properly initialised"; the failure was never retrieved and asyncio
    logged it when the garbage collector ran, inside whatever test ran then (the Twitch
    fan-out tests assert no ERROR in their caplog and failed now and then).
    """
    loops = []
    await built_tree(loops)

    failed = [
        loop.coro.__qualname__
        for loop in loops
        if (task := loop.get_task()) is not None
        and task.done()
        and not task.cancelled()
        and task.exception() is not None
    ]

    assert loops and failed == []


@pytest.mark.parametrize(
    "name, kind",
    [
        pytest.param("setup", discord.AppCommandType.chat_input, id="configurar"),
        pytest.param("block-links-check", discord.AppCommandType.message, id="link-check"),
    ],
)
async def test_what_configures_keiko_shows_only_to_whoever_has_manage_server(
    payloads, name, kind
):
    """/configurar and the link check menu (a diagnostic that exposes exempt roles,
    channels and rules) show to whoever has Manage Server, the permission every gate
    reads since PR9 (D9-04), and stay hidden from members."""
    command = next(payload for payload in payloads if payload["name"] == name)

    assert command["type"] == kind.value
    assert command["default_member_permissions"] == discord.Permissions(manage_guild=True).value
