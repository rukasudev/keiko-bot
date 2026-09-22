"""The panel shows what the card shows, and nothing the card hides.

A card section can declare `visible-when`, and the card obeys it. The panel
did not: it listed every field of the card, so a section the card hides kept a
line on the panel, and a field marked hidden to keep that line away lost its
Edit with it. On the local bot (2026-09-22) that left the welcome banner's
custom image impossible to change after setup.

Shared behaviour: `manager.panel_rows`, read by every manager panel and by
every setup review. Consumer that exposed it: welcome_messages. Guaranteed: a
card key is on the panel exactly while the section that owns it is visible,
and while it is there it carries its own Edit.
"""

from types import SimpleNamespace

import discord
import pytest

from tests.behavioral.golden.paths.common import GUILD_ID, seed_document
from tests.behavioral.harness.locators import walk_items

pytestmark = pytest.mark.behavioral

IMAGE_LINE = "Imagem personalizada"
UPLOADED = "https://cdn.discordapp.com/novo.png"
SAVED = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "welcome_messages_channel": {"style": "channel", "values": "101"},
    "welcome_custom_image": "https://cdn.example/banner.png",
    "welcome_messages_title": "Oi!",
    "welcome_messages": {"style": "bullet", "values": "A;B"},
}


def _lines(view):
    return "\n".join(
        item.content
        for item in walk_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    )


async def _panel(scenario_factory, deps, design):
    seed_document(deps, "welcome_messages", {**SAVED, "welcome_design": design})

    async def send(*_args, **_kwargs):
        return SimpleNamespace(attachments=[SimpleNamespace(url=UPLOADED)])

    deps.bot.get_channel = lambda _channel_id: SimpleNamespace(send=send)
    return await scenario_factory(locale="pt-br").start_command("welcome_messages")


async def test_a_custom_design_puts_the_image_on_the_panel_with_its_own_edit(
    scenario_factory, deps
):
    scenario = await _panel(scenario_factory, deps, "custom_only")

    assert IMAGE_LINE in _lines(scenario.current_message.view)
    await scenario.click("section:welcome_config/image")
    await scenario.submit_file_upload(filename="novo.png", content=b"\x89PNG")
    document = deps.mongo_client.guild["welcome_messages"].find_one(
        {"guild_id": GUILD_ID}
    )
    assert document["welcome_custom_image"] == UPLOADED


async def test_the_server_design_leaves_no_image_line_behind(scenario_factory, deps):
    """The saved url outlives a design switch, so the row has to be the one
    that goes."""
    scenario = await _panel(scenario_factory, deps, "server_blur")

    assert IMAGE_LINE not in _lines(scenario.current_message.view)
    await scenario.finish()
