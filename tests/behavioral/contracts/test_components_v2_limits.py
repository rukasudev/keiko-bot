"""Guardrails for Discord's Components V2 hard limits.

Discord rejects a LayoutView message that exceeds 40 components or 4000
characters of combined text. These are exactly the errors an offline suite
would otherwise miss (the fake transport accepts anything), so the real
cards are built through real scenarios and checked against the documented
limits here.
"""

import discord
import pytest

from app.core.utils import ml
from tests.behavioral.harness.locators import walk_items
from tests.behavioral.scenarios.test_reminders_birthday_flow import (
    _setup,
    _to_member_card_from_the_panel,
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


def _assert_ids_are_unique(view, label: str) -> None:
    """Discord refuses a whole message when two components share a custom id."""
    ids = [
        item.custom_id
        for item in walk_items(view)
        if getattr(item, "custom_id", None) is not None
    ]
    duplicated = {found for found in ids if ids.count(found) > 1}
    assert not duplicated, f"{label}: {len(duplicated)} custom id(s) used twice"


async def test_a_panel_whose_list_is_empty_has_no_duplicated_custom_id(
    scenario_factory, deps
):
    """Broke as: block links did not open at all (50035, "Component custom id
    cannot be duplicated"). The button beside an empty list adds, like the Add
    below the card, and both encoded the same action with no argument."""
    from tests.behavioral.golden.paths.block_links import ENABLED
    from tests.behavioral.golden.paths.common import seed_document

    empty = {**ENABLED, "custom_links": {"style": "composition", "values": []}}
    seed_document(deps, "block_links", empty)

    scenario = await scenario_factory(locale="pt-br").start_command("block_links")

    _assert_ids_are_unique(scenario.current_message.view, "block_links panel")
    await scenario.click("edit:group:exceptions")
    _assert_ids_are_unique(scenario.current_message.view, "exceptions screen")
    await scenario.finish()


async def test_the_exceptions_screen_is_within_discord_limits(scenario_factory, deps):
    """A server that freed a long list of links must still open the screen:
    Discord refuses a message over forty components, and every link there
    carries a block of its own."""
    from tests.behavioral.golden.paths.block_links import ENABLED
    from tests.behavioral.golden.paths.common import seed_document

    crowded = {
        **ENABLED,
        "custom_links": {
            "style": "composition",
            "values": [
                {"link": {"value": f"site{i}.com", "title": "Link", "style": "code"}}
                for i in range(25)
            ],
        },
    }
    seed_document(deps, "block_links", crowded)
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")

    await scenario.click("edit:group:exceptions")

    view = scenario.current_message.view
    _assert_within_limits(view, "exceptions screen with 25 links")
    _assert_ids_are_unique(view, "exceptions screen with 25 links")
    await scenario.finish()


async def test_birthday_global_card_is_within_discord_limits(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "birthday_config card")
    await scenario.finish()


async def test_birthday_member_card_is_within_discord_limits(scenario_factory):
    setup = await _setup(scenario_factory, "pt-br")
    scenario = await _to_member_card_from_the_panel(scenario_factory, setup, "pt-br")
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


async def test_the_welcome_gallery_opened_from_the_panel_is_within_discord_limits(
    scenario_factory, deps
):
    """The gallery of every design opens from the Edit beside the design on the
    panel; the card itself picks its design on a select."""
    from tests.behavioral.golden.paths.common import seed_document
    from tests.behavioral.golden.paths.welcome_messages import ENABLED

    seed_document(deps, "welcome_messages", ENABLED)
    panel = await scenario_factory(locale="pt-br").start_command("welcome_messages")
    await panel.click("section:welcome_config/design")
    _assert_within_limits(panel.current_message.view, "welcome gallery")
    await panel.finish()


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


CROWDED_LINKS = [
    {"link": {"value": f"site{index}.com", "title": "Link", "style": "code"}}
    for index in range(25)
]


async def test_a_panel_with_a_full_list_drawn_in_the_card_is_within_discord_limits(
    scenario_factory, deps
):
    """P4 (beta.2): the list sits inside the card with its items as bullets,
    Add beside the title and Remove below; twenty-five links still fit."""
    from tests.behavioral.golden.paths.block_links import ENABLED
    from tests.behavioral.golden.paths.common import seed_document

    crowded = {**ENABLED, "custom_links": {"style": "composition", "values": CROWDED_LINKS}}
    seed_document(deps, "block_links", crowded)
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")

    view = scenario.current_message.view
    _assert_within_limits(view, "block_links panel with 25 links")
    _assert_ids_are_unique(view, "block_links panel with 25 links")
    await scenario.click("remove:custom_links")
    select = next(item for item in walk_items(scenario.current_message.view)
                  if isinstance(item, discord.ui.Select))
    assert len(select.options) == 25 and select.max_values == 25
    await scenario.finish()


def test_a_review_with_a_full_list_is_within_discord_limits():
    from app.settings.discord.views import layout_of
    from app.settings.form import events as ev
    from app.settings.form.form import decide
    from app.settings.form.form_state import Answer, Origin, Setup, new_session
    from app.settings.form.form_yaml import DefinitionRegistry

    definition = DefinitionRegistry().get("block_links")
    links = tuple(
        {"link": Answer(f"site{index}.com"), "match_type": Answer("domain")}
        for index in range(25)
    )
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin("123456789", "555", "pt-br"),
        ttl_seconds=60,
        answers={
            "mode": Answer("block_all"),
            "custom_links": Answer(links),
        },
    ).at(definition.steps[-1].key)

    decision = decide(definition, session, ev.ScreenRequested("e1", None))
    view = layout_of(decision.effects[0].screen, lambda action, arg=None: "k:s:1:x", None)
    _assert_within_limits(view, "block_links review with 25 links")


