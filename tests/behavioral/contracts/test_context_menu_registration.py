"""Contract: context menus must be buildable exactly as the cog registers them.

Discord (and discord.py) only validate a context-menu callback at tree build
time, i.e. at bot startup. The suite enters at the service seam and would
happily stay green while the bot refused to boot, which is the same class of
incident test_slash_command_copy.py was written for.
"""
import discord
import pytest
from discord import app_commands

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("command_copy")]


def test_block_links_check_context_menu_builds_with_its_real_callback(deps):
    from app.cogs.moderations.block import Block
    from app.translator import locale_str

    block = Block(bot=deps.bot)

    menu = app_commands.ContextMenu(
        name=locale_str(
            "block-links-check", type="context-menu", namespace="block-links-check"
        ),
        callback=block.validate_block_link,
        type=discord.AppCommandType.message,
    )

    assert menu.type is discord.AppCommandType.message
    assert menu.default_permissions is not None and menu.default_permissions.administrator, (
        "the diagnostic exposes exempt roles, channels and rules: it stays admin only"
    )
