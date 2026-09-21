"""The /setup History button: the server's recent changes across features.

Each feature kept its own history behind its own panel, so an admin who wanted
to know what changed in the server had to open six panels. Guaranteed: the
history merges every feature's audit records, newest first, and every line
names the feature it happened in.
"""
from types import SimpleNamespace

import pytest

from app.services.utils import ml
from tests.behavioral.golden.paths.common import GUILD_ID, seed_history

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("setup_dashboard")]


async def test_the_history_lists_every_feature_newest_first_and_names_each(deps):
    from app.services.setup import guild_history, history_fields

    seed_history(deps, "welcome_messages", "enabled")
    seed_history(deps, "block_links", "enabled", "paused")

    records = await guild_history(GUILD_ID)

    assert (records[0]["cog_key"], records[0]["event"]) == ("block_links", "paused")
    assert {record["cog_key"] for record in records} == {"welcome_messages", "block_links"}

    interaction = SimpleNamespace(
        locale="pt-br", guild=None, client=SimpleNamespace(user=SimpleNamespace(id=999))
    )
    rendered = "\n".join(history_fields(records, interaction).values())
    assert ml("buttons.setup.block-links.label", "pt-br") in rendered
    assert ml("buttons.setup.welcome-messages.label", "pt-br") in rendered
