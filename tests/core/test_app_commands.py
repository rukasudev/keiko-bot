"""The app commands a reader of any server is offered, read from the tree the bot syncs:
each slash command to its leaf and each message menu, in the reader's language, marked
for everyone or for whoever configures Keiko."""

import discord
import pytest
from discord import app_commands

from app.core.app_commands import listed_commands
from app.core.commands import PARENT_GROUPS
from app.core.utils import ml
from tests.helpers.slash_tree import DISCORD_LOCALES, synced_tree

LOCALES = list(DISCORD_LOCALES)


def leaves(tree, locale):
    """The path of every slash command a member can run, in `locale`."""
    paths = tree.paths()
    return {
        typed[locale]
        for name, typed in paths.items()
        if not any(other.startswith(f"{name} ") for other in paths)
    }


def named(listed, name):
    return next(command for command in listed if command.name == name)


@pytest.mark.parametrize("locale", LOCALES)
async def test_every_slash_command_a_member_can_run_is_listed_once(locale):
    tree = await synced_tree()
    slash = [command.name for command in tree.listed[locale] if not command.menu]

    assert sorted(slash) == sorted(leaves(tree, locale))


@pytest.mark.parametrize("locale", LOCALES)
async def test_a_command_is_named_in_the_readers_language_and_in_english(locale):
    listed = (await synced_tree()).listed[locale]

    help_command = named(listed, f"/{ml('help.command.slash.name', locale)}")

    assert help_command.english == f"/{ml('help.command.slash.name', 'en-us')}"
    assert help_command.description == ml("help.command.slash.description", locale)


@pytest.mark.parametrize("locale", LOCALES)
async def test_a_message_menu_is_listed_by_its_label(locale):
    listed = (await synced_tree()).listed[locale]

    menu = named(listed, ml("translate.command.slash.name", locale))

    assert menu.menu is True
    assert menu.english == ml("translate.command.slash.name", "en-us")
    assert menu.configures is False


async def test_whoever_configures_keiko_gets_the_groups_setup_and_the_link_check():
    listed = (await synced_tree()).listed["en-us"]
    groups = tuple(
        f"/{ml(f'texts.groups.{group}', 'en-us')} " for group in PARENT_GROUPS
    )

    for command in listed:
        expected = command.english.startswith(groups) or command.english in (
            f"/{ml('setup.command.slash.name', 'en-us')}",
            ml("block_links.command.slash.commands.check.name", "en-us"),
        )
        assert command.configures is expected, command.english


@pytest.mark.parametrize("locale", LOCALES)
async def test_a_command_under_a_parent_group_names_that_group(locale):
    tree = await synced_tree()
    root_of = {typed[locale]: name.split()[0] for name, typed in tree.paths().items()}

    for command in tree.listed[locale]:
        root = None if command.menu else root_of[command.name]
        expected = root if root in PARENT_GROUPS else None
        assert command.group == expected, command.name


async def test_a_command_bound_to_one_server_is_never_listed():
    """The admin server's commands (/admin, /config, its inspection menus) are bound
    to that server; a reader anywhere else is never offered them."""
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))

    async def callback(interaction: discord.Interaction) -> None:
        """Answer nothing."""

    tree.add_command(
        app_commands.Command(name="ping", description="d", callback=callback)
    )
    tree.add_command(
        app_commands.Command(name="admin", description="d", callback=callback),
        guild=discord.Object(id=1),
    )

    listed = await listed_commands(tree, "en-us")

    assert [command.english for command in listed] == ["/ping"]
