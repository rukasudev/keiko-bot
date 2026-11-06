"""Behavioral scenario: custom image upload on the birthday member card.

Drives the real FileUploadModal (ui.Label + ui.FileUpload):
the fake attachment is read for real, re-uploaded to the (recorded) dump
channel, and the returned permanent URL must land in card state and in the
persisted birthday item.
"""

from types import SimpleNamespace

import pytest

from tests.behavioral.golden.paths.reminders_birthday import _type_day
from tests.behavioral.scenarios.test_reminders_birthday_flow import (
    _setup,
    _to_member_card_from_the_panel,
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
    scenario_factory, dump_channel
):
    setup = await _setup(scenario_factory, "pt-br")
    scenario = await _to_member_card_from_the_panel(scenario_factory, setup, "pt-br")

    await scenario.select_option("05", target="month")
    await _type_day(scenario, "12")

    await scenario.click("customize:4")  # image -> FileUploadModal
    await scenario.submit_file_upload(filename="dog.png", content=b"\x89PNG-fake")

    assert dump_channel.sent, "attachment must be re-uploaded to the dump channel"
    assert FAKE_CDN_URL in scenario.card_state.values(), (
        f"card state should hold the permanent URL, got {scenario.card_state}"
    )

    await scenario.click("done")

    item = scenario.get_persisted(
        "reminders",
        "birthdays",
        {"guild_id": str(scenario.guild.id), "user_id": "555"},
    )
    assert item["image"]["mode"] == "custom"
    assert item["image"]["url"] == FAKE_CDN_URL
    await scenario.finish()


async def test_a_welcome_custom_image_is_required_only_for_custom_designs(
    scenario_factory, dump_channel
):
    """The image section only shows for a custom design, and Done asks for it."""
    from app.services.utils import ml

    scenario = await scenario_factory(locale="pt-br").start("welcome_messages")
    await scenario.confirm()
    await scenario.select_option("welcome", target="welcome_messages_channel")
    await scenario.select_option("custom_only", target="welcome_design")

    await scenario.click("done")
    scenario.expect_error(ml("buttons.summary-card.required", "pt-br").split(":")[0])

    await scenario.click("customize:3")
    await scenario.submit_file_upload(filename="banner.png", content=b"\x89PNG-fake")
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.finish()
