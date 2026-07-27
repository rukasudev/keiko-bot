"""Behavioral scenario: custom image upload on the birthday member card.

Drives the real FileUploadModal (master-only ui.Label + ui.FileUpload):
the fake attachment is read for real, re-uploaded to the (recorded) dump
channel, and the returned permanent URL must land in card state and in the
persisted birthday item.
"""
from types import SimpleNamespace

import pytest

from tests.behavioral.scenarios.test_reminders_birthday_flow import (
    YES_LABEL,
    _complete_global_card,
)

pytestmark = pytest.mark.behavioral

FAKE_CDN_URL = "https://cdn.discordapp.com/attachments/999/1/upload.png"


@pytest.fixture
def dump_channel(deps):
    """Recording dump channel behind bot.get_channel."""
    sent = []

    async def send(*args, **kwargs):
        sent.append(kwargs)
        return SimpleNamespace(attachments=[SimpleNamespace(url=FAKE_CDN_URL)])

    channel = SimpleNamespace(send=send, sent=sent)
    deps.bot.get_channel = lambda _channel_id: channel
    return channel


async def test_custom_image_upload_persists_permanent_url(
        scenario_factory, dump_channel):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)
    await scenario.click(YES_LABEL["pt-br"])
    await scenario.select_option("Tester")
    await scenario.confirm()

    await scenario.click("customize:0")               # month
    await scenario.select_option("05")
    await scenario.click("customize:1")               # day
    await scenario.submit_modal({"Dia": "12"})

    await scenario.click("customize:3")               # image -> FileUploadModal
    await scenario.submit_file_upload(filename="dog.png", content=b"\x89PNG-fake")

    assert dump_channel.sent, "attachment must be re-uploaded to the dump channel"
    card = scenario.current_message.view
    assert FAKE_CDN_URL in card.state.values(), (
        f"card state should hold the permanent URL, got {card.state}"
    )

    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.confirm()

    item = scenario.get_persisted(
        "reminders", "birthdays",
        {"guild_id": str(scenario.guild.id), "user_id": "555"},
    )
    assert item["image"]["mode"] == "custom"
    assert item["image"]["url"] == FAKE_CDN_URL
    await scenario.finish()
