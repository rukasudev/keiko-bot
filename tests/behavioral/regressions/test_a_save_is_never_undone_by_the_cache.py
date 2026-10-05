"""A saved change is what every reader sees next, however busy the server is.

Broke as: `write_document` and `update_document` dropped the Redis entry of a
feature and only then wrote Mongo. A message or a member join landing between
the two read Mongo, found the old document and cached it again, for thirty
days. An edit came back on its own, and a disabled block links could keep
blocking for a month, because Disable did the same: it even wrote the document
back on (`enabled: True`) right before deleting it, so a paused feature woke up
for whoever read it in that window.

Shared behaviour: the document layer every feature commits through
(`GenericCogFeature`, app/settings/features/feature.py) and the config cache
every runtime reader goes through (`get_cog_data_or_populate`,
app/services/cache.py). Exposed by block links (`on_message`) and default roles
(`on_member_join`), which read on every event.

Guaranteed: a read that races a save can return the old document while the save
is running, never after it; Disable is seen by the next reader; a paused feature
being disabled is never on in between; and a cache entry lives minutes, not a
month.
"""
from datetime import datetime, timezone

import pytest

from app.services import cache
from app.settings.features import feature_for
from app.settings.features.feature import CommitContext
from app.settings.form.form_state import Answer

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "123456789"
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

AUTO_ROLES = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "default_roles": {"style": "role", "values": "201"},
    "default_roles_bot": {"style": "role", "values": ["202"]},
}

BLOCK_LINKS = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": "100"},
    "answer": "Nada de links aqui! :p",
}


def context(document):
    return CommitContext(GUILD_ID, "555", "pt-br", document, NOW, "slash", "s1")


def a_reader_arrives_during(collection, operation, monkeypatch, key):
    """Every call to `operation` lets one runtime read in before it applies."""
    original = getattr(collection, operation)
    seen = []

    def racing(*args, **kwargs):
        seen.append(cache.get_cog_data_or_populate(GUILD_ID, key))
        return original(*args, **kwargs)

    monkeypatch.setattr(collection, operation, racing)
    return seen


async def test_a_member_joining_during_an_edit_never_brings_the_old_roles_back(
    deps, monkeypatch
):
    collection = deps.mongo_client.guild["default_roles"]
    collection.insert_one(dict(AUTO_ROLES))
    cache.get_cog_data_or_populate(GUILD_ID, "default_roles")
    a_reader_arrives_during(collection, "update_one", monkeypatch, "default_roles")

    await feature_for("default_roles").commit(
        "edit", {"answers": {"default_roles": Answer("203")}}, context(AUTO_ROLES)
    )

    after = cache.get_cog_data_or_populate(GUILD_ID, "default_roles")
    assert after["default_roles"]["values"] == "203", (
        "the member who joined during the save cached the old roles, and every "
        "member after them got the old roles too"
    )


async def test_disable_is_seen_by_the_next_message(deps, monkeypatch):
    collection = deps.mongo_client.guild["block_links"]
    collection.insert_one(dict(BLOCK_LINKS))
    cache.get_cog_data_or_populate(GUILD_ID, "block_links")
    a_reader_arrives_during(collection, "find_one_and_delete", monkeypatch, "block_links")

    await feature_for("block_links").commit("disable", {}, context(BLOCK_LINKS))

    assert cache.get_cog_data_or_populate(GUILD_ID, "block_links") is None, (
        "block links was disabled and the next message still found it on"
    )


async def test_a_paused_feature_is_never_on_while_it_is_being_disabled(
    deps, monkeypatch
):
    paused = {**BLOCK_LINKS, "enabled": False}
    collection = deps.mongo_client.guild["block_links"]
    collection.insert_one(dict(paused))
    seen = a_reader_arrives_during(collection, "find_one_and_delete", monkeypatch, "block_links")

    await feature_for("block_links").commit("disable", {}, context(paused))

    assert seen == [{}], (
        "a message read the paused block links as on while it was being disabled"
    )


async def test_a_cached_setting_lives_minutes_not_a_month(deps):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))

    cache.get_cog_data_or_populate(GUILD_ID, "block_links")

    ttl = deps.redis_client.ttl(f"guild:{GUILD_ID}:cog.block_links")
    assert 0 < ttl <= 15 * 60, f"the entry lives {ttl} seconds"
