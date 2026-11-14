"""A server recorded again with another language is answered in that language at once.

Broke as: database PR 1 (#61) kept a server's settings in a process memo
(`server_settings` and `_server_setting`, app/core/servers.py, through
`recalled`/`remembered` in app/core/cache.py) that every product writer forgets
(`join_guild`, `leave_guild`, `save_server_setting`), but the test seed
`recorded_server` (tests/generators/moderations.py) wrote guild.settings without
forgetting it. The memory soak runs every golden path in one process, so
`welcome_messages/setup_happy.en-us`, run right after its pt-br twin on the same
server, read the memo's pt-br and its modal showed the Portuguese fields.

Shared behaviour: the server settings memo and the seed every scenario starts
from (`FormScenario.__init__`, tests/behavioral/harness/driver.py). Consumers:
every golden path, every behavioral scenario and the soak (`make soak`).

Guaranteed: a server recorded in en-us after a pt-br run in the same process
shows an en-us admin the English modal, as it would after a real save.
"""
from tests.behavioral.golden.paths import welcome_messages


async def test_an_en_us_admin_sees_english_fields_after_a_pt_br_run(
    scenario_factory, deps
):
    await welcome_messages.setup_happy(scenario_factory, deps, "pt-br")
    deps.mongo_client.guild.cogs._data.clear()
    deps.redis_client.flushdb()

    scenario = await welcome_messages._to_card(scenario_factory, "en-us")
    await scenario.click("customize:1")

    assert welcome_messages.TITLE_FIELD["en-us"] in scenario.pending_modal_fields()
