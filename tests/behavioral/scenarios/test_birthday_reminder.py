"""The birthday message itself: what the server sees on the day."""
import pytest

from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
from app.services.moderations import update_moderations_by_guild
from app.webhooks import birthday_handler
from tests.mocks.discord import create_member

pytestmark = pytest.mark.behavioral


async def _celebrate(deps, monkeypatch, mm_dd="05-12"):
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    guild_id = str(deps.guild.id)
    channel = deps.guild.text_channels[0]
    if not deps.guild.get_member(555):
        create_member(deps.guild, id=555, name="Tester")
    upsert_birthday_config(
        guild_id,
        str(channel.id),
        False,
        timezone="America/Sao_Paulo",
        notification_time="08:00",
    )
    upsert_birthday_item(guild_id, "555", mm_dd)
    update_moderations_by_guild(guild_id, "reminders_birthday", True)

    await birthday_handler.process_birthday_webhook("reminder-1", mm_dd)
    return channel


async def test_the_birthday_message_arrives_with_a_party_reaction(deps, monkeypatch):
    """A celebration the server can join: the message carries a reaction."""
    channel = await _celebrate(deps, monkeypatch)

    sent = channel._sent_messages[-1]
    assert sent.embeds, "the birthday message is an embed"
    assert sent.reactions == ["🎉"]
