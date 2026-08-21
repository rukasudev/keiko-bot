"""Regression: an edit must be auditable in time.

What broke: `Manager.update_command` stamped the audit record with
`interaction.message.edited_at` alone. A manager panel that had never been
edited reports `edited_at = None`, so the `edited` event was written with
`datetime: None`. `find_cog_events_by_guild_id` sorts on that field, so the
History screen showed the edit out of order — or lost it among the null keys.

Shared behavior affected: `insert_cog_event` (app/services/cogs.py), consumed
by the form engine, the manager lifecycle and the History button of every
configured feature. The five sibling call sites already used the
`edited_at or created_at` fallback; this one did not.

What must remain guaranteed: every audit event carries a real timestamp,
whatever state the message it came from was in.
"""
import pytest

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

LEGACY_COG = {
    "guild_id": "123456789",
    "enabled": True,
    "mode": "block_all",
    "answer": "Resposta antiga",
}


async def test_editing_a_never_edited_panel_still_records_a_timestamp(
    scenario_factory, deps
):
    from app.services.block_links import normalize_block_links_config

    deps.mongo_client.guild["block_links"].insert_one(dict(LEGACY_COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(LEGACY_COG)
    )

    await scenario.click("section:link_settings")
    await scenario.click("customize:2")
    await scenario.submit_modal({"resposta": "Resposta nova"})
    await scenario.click("done")

    events = list(deps.mongo_client.events["block_links"].find({}))
    edited = [event for event in events if event["event"] == "edited"]

    assert edited, "the edit must produce an audit record"
    for event in edited:
        assert event["datetime"] is not None, (
            "an audit record without a timestamp cannot be ordered in History"
        )

    await scenario.finish()
