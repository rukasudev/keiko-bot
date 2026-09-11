"""A failed send must not take the person's flow with it.

Reported from production: a guild that had just added Keiko opened
`/moderations welcome messages` and got three errors in two minutes.

    08:35:19  ChannelSelectView error: 400 Invalid Form Body
              In components.0: Value of field "type" must be one of (1, 9, 10, 12, 13, 14, 17)
    08:36:58  FileUploadModal error: 404 Unknown Message
    08:37:03  FileUploadModal error: 400 Invalid Form Body
              In type: Value must be one of {4, 5, 6, 7, 10, 12}

Only the first one is a bug of its own. `_send_layout_view` deleted the message
the person was looking at and *then* sent its replacement, so when the send
failed the flow was already gone: every later click landed on a message that no
longer existed (10008), and the step after that tried to open a modal from a
modal submission, which Discord refuses (response type 9 is not in that set).

Two things must stay guaranteed:

- a send that fails leaves the previous message alone, so the person can carry
  on or retry;
- the view that gets sent is the view the step built, not whatever `self.view`
  happens to hold by the time the send runs. It is reassigned for fifteen
  different objects across the form, and the send used to read it after two
  awaits.
"""
from types import SimpleNamespace

import discord
import pytest

from app.views.form import Form

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]


class FakeFollowup:
    """Tracks what the person can actually see."""

    def __init__(self, fail_send: bool = False) -> None:
        self.visible = {"original"}
        self.fail_send = fail_send
        self.sent_view = None

    async def delete_message(self, message_id):
        self.visible.discard("original")

    async def send(self, view=None, **kwargs):
        if self.fail_send:
            raise discord.HTTPException(
                SimpleNamespace(status=400, reason="Bad Request"),
                {"code": 50035, "message": "Invalid Form Body"},
            )
        self.sent_view = view
        self.visible.add("replacement")


def make_form(followup):
    form = Form.__new__(Form)
    form.state = SimpleNamespace()
    form.cogs = None
    interaction = SimpleNamespace(
        followup=followup, message=SimpleNamespace(id=1234),
    )
    return form, interaction


class OneContainerView(discord.ui.LayoutView):
    def __init__(self) -> None:
        super().__init__(timeout=60)
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay("hello"))
        self.add_item(container)


async def test_a_failed_send_leaves_the_person_where_they_were():
    followup = FakeFollowup(fail_send=True)
    form, interaction = make_form(followup)
    form.view = OneContainerView()

    with pytest.raises(discord.HTTPException):
        await form._send_layout_view(interaction, view=form.view)

    assert "original" in followup.visible, (
        "the message was deleted before the replacement was sent, so a failed "
        "send left the person with no flow at all"
    )


async def test_a_successful_send_still_replaces_the_message():
    followup = FakeFollowup()
    form, interaction = make_form(followup)
    form.view = OneContainerView()

    await form._send_layout_view(interaction, view=form.view)

    assert "replacement" in followup.visible
    assert "original" not in followup.visible, "the old message must not linger"


async def test_the_step_sends_the_view_it_built():
    """`self.view` is reassigned for fifteen kinds of object across the form."""
    followup = FakeFollowup()
    form, interaction = make_form(followup)
    built = OneContainerView()
    form.view = OneContainerView()  # what a concurrent step might leave behind

    await form._send_layout_view(interaction, view=built)

    assert followup.sent_view is built


def test_the_view_cannot_be_left_to_chance():
    """No default: a future caller must not be able to reopen the gap."""
    import inspect

    parameter = inspect.signature(Form._send_layout_view).parameters["view"]

    assert parameter.default is inspect.Parameter.empty