@pytest.mark.parametrize("months", [1, 12], ids=["one-month", "every-month"])
async def test_a_birthday_panel_with_a_full_list_is_within_discord_limits(
    scenario_factory, deps, months
):
    """bd-6: twenty-five birthdays, all in one month or spread over the year,
    keep the panel within the limits with its page and its footer row."""
    from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
    from app.core.moderations import update_moderations_by_guild

    upsert_birthday_config(
        "123456789", "100", False, timezone="America/Sao_Paulo", notification_time="08:00"
    )
    for index in range(25):
        month = index % months + 1
        upsert_birthday_item("123456789", str(1000 + index), f"{month:02d}-{index + 1:02d}")
    update_moderations_by_guild("123456789", "reminders_birthday", True)
    scenario = await scenario_factory(locale="pt-br").start_command("reminders_birthday")

    view = scenario.current_message.view
    _assert_within_limits(view, f"birthday panel, 25 birthdays over {months} months")
    _assert_ids_are_unique(view, "birthday panel")
    await scenario.finish()


async def test_the_block_links_card_with_a_full_list_is_within_discord_limits(
    scenario_factory,
):
    """bl-7: the custom links are drawn with Add and Remove, and twenty-five of
    them still fit, on the exceptions screen that holds them (round 5 E) and on
    the card that tells them after Voltar."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")
    for index in range(25):
        await scenario.click("customize:6")
        await scenario.submit_modal({"Digite o link ou site": f"site{index}.com"})

    assert len(scenario.answers["custom_links"]) == 25
    view = scenario.current_message.view
    _assert_within_limits(view, "block_links exceptions with 25 links")
    _assert_ids_are_unique(view, "block_links exceptions with 25 links")
    await scenario.click("back")
    view = scenario.current_message.view
    _assert_within_limits(view, "block_links card with 25 links")
    _assert_ids_are_unique(view, "block_links card with 25 links")
    await scenario.finish()


LARGEST_CARDS = {
    ("block_links", "link_settings"): {
        "mode": "block_all",
        "answer": "x" * 100,
        "allowed_links": ["youtube.com", "twitch.tv"],
        "allowed_chats": ["1", "2"],
        "allowed_roles": ["3"],
    },
    ("default_roles", "default_roles_config"): {
        "default_roles_bot": ["1", "2"],
        "default_roles": ["3"],
    },
    ("reminders_birthday", "birthday_config"): {
        "channel": "1",
        "timezone": "UTC",
        "notification_time": "10:00",
        "mention_everyone": True,
        "default_message_mode": "custom",
        "default_message_title": "Oi {user}",
        "default_message_content": "Parabéns!",
    },
    ("reminders_birthday", "birthday_member_config"): {
        "user": "777",
        "month": "05",
        "day": "12",
        "use_custom_message": "custom",
        "custom_message_title": "Oi",
        "custom_message_content": "Parabéns!",
        "use_custom_image": "custom",
        "custom_image": "https://cdn.example/image.png",
    },
    ("welcome_messages", "welcome_config"): {
        "welcome_messages_channel": "1",
        "welcome_messages": "Oi {user}!;Bem-vindo!",
        "welcome_design": "custom_blur",
        "welcome_custom_image": "https://cdn.example/image.png",
    },
    ("notifications_twitch", "notification"): {
        "channel": "1",
        "streamer": "shroud",
        "streamer_avatar": "https://cdn.example/avatar.png",
        "notification_messages": ["a" * 100, "b" * 100],
    },
    ("notifications_youtube_video", "notification"): {
        "channel": "1",
        "youtuber": "pewdiepie",
        "youtuber_avatar": "https://cdn.example/avatar.png",
        "notification_messages": ["a" * 100, "b" * 100],
    },
}


def _every_step(steps):
    from app.settings.form.form_yaml import CompositionStep

    for step in steps:
        yield step
        if isinstance(step, CompositionStep):
            yield from _every_step(step.steps)


def test_every_card_of_every_form_is_listed_at_its_largest():
    from app.settings.form.form_yaml import CardStep, DefinitionRegistry

    cards = {
        (definition.key, step.key)
        for definition in DefinitionRegistry().load_all()
        for step in _every_step(definition.steps)
        if isinstance(step, CardStep)
    }
    assert cards == set(LARGEST_CARDS)


@pytest.mark.parametrize("form,card_key", sorted(LARGEST_CARDS))
@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_every_card_with_its_controls_at_its_largest_fits_discord(
    form, card_key, locale
):
    """N4 (beta.2 round 2): every card draws its selects and buttons on itself;
    with its lists full, every optional part shown and a picture on each part
    that takes one, it still fits Discord, within the compiler's estimate."""
    from app.settings.discord.views import layout_of
    from app.settings.form.actions.action import Context
    from app.settings.form.actions.configuration_card import render
    from app.settings.form.form_state import Answer, Origin, Setup, new_session
    from app.settings.form.form_yaml import (
        DefinitionRegistry,
        card_components,
        cards_with_back,
    )

    definition = DefinitionRegistry().get(form)
    card, back = next(
        (step, back)
        for step, back in cards_with_back(definition.steps)
        if step.key == card_key
    )
    links = tuple(
        {"link": Answer(f"site{index}.com"), "match_type": Answer("domain")}
        for index in range(25)
    )
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin("123456789", "555", locale),
        ttl_seconds=60,
        answers={
            card_key: Answer(None, LARGEST_CARDS[(form, card_key)]),
            "custom_links": Answer(links),
        },
    ).at(card_key)
    designs = {design.key: f"https://cdn.example/{design.key}.png"
               for design in definition.designs()}
    context = Context(
        definition=definition,
        steps=definition.steps,
        locale=locale,
        can_go_back=back,
        previews=designs,
        member_pictures={"777": "https://cdn.example/member.png"},
    )

    view = layout_of(
        render(card, session, context), lambda action, arg=None: "k:s:1:x", None
    )
    drawn = len(list(walk_items(view)))
    _assert_within_limits(view, f"{form}.{card_key} {locale}")
    assert drawn <= card_components(card, back), (
        f"{form}.{card_key}: {drawn} drawn, the compiler estimates "
        f"{card_components(card, back)}"
    )
    print(f"{form}.{card_key} {locale}: {drawn} components")


