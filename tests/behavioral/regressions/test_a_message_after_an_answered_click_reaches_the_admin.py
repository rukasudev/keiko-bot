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
notifications_twitch, a streamer name Twitch does not know.

Guaranteed: a self-deleting message sent after the first answer is sent
without `delete_after` and deleted after the same delay; the fake followup
refuses `delete_after` the way discord.py does.
"""

import pytest

from app.services.utils import ml
from tests.behavioral.golden.paths import notifications_twitch as twitch

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]


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
    assert refusals[-1]["delete_after"] == 10
