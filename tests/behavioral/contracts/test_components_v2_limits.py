"""Guardrails for Discord's Components V2 hard limits.

Discord rejects a LayoutView message that exceeds 40 components or 4000
characters of combined text. These are exactly the errors an offline suite
would otherwise miss (the fake transport accepts anything), so the real
cards are built through real scenarios and checked against the documented
limits here.
"""

import discord
import pytest

from tests.behavioral.harness.locators import walk_items
from tests.behavioral.scenarios.test_reminders_birthday_flow import (
    YES_LABEL,
    _complete_global_card,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

MAX_COMPONENTS = 40
MAX_TOTAL_TEXT = 4000


def _assert_within_limits(view, label: str) -> None:
    items = list(walk_items(view))
    assert len(items) <= MAX_COMPONENTS, (
        f"{label}: {len(items)} components exceed Discord's limit of {MAX_COMPONENTS}"
    )
    total_text = sum(
        len(item.content or "")
        for item in items
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert total_text <= MAX_TOTAL_TEXT, (
        f"{label}: {total_text} chars of TextDisplay exceed {MAX_TOTAL_TEXT}"
    )


async def test_birthday_global_card_is_within_discord_limits(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "birthday_config card")
    await scenario.finish()


async def test_birthday_member_card_is_within_discord_limits(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)
    await scenario.click(YES_LABEL["pt-br"])
    await scenario.select_option("Tester")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "birthday_member_config card")
    await scenario.finish()


MANAGER_SEEDS = {
    "block_links": "tests.behavioral.golden.paths.block_links",
    "default_roles": "tests.behavioral.golden.paths.default_roles",
    "welcome_messages": "tests.behavioral.golden.paths.welcome_messages",
    "notifications_twitch": "tests.behavioral.golden.paths.notifications_twitch",
    "notifications_youtube_video": "tests.behavioral.golden.paths.notifications_youtube_video",
    "stream_elements_commands": "tests.behavioral.golden.paths.stream_elements_commands",
}


@pytest.mark.parametrize("form", sorted(MANAGER_SEEDS))
@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_every_manager_panel_is_within_discord_limits(
    scenario_factory, deps, form, locale
):
    import importlib

    from tests.behavioral.golden.paths.common import seed_document

    paths = importlib.import_module(MANAGER_SEEDS[form])
    seed_document(deps, form, getattr(paths, "TWO_ENTRIES", paths.ENABLED))
    scenario = await scenario_factory(locale=locale).start_command(form)
    _assert_within_limits(scenario.current_message.view, f"{form} panel {locale}")
    await scenario.finish()


async def test_the_birthday_manager_panel_is_within_discord_limits(
    scenario_factory, deps
):
    from tests.behavioral.golden.paths.reminders_birthday import _open_manager

    scenario = await _open_manager(
        scenario_factory, deps, "pt-br", members=tuple(str(555 + i) for i in range(8))
    )
    _assert_within_limits(scenario.current_message.view, "birthday panel")
    await scenario.finish()


@pytest.mark.parametrize("form", sorted(MANAGER_SEEDS))
def test_every_setup_review_is_within_discord_limits(form):
    import importlib

    from app.settings.discord.views import layout_of
    from app.settings.features import feature_for
    from app.settings.form import events as ev
    from app.settings.form.form import decide
    from app.settings.form.form_state import Origin, Setup, new_session
    from app.settings.form.form_yaml import DefinitionRegistry

    paths = importlib.import_module(MANAGER_SEEDS[form])
    definition = DefinitionRegistry().get(form)
    document = getattr(paths, "TWO_ENTRIES", paths.ENABLED)
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin("123456789", "555", "pt-br"),
        ttl_seconds=60,
        answers=feature_for(form).from_document(document),
    ).at(definition.steps[-1].key)

    decision = decide(definition, session, ev.ScreenRequested("e1", None))

    view = layout_of(
        decision.effects[0].screen, lambda action, arg=None: "k:s:1:x", None
    )
    _assert_within_limits(view, f"{form} review")


async def test_the_welcome_card_and_its_gallery_are_within_discord_limits(
    scenario_factory,
):
    scenario = await scenario_factory(locale="pt-br").start("welcome_messages")
    await scenario.confirm()
    await scenario.click("customize:2")
    _assert_within_limits(scenario.current_message.view, "welcome gallery")
    await scenario.click("design:custom_only")
    _assert_within_limits(scenario.current_message.view, "welcome card, custom design")
    await scenario.finish()


async def test_a_twitch_notification_card_is_within_discord_limits(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "twitch item card")
    await scenario.finish()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_every_modal_input_label_fits_discord(locale):
    """A label over the limit is truncated by the renderer, silently."""
    from app.constants import DiscordLimits
    from app.settings.form.form_yaml import (
        CardStep,
        CompositionStep,
        DefinitionRegistry,
        ModalInputSection,
        TextStep,
    )

    def every_step(steps):
        for step in steps:
            yield step
            if isinstance(step, CompositionStep):
                yield from every_step(step.steps)

    def labels(definition):
        for step in every_step(definition.steps):
            if isinstance(step, TextStep):
                yield from (field.label.get(locale) for field in step.fields)
            if isinstance(step, CardStep):
                for section in step.sections:
                    if isinstance(section, ModalInputSection):
                        yield from (
                            field.label.get(locale) for field in section.modal.fields
                        )

    for definition in DefinitionRegistry().load_all():
        for label in labels(definition):
            assert len(label) <= DiscordLimits.MODAL_INPUT_LABEL, (
                f"{definition.key} [{locale}]: {label!r} is {len(label)} characters"
            )
