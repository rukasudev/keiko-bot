"""A feature document says who last saved it, and Keiko's own writes never claim it.

Shared behaviour: the commits of the feature document layer
(`GenericCogFeature`, app/settings/features/feature.py, and the birthday module),
`set_feature_enabled` (app/core/servers.py) and the writes of guild.cogs
(app/data/guild/cogs.py), which take `updated_by` from the commit context.

Guaranteed: a setup, an edit, a list change, a pause and an unpause record the
member who made them in `updated_by`; what Keiko learns on its own
(`set_cog_state_async`) and the pause of a server Keiko left leave it as it was.
"""

from datetime import datetime, timezone

import pytest

from app.core.servers import leave_guild
from app.data.guild.cogs import set_cog_state_async, update_cog_if_unchanged_async
from app.settings.features import feature_for
from app.settings.features.feature import CommitContext
from app.settings.form.form_state import Answer
from tests.generators.moderations import recorded_server

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
ROLES = {"default_roles_bot": Answer(["202"]), "default_roles": Answer("201")}


def context(user_id, document=None):
    return CommitContext(GUILD_ID, user_id, "pt-br", document or {}, NOW, "slash", "s1")


def saved_by(deps, cog):
    stored = deps.mongo_client.guild.cogs.find_one({"_id": f"{GUILD_ID}:{cog}"})
    return stored.get("updated_by")


async def test_every_change_a_member_makes_names_them(deps):
    feature = feature_for("default_roles")

    await feature.commit("setup", {"answers": ROLES}, context("555"))
    assert saved_by(deps, "default_roles") == "555"
    await feature.commit(
        "edit", {"answers": {"default_roles": Answer("203")}}, context("556")
    )
    assert saved_by(deps, "default_roles") == "556"
    await feature.commit("pause", {}, context("557"))
    assert saved_by(deps, "default_roles") == "557"
    await feature.commit("unpause", {}, context("558"))
    assert saved_by(deps, "default_roles") == "558"


async def test_a_list_change_names_who_made_it(deps):
    await feature_for("default_roles").commit("setup", {"answers": ROLES}, context("555"))

    written = await update_cog_if_unchanged_async(
        GUILD_ID, "default_roles", "default_roles", {"style": "role", "values": "201"},
        {"style": "role", "values": "204"}, "559",
    )

    assert written and saved_by(deps, "default_roles") == "559"


async def test_keikos_own_writes_leave_the_last_person_in_place(deps):
    recorded_server(deps.mongo_client, GUILD_ID)
    await feature_for("default_roles").commit("setup", {"answers": ROLES}, context("555"))

    await set_cog_state_async(GUILD_ID, "default_roles", {"schema_version": 1})
    assert saved_by(deps, "default_roles") == "555"
    leave_guild(GUILD_ID, "99")

    stored = deps.mongo_client.guild.cogs.find_one({"_id": f"{GUILD_ID}:default_roles"})
    assert stored["enabled"] is False, "leaving paused the feature"
    assert stored["updated_by"] == "555", "the pause on leaving is Keiko's, not a person's"


async def test_a_birthday_setup_names_who_made_it(deps):
    answers = {
        "channel": Answer("100"),
        "timezone": Answer("America/Sao_Paulo"),
        "notification_time": Answer("08:00"),
        "mention_everyone": Answer(False),
        "default_message_mode": Answer("default"),
    }
    recorded_server(deps.mongo_client, GUILD_ID)

    await feature_for("reminders_birthday").commit(
        "setup", {"answers": answers}, context("555")
    )

    assert saved_by(deps, "reminders_birthday") == "555"