@pytest.mark.parametrize(
    "mode,group", [("block_all", "exceptions"), ("allow_all", "channel_exceptions")]
)
@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_the_block_links_card_and_its_exceptions_screen_fit_in_each_mode(
    mode, group, locale
):
    """Round 5 E: with every popular website on, channels and roles picked and
    twenty-five links of the longest kind, the card and the screen its
    Configurar opens each fit Discord, within the compiler's estimates."""
    from dataclasses import replace

    from app.settings.discord.views import layout_of
    from app.settings.form.actions.action import Context
    from app.settings.form.actions.configuration_card import render
    from app.settings.form.form_state import Answer, Origin, Setup, new_session
    from app.settings.form.form_yaml import (
        DefinitionRegistry,
        card_components,
        group_components,
    )

    definition = DefinitionRegistry().get("block_links")
    card = definition.step("link_settings")
    sections = {section.key: section for section in card.sections}
    links = tuple(
        {"link": Answer(f"site{index:02d}.example.com/{'p' * 70}")}
        for index in range(25)
    )
    state = {
        **LARGEST_CARDS[("block_links", "link_settings")],
        "mode": mode,
        "allowed_links": [
            str(option.value) for option in sections["allowed_links"].options
        ],
        "allowed_chats": [str(index) for index in range(1, 11)],
        "allowed_roles": [str(index) for index in range(11, 21)],
    }
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin("123456789", "555", locale),
        ttl_seconds=60,
        answers={"link_settings": Answer(None, state), "custom_links": Answer(links)},
    ).at("link_settings")
    context = Context(definition=definition, steps=definition.steps, locale=locale)
    screens = {
        "card": (session, card_components(card, False)),
        "exceptions": (
            replace(session, opened_group=group),
            group_components(card, sections[group]),
        ),
    }

    for name, (shown, estimate) in screens.items():
        view = layout_of(
            render(card, shown, context), lambda action, arg=None: "k:s:1:x", None
        )
        drawn = len(list(walk_items(view)))
        _assert_within_limits(view, f"block_links {name} in {mode}")
        assert drawn <= estimate, f"{name}: {drawn} drawn, estimated {estimate}"
        print(f"block_links {name} {mode} {locale}: {drawn} components")


@pytest.mark.parametrize("form", ["notifications_twitch", "notifications_youtube_video"])
async def test_a_full_notification_panel_with_its_item_buttons_is_within_discord_limits(
    scenario_factory, deps, form
):
    """N5 (round 2): every item carries Edit, Preview and Remove, and the list
    title counts its items; a list at its most items still fits."""
    import importlib

    from app.settings.form.form_yaml import registry
    from tests.behavioral.golden.paths.common import seed_document

    paths = importlib.import_module(MANAGER_SEEDS[form])
    most = registry.get(form).composition.items.max
    items = [paths.notification(str(100 + index), f"name{index}") for index in range(most)]
    seed_document(
        deps, form, {**paths.ENABLED, "notifications": {"style": "composition", "values": items}}
    )
    scenario = await scenario_factory(locale="pt-br").start_command(form)

    view = scenario.current_message.view
    _assert_within_limits(view, f"{form} panel with {most} items")
    _assert_ids_are_unique(view, f"{form} panel with {most} items")
    await scenario.finish()
