"""The block links answer pings who the admin wrote, and its error keeps no member's link.

Broke as: the answer was sent with discord.py's default mentions, so a stricter
default for the whole bot (PR5 sets one) would have silenced the `@everyone`, the
role and the member an admin wrote into it; its placeholder was filled by its own
`str.replace` instead of the filler every notifier shares; and when Keiko could
not delete a message, the error context carried the member's links themselves,
query strings and tokens included, to the error channel and `guild.logs`.

Shared behaviour: `block_links.check_message` (every message of every server with
block links on), `fill_placeholders` and the error context of a failed delete.
Consumer that exposed it: block_links (the review of v1 round 1, I4, I5 and S6).

Guaranteed: the answer names the member through `{user}` and is sent with every
mention allowed, the ones the admin wrote included; the error context of a
failed delete names the blocked websites and how many links there were, never a
link.
"""
import discord
import pytest

from app.services import block_links
from tests.mocks import create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "123456789"

COG = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "allowed_roles": {"values": []},
    "allowed_chats": {"values": []},
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "custom_links": {"style": "composition", "values": []},
    "answer": "{user}, nada de links por aqui! @everyone <@&999>",
}


async def test_the_answer_pings_the_member_and_everyone_the_admin_wrote(
    mock_cache, channel, member
):
    mock_cache.return_value = dict(COG)
    message = create_message(channel, member, "https://spam-site.com")

    await block_links.check_message(GUILD_ID, message)

    sent = channel._send.await_args.kwargs
    assert sent["content"] == f"{member.mention}, nada de links por aqui! @everyone <@&999>"
    mentions = sent["allowed_mentions"]
    assert (mentions.everyone, mentions.users, mentions.roles) == (True, True, True)


async def test_a_failed_delete_names_the_websites_never_the_links(
    mock_cache, channel, member, monkeypatch
):
    mock_cache.return_value = dict(COG)
    message = create_message(
        channel,
        member,
        "https://spam-site.com/convite?token=s3cr3t https://spam-site.com/b https://x.io/c",
    )
    message._delete.side_effect = discord.Forbidden(
        type("Response", (), {"status": 403, "reason": "Forbidden"})(),
        {"code": 50013, "message": "Missing Permissions"},
    )
    logged = []
    monkeypatch.setattr(
        block_links.logger, "error", lambda *args, **kwargs: logged.append(kwargs)
    )

    with pytest.raises(discord.Forbidden):
        await block_links.check_message(GUILD_ID, message)

    extra = logged[0]["context"].extra
    assert extra["blocked_hosts"] == ["spam-site.com", "x.io"]
    assert extra["blocked_count"] == 3
    copied = {
        key: value for key, value in extra.items()
        if key != "message_preview" and "s3cr3t" in str(value)
    }
    assert copied == {}, f"the error context copied a member's link: {copied}"
