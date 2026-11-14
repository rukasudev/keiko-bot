"""Disable drops the feature's document and then leaves that collection alone.

Broke as: after `find_one_and_delete`, `commit_disable` recorded the feature off
through `set_feature_enabled`, which ran `update_one(enabled=False)` on the
document it had just deleted and dropped its cache a second time. Another
admin's setup saved between the two found its new feature paused, and every
Disable paid a second Redis round trip. The birthday module's Disable did the
same over its own config.

Shared behaviour: the Disable commit of the feature document layer
(`GenericCogFeature.commit_disable`, app/settings/features/feature.py), the
birthday module's Disable (app/settings/features/reminders_birthday.py), over
guild.cogs (app/data/guild/cogs.py). Exposed by the v1 review of the Disable path.

Guaranteed: Disable never writes the collection it deleted from, so a document
saved right after it stays as it was saved; the cache entry is dropped once.
"""
from datetime import datetime, timezone

import pytest

from app.data import data
from app.core import cache
from app.settings.features import feature_for
from app.settings.features.feature import CommitContext
from tests.generators.moderations import seed_cog, stored_cog

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "123456789"
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

BLOCK_LINKS = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": "100"},
    "answer": "Nada de links aqui! :p",
}

BIRTHDAYS = {"guild_id": GUILD_ID, "enabled": True, "channel": "100"}


def context(document):
    return CommitContext(GUILD_ID, "555", "pt-br", document, NOW, "slash", "s1")


def another_admin_saves_right_after(collection, operation, monkeypatch, key, document):
    original = getattr(collection, operation)

    def then_saved(*args, **kwargs):
        result = original(*args, **kwargs)
        collection.insert_one(stored_cog(key, document))
        return result

    monkeypatch.setattr(collection, operation, then_saved)


async def test_a_setup_saved_right_after_a_disable_stays_on(deps, monkeypatch):
    collection = deps.mongo_client.guild.cogs
    collection.insert_one(stored_cog("block_links", BLOCK_LINKS))
    another_admin_saves_right_after(
        collection, "find_one_and_delete", monkeypatch, "block_links", BLOCK_LINKS
    )

    await feature_for("block_links").commit("disable", {}, context(BLOCK_LINKS))

    assert (await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "block_links"))["enabled"] is True, (
        "Disable paused the block links another admin set up right after it"
    )


async def test_a_birthday_setup_saved_right_after_a_disable_stays_on(deps, monkeypatch):
    collection = deps.mongo_client.guild.cogs
    collection.insert_one(stored_cog("reminders_birthday", BIRTHDAYS))
    another_admin_saves_right_after(
        collection, "find_one_and_delete", monkeypatch, "reminders_birthday", BIRTHDAYS
    )

    await feature_for("reminders_birthday").commit("disable", {}, context(BIRTHDAYS))

    assert (await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "reminders_birthday"))["enabled"] is True, (
        "Disable paused the birthdays another admin set up right after it"
    )


async def test_disable_drops_the_cached_settings_once(deps, monkeypatch):
    seed_cog(deps.mongo_client, "block_links", dict(BLOCK_LINKS))
    cache.get_cog_data_or_populate(GUILD_ID, "block_links")
    dropped = []
    original = deps.redis_client.delete

    def counted(*keys):
        dropped.extend(key for key in keys if key.endswith(":block_links"))
        return original(*keys)

    monkeypatch.setattr(deps.redis_client, "delete", counted)

    await feature_for("block_links").commit("disable", {}, context(BLOCK_LINKS))

    assert len(dropped) == 1, f"Disable dropped the block links cache {len(dropped)} times"
    assert cache.get_cog_data_or_populate(GUILD_ID, "block_links") is None
