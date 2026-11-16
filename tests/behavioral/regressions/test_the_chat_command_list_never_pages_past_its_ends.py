"""The StreamElements command list asked in chat opens on page 1 with no way before it.

Broke as: `ks!commands` replied through `PaginationWithoutInteractionView`, a copy of
`PaginationView` that drew its first page without disabling any arrow, so `<` and
`|<` were live on page 1: a press went to "Page 0 / 2" and on to the pages counted
from the end.

Shared behaviour: `PaginationView` (`app/ui/pagination.py`), which now also replies to
a chat message (`reply`), and `RecordsBrowser.reply` (`app/ui/records.py`), which
draws a records list as that reply. Consumer that exposed it:
stream_elements_commands (the duplicate view's removal).

Guaranteed: the chat list opens with its back arrows disabled, its forward arrows
lead to the last page and stop there, and each press edits the reply itself.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.constants import ViewConstants
from app.core.events import Events
from app.integrations.stream_elements import StreamElementsClient
from tests.behavioral.regressions.test_stream_elements_reply import (
    ask,
    english_guild,
    member_without_a_picture,
)
from tests.mocks.discord import MockInteraction, create_message

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]


@pytest.fixture
def two_pages_of_commands(mock_cache, monkeypatch):
    """`gaules` answers one command more than a page holds."""

    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {
                "streamer": "gaules",
                "state": {"channel_id": "se-gaules"},
                "enabled": True,
            }
        return {}

    mock_cache.side_effect = configured
    commands = [
        {"command": f"command{index}", "reply": f"reply {index}", "enabled": True}
        for index in range(ViewConstants.COMMANDS_PAGE_SIZE + 1)
    ]
    monkeypatch.setattr(
        StreamElementsClient, "get_chat_commands", staticmethod(lambda channel_id: commands)
    )


def _arrows(view):
    return [
        view.first_page_button.disabled,
        view.prev_button.disabled,
        view.next_button.disabled,
        view.last_page_button.disabled,
    ]


async def test_the_chat_list_opens_on_page_one_and_stops_at_the_last(
    deps, two_pages_of_commands
):
    guild = english_guild()
    member = member_without_a_picture(guild)
    sent = MagicMock(edit=AsyncMock())

    async def replied(*_args, **_kwargs):
        return sent

    message = create_message(
        content="ks!commands", author=member, channel=guild.text_channels[0]
    )
    message._reply.side_effect = replied

    await Events(deps.bot).on_message(message)

    reply = message._reply.await_args.kwargs
    view = reply["view"]
    assert reply["embed"].footer.text == "• Page 1 / 2"
    assert _arrows(view) == [True, True, False, False]

    await view.next_button.callback(MockInteraction(member, guild))

    shown = sent.edit.await_args.kwargs
    assert shown["embed"].footer.text == "• Page 2 / 2"
    assert _arrows(shown["view"]) == [False, False, True, True]


async def test_a_list_of_one_page_offers_no_arrow(deps, mock_cache, monkeypatch):
    def configured(guild_id, key, *args, **kwargs):
        if key == "stream_elements_commands":
            return {"streamer": "gaules", "state": {"channel_id": "se-gaules"}}
        return {}

    mock_cache.side_effect = configured
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(lambda channel_id: [{"command": "mouse", "reply": "G Pro", "enabled": True}]),
    )
    guild = english_guild()

    message = await ask(deps.bot, guild, member_without_a_picture(guild), "ks!commands")

    assert _arrows(message._reply.await_args.kwargs["view"]) == [True, True, True, True]
