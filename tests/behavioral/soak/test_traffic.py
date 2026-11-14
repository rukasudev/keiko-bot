"""The soak's traffic drives what it claims, checked in the default run.

Broke as: after database PR 1 (#61) the soak seeded its servers' features in the
old per-feature collections, which nothing reads any more, so the blocked link, the
StreamElements command, the welcome and the default roles never answered; and its
golden paths shared the server settings memo, so `welcome_messages/setup_happy`
in en-us, right after its pt-br run, showed the pt-br modal and failed 8 steps.
`make soak` failed while the default suite stayed green.

Guaranteed: two iterations in a row, the pt-br and the en-us run of a golden path
on the same server, drive every step and every listener.
"""
from tests.behavioral.soak.test_memory_soak import DRIVEN
from tests.behavioral.soak.traffic import Traffic


async def test_consecutive_iterations_drive_every_step_and_listener(
    scenario_factory, deps, streamer_commands
):
    traffic = Traffic(scenario_factory, deps)
    english = traffic.paths.index(
        next(
            (path, locale)
            for path, locale in traffic.paths
            if path.id == "welcome_messages/setup_happy" and locale == "en-us"
        )
    )

    for number in (english - 1, english):
        await traffic.iteration(number)

    assert traffic.failures == []
    assert {kind for kind, count in traffic.answered.items() if count} >= DRIVEN
