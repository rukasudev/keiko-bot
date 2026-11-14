"""Regression: discarding an item's own screens closes the whole setup as discarded.

What broke: Cancel on the first screen of an item (the streamer card of
Twitch or YouTube, a child session under the setup) closed only the child.
The setup above it stayed waiting for the child, so the message was gone but
the setup expired later: `setup.abandoned` fired after `setup.discarded` and
the story closed as abandoned. Once Cancel with nothing answered stopped
asking (ux-552), this became one click.

Shared behavior affected: `_discard` and the child ending
(`ResumeParent`, `ChildFinished`, app/settings/form/form.py) and the
adapter's closing of a root session (`Runtime._run`, `Runtime._close`).

Exposed by: notifications_twitch and notifications_youtube_video, Cancel
on the first streamer card.

Must remain guaranteed: the setup and its item both end cancelled, the
setup's story ends discarded, `setup.discarded` fires once and
nothing is abandoned afterwards.
"""
import pytest

from app.core import analytics
from app.settings.discord.callbacks import RUNTIME
from app.settings.form.form_state import Status

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]


@pytest.mark.parametrize(
    "form", ["notifications_twitch", "notifications_youtube_video"]
)
async def test_cancel_on_an_item_closes_its_setup_as_discarded(
    scenario_factory, stories, monkeypatch, form
):
    events = []
    monkeypatch.setattr(analytics, "record", lambda name, **_: events.append(name))
    scenario = await scenario_factory(locale="pt-br").start(form)
    setup = scenario.session.id
    await scenario.confirm()
    item = scenario.session.id
    assert item != setup

    await scenario.cancel()
    await scenario.expire()

    assert RUNTIME.store.get(item).status is Status.CANCELLED
    assert RUNTIME.store.get(setup).status is Status.CANCELLED
    endings = [story[2] for story in stories]
    assert endings[-1] == "discarded"
    assert "abandoned" not in endings
    assert events.count("setup.discarded") == 1
    assert "setup.abandoned" not in events
    await scenario.finish()
