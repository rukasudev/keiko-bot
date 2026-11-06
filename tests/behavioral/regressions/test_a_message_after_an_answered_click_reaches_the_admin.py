"""A message sent after the click was already answered reaches the admin.

Broke as: an error or a notice sent once the interaction was already answered
(a typed name looked up on Twitch or YouTube defers the click first, an edit
defers before it saves) went out through the followup webhook with
`delete_after`, which discord.py's `Webhook.send` does not take. It raised
TypeError, the admin never read why the name was refused, and the click was
logged as a failure. The offline fake accepted the argument, so every test and
golden stayed green.

Shared behaviour: `Executor._send` (`app/settings/discord/transitions.py`), the
one way every effect sends a new ephemeral message. Exposed by:
notifications_twitch, a streamer name Twitch does not know (beta.2 drew that
refusal inside the card for a while, so the out-of-date notice of an item edit
that saves, after another panel removed the item, pinned the contract too; since
an edit from the panel no longer defers before it saves, that notice is the
click's first answer and only its self-deletion is pinned here).

Guaranteed: a self-deleting message sent after the first answer is sent
without `delete_after` and deleted after the same delay; the fake followup
refuses `delete_after` the way discord.py does.
"""

import pytest

from app.constants import ViewConstants
from app.services.utils import ml
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.regressions.test_a_save_is_never_undone_by_the_cache import (
    _remove,
    _two_twitch_panels,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]


async def test_a_notice_sent_after_the_click_was_answered_reaches_the_admin(
    scenario_factory, deps,
):
    deps.bot.config.is_dev = lambda: False
    first, second = await _two_twitch_panels(scenario_factory, deps)
    await _remove(first, "notifications$1")

    await second.click("section:notifications$1")
    await twitch._channel(second, "announcements")
    await second.click("done")

    stale = ml("commands.form-notices.stale", locale="en-us")
    notices = [event for event in second.outputs if event.get("content") == stale]
    assert notices, "the admin never read why the edit was not saved"
    assert notices[-1]["delete_after"] == ViewConstants.ACTION_NOTICE_SECONDS


async def test_a_streamer_name_refused_after_its_lookup_reaches_the_admin(
    scenario_factory, deps,
):
    scenario = await twitch._card(scenario_factory, deps, "en-us")

    await twitch._submit_streamer(scenario, "naoexiste")

    title = ml("errors.streamer-not-found.title", locale="en-us")
    refusals = [
        event for event in scenario.outputs
        if title in ((event.get("embed") or {}).get("title") or "")
    ]
    assert refusals, "the admin never read why the name was refused"
    assert refusals[-1]["kind"] == "followup_send"
    assert refusals[-1]["delete_after"] == 10
