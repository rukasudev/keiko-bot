"""A change still reaches the History with the product analytics turned off.

Broke as: `ANALYTICS_ENABLED=false` made `record` return before it wrote a change, and
`events.changes` is the only history a server has, so a feature paused, edited or set up while the
analytics were off never showed in /setup's History or its panel's.

Shared behaviour: `app.core.analytics.record`, the one writer of every change.
Consumer that exposed it: every feature's commit (a block links pause here) and the History.
Guaranteed: the kill switch silences the queue (counts, activity, interactions) and
the journey, never a `collection: changes` event.
"""

from types import SimpleNamespace

import pytest

from app.core import analytics
from tests.behavioral.golden.paths.block_links import ENABLED
from tests.behavioral.golden.paths.common import seed_document

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "123456789"


async def test_a_pause_with_analytics_off_still_lists_in_the_history(
    scenario_factory, deps, monkeypatch
):
    from app.services.setup import guild_history, history_fields

    monkeypatch.setattr(analytics, "_ENABLED", False)

    seed_document(deps, "block_links", ENABLED)
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")
    await scenario.click("pause")
    await scenario.submit_confirmation()
    await analytics.written()
    await scenario.finish()

    records = await guild_history(GUILD_ID)
    interaction = SimpleNamespace(
        locale="pt-br", guild=None, client=SimpleNamespace(user=SimpleNamespace(id=999))
    )
    rendered = "\n".join(history_fields(records, interaction, "pt-br").values())

    assert [(record["cog"], record["type"]) for record in records] == [
        ("block_links", "feature.paused")
    ]
    assert "**🚫 Bloquear Links**" in rendered
    assert analytics.drain() == [], "the queue still took events with analytics off"
