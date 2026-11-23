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
from tests.behavioral.flows import birthdays
from tests.helpers.discord_limits import (
    assert_ids_are_unique,
    assert_within_limits,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

async def test_a_panel_whose_list_is_empty_has_no_duplicated_custom_id(
    scenario_factory, deps
):
    """Broke as: block links did not open at all (50035, "Component custom id
    cannot be duplicated"). The button beside an empty list adds, like the Add
    below the card, and both encoded the same action with no argument."""
    from tests.behavioral.flows.block_links import ENABLED
    from tests.behavioral.flows.common import seed_document

    empty = {**ENABLED, "custom_links": {"style": "composition", "values": []}}
    seed_document(deps, "block_links", empty)

    scenario = await scenario_factory(locale="pt-br").start_command("block_links")

    assert_ids_are_unique(scenario.current_message.view, "block_links panel")
    await scenario.click("edit:group:exceptions")
    assert_ids_are_unique(scenario.current_message.view, "exceptions screen")
    await scenario.finish()


async def test_the_exceptions_screen_is_within_discord_limits(scenario_factory, deps):
    """A server that freed a long list of links must still open the screen:
    Discord refuses a message over forty components, and every link there
    carries a block of its own."""
    from tests.behavioral.flows.block_links import ENABLED
    from tests.behavioral.flows.common import seed_document

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
    assert_within_limits(view, "exceptions screen with 25 links")
    assert_ids_are_unique(view, "exceptions screen with 25 links")
    await scenario.finish()


async def test_birthday_global_card_is_within_discord_limits(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    assert_within_limits(scenario.current_message.view, "birthday_config card")
    await scenario.finish()


async def test_birthday_member_card_is_within_discord_limits(scenario_factory):
    setup = await birthdays.setup(scenario_factory, "pt-br")
    scenario = await birthdays.to_member_card_from_the_panel(scenario_factory, setup, "pt-br")
    assert_within_limits(scenario.current_message.view, "birthday_member_config card")
    await scenario.finish()


MANAGER_SEEDS = (
    "block_links",
    "default_roles",
    "reaction_roles",
    "welcome_messages",
    "notifications_twitch",
    "notifications_kick",
    "notifications_youtube_video",
    "notifications_youtube_live",
    "stream_elements_commands",
)


@pytest.mark.parametrize("form", sorted(MANAGER_SEEDS))
@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_every_manager_panel_is_within_discord_limits(
    scenario_factory, deps, form, locale
):
    from tests.behavioral.flows import module_of
    from tests.behavioral.flows.common import seed_document

    paths = module_of(form)
    seed_document(deps, form, getattr(paths, "TWO_ENTRIES", paths.ENABLED))
    scenario = await scenario_factory(locale=locale).start_command(form)
    assert_within_limits(scenario.current_message.view, f"{form} panel {locale}")
    await scenario.finish()


async def test_the_birthday_manager_panel_is_within_discord_limits(
    scenario_factory, deps
):
    from tests.behavioral.flows.birthdays import open_manager

    scenario = await open_manager(
        scenario_factory, deps, "pt-br", members=tuple(str(555 + i) for i in range(8))
    )
    assert_within_limits(scenario.current_message.view, "birthday panel")
    await scenario.finish()


@pytest.mark.parametrize("form", sorted(MANAGER_SEEDS))
def test_every_setup_review_is_within_discord_limits(form):
    from app.forms.discord.views import layout_of
    from app.forms.features import feature_for
    from app.forms.form import events as ev
    from app.forms.form.form import decide
    from app.forms.form.form_state import Origin, Setup, new_session
    from app.forms.form.form_yaml import DefinitionRegistry
    from tests.behavioral.flows import module_of

    paths = module_of(form)
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
    assert_within_limits(view, f"{form} review")


async def test_the_welcome_gallery_opened_from_the_panel_is_within_discord_limits(
    scenario_factory, deps
):
    """The gallery of every design opens from the Edit beside the design on the
    panel; the card itself picks its design on a select."""
    from tests.behavioral.flows.common import seed_document
    from tests.behavioral.flows.welcome import ENABLED

    seed_document(deps, "welcome_messages", ENABLED)
    panel = await scenario_factory(locale="pt-br").start_command("welcome_messages")
    await panel.click("section:welcome_config/design")
    assert_within_limits(panel.current_message.view, "welcome gallery")
    await panel.finish()


async def test_a_twitch_notification_card_is_within_discord_limits(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    assert_within_limits(scenario.current_message.view, "twitch item card")
    await scenario.finish()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_every_modal_input_label_fits_discord(locale):
    """A label over the limit is truncated by the renderer, silently."""
    from app.constants import DiscordLimits
    from app.forms.form.form_yaml import (
        CardStep,
        ListStep,
        DefinitionRegistry,
        ModalInputSection,
        TextStep,
    )

    def every_step(steps):
        for step in steps:
            yield step
            if isinstance(step, ListStep):
                yield from every_step(step.steps)

    def labels(definition):
        for step in every_step(definition.steps):
            if isinstance(step, TextStep) and step.label is not None:
                yield step.label.get(locale)
            if isinstance(step, CardStep):
                for section in step.sections:
                    if isinstance(section, ModalInputSection):
                        yield from (
                            field.label.get(locale) for field in section.modal.inputs
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
    from tests.behavioral.flows.block_links import ENABLED
    from tests.behavioral.flows.common import seed_document

    crowded = {**ENABLED, "custom_links": {"style": "composition", "values": CROWDED_LINKS}}
    seed_document(deps, "block_links", crowded)
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")

    view = scenario.current_message.view
    assert_within_limits(view, "block_links panel with 25 links")
    assert_ids_are_unique(view, "block_links panel with 25 links")
    await scenario.click("remove:custom_links")
    select = next(item for item in walk_items(scenario.current_message.view)
                  if isinstance(item, discord.ui.Select))
    assert len(select.options) == 25 and select.max_values == 25
    await scenario.finish()


def test_a_review_with_a_full_list_is_within_discord_limits():
    from app.forms.discord.views import layout_of
    from app.forms.form import events as ev
    from app.forms.form.form import decide
    from app.forms.form.form_state import Answer, Origin, Setup, new_session
    from app.forms.form.form_yaml import DefinitionRegistry

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
    assert_within_limits(view, "block_links review with 25 links")


FULL_LADDER = (1, 3, 7, 14, 30)


def test_an_auto_roles_review_with_a_full_ladder_is_within_discord_limits():
    """Five rungs, the most a ladder takes, beside the join roles of both kinds."""
    from app.forms.discord.views import layout_of
    from app.forms.form import events as ev
    from app.forms.form.form import decide
    from app.forms.form.form_state import Answer, Origin, Setup, new_session
    from app.forms.form.form_yaml import DefinitionRegistry

    definition = DefinitionRegistry().get("default_roles")
    rungs = tuple(
        {"time": Answer(f"P{days}D"), "role": Answer(str(200 + index))}
        for index, days in enumerate(FULL_LADDER)
    )
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin("123456789", "555", "pt-br"),
        ttl_seconds=60,
        answers={
            "default_roles_config": Answer(
                None, {"default_roles": ["202"], "default_roles_bot": ["201"]}
            ),
            "role_ladder": Answer(rungs),
        },
    ).at(definition.steps[-1].key)

    decision = decide(definition, session, ev.ScreenRequested("e1", None))
    view = layout_of(decision.effects[0].screen, lambda action, arg=None: "k:s:1:x", None)
    assert_within_limits(view, "default_roles review with 5 rungs")


async def test_an_auto_roles_panel_with_a_full_ladder_is_within_discord_limits(
    scenario_factory, deps
):
    """The panel lists five rungs, with its Add off and its Sync, inside the limits."""
    from tests.behavioral.flows import roles
    from tests.behavioral.flows.common import seed_document
    from tests.generators.moderations import ladder_rung

    full = {
        **roles.ENABLED,
        "role_ladder": {
            "style": "composition",
            "values": [ladder_rung(days, "202") for days in FULL_LADDER],
        },
    }
    seed_document(deps, roles.FORM, full)
    guild = roles.server_where_keiko_gives_every_role()
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(roles.FORM)

    view = scenario.current_message.view
    assert_within_limits(view, "default_roles panel with 5 rungs")
    assert_ids_are_unique(view, "default_roles panel with 5 rungs")
    await scenario.click("edit:group:ladder")
    screen = scenario.current_message.view
    assert_within_limits(screen, "default_roles ladder screen with 5 rungs")
    assert_ids_are_unique(screen, "default_roles ladder screen with 5 rungs")
    await scenario.finish()


def test_the_auto_roles_card_ladder_screen_with_a_full_ladder_fits_its_estimate():
    """PR10 rounds 3 and 4: the card's ladder screen draws each of five time roles as a
    block with its Edit and Remove, inside Discord's limits and within the compiler's
    estimate."""
    from dataclasses import replace

    from app.forms.discord.views import layout_of
    from app.forms.form.actions.action import Context
    from app.forms.form.actions.configuration_card import render
    from app.forms.form.form_state import Answer, Origin, Setup, new_session
    from app.forms.form.form_yaml import DefinitionRegistry, ListStep, group_components

    definition = DefinitionRegistry().get("default_roles")
    card = definition.step("default_roles_config")
    [group] = [section for section in card.sections if section.collapsed_on_card()]
    lists = {step.key: step for step in definition.steps if isinstance(step, ListStep)}
    rungs = tuple(
        {"time": Answer(f"P{days}D"), "role": Answer(str(200 + index))}
        for index, days in enumerate(FULL_LADDER)
    )
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin("123456789", "555", "pt-br"),
        ttl_seconds=60,
        answers={"role_ladder": Answer(rungs)},
    ).at(card.key)
    context = Context(definition=definition, steps=definition.steps, locale="pt-br")

    shown = replace(session, opened_group=group.key)
    view = layout_of(
        render(card, shown, context), lambda action, arg=None: "k:s:1:x", None
    )
    drawn = len(list(walk_items(view)))
    estimate = group_components(card, group, lists)
    assert_within_limits(view, "default_roles card ladder screen with 5 rungs")
    assert drawn <= estimate, f"{drawn} drawn, estimated {estimate}"
    print(f"default_roles card ladder screen: {drawn} drawn, estimated {estimate}")


LONGEST_LADDER = (
    ((1, "months"), 350),
    ((1, "years"), 715),
    ((2, "years"), 1080),
    ((3, "years"), 1810),
    ((5, "years"), 3640),
)
SNOWFLAKE = 1246698994160242000


@pytest.mark.parametrize("locale", ["en-us", "pt-br"])
async def test_the_time_roles_question_with_every_role_full_fits_its_card(
    scenario_factory, deps, locale
):
    """Five time roles, each past its preview with members whose lines read longest.

    Broke as: the question wrote up to ten lines and "and N more" for each of five
    time roles, with no regard to the embed, so five roles of eleven members who
    joined years ago passed Discord's 4096 characters (4398 in en-us); Discord
    refused the question after the save, and the admin never saw it.
    Shared behaviour: none, the question auto roles asks after a save, drawn by
    `response_card` since the round-6 mark. Consumer that exposed it: a setup with
    five time roles.
    Guaranteed: the question is a card within Discord's forty components and 4000
    characters, naming three roles of five members at most and how many more, with
    See all below it, which opens the rest, each page within a field.
    """
    from datetime import datetime, timedelta, timezone

    from app.constants import DiscordLimits, ViewConstants
    from app.core.utils import ml
    from tests.behavioral.flows import roles
    from tests.helpers.components import button_labels
    from tests.mocks.discord import create_member

    guild = roles.server_where_keiko_gives_every_role()
    deps.bot.get_guild = lambda guild_id: guild
    now = datetime.now(timezone.utc)
    names = [f"Veterans of the server, rank {index}" for index in range(5)]
    roles.add_roles(guild, names, first_id=SNOWFLAKE)
    for index, (_time, days) in enumerate(LONGEST_LADDER):
        for member in range(ViewConstants.COMPOSITION_PREVIEW_LIMIT + 1):
            create_member(
                guild,
                id=SNOWFLAKE + 100 * (index + 1) + member,
                name=f"A member with a long display name {member}",
                joined_at=now - timedelta(days=days),
            )
    scenario = await scenario_factory(locale=locale, guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    for name, (time, _days) in zip(names, LONGEST_LADDER):
        await roles.add_rung(scenario, time, name)
    await scenario.click("done")
    await scenario.confirm()

    question = scenario.current_message
    assert not question.embeds, "the question is a card"
    assert_within_limits(question.view, "the time roles question with five full roles")
    assert_ids_are_unique(question.view, "the time roles question with five full roles")
    assert ml("buttons.see_all.label", locale) in button_labels(question)

    await scenario.click(ml("buttons.see_all.label", locale))
    for _page in range(len(LONGEST_LADDER)):
        [field] = scenario.expect_message(ephemeral=True)["embed"]["fields"]
        assert len(field["value"]) <= DiscordLimits.EMBED_FIELD_VALUE
        await scenario.click(">")
    await scenario.finish()


@pytest.mark.parametrize("months", [1, 12], ids=["one-month", "every-month"])
async def test_a_birthday_panel_with_a_full_list_is_within_discord_limits(
    scenario_factory, deps, months
):
    """bd-6: twenty-five birthdays, all in one month or spread over the year,
    keep the panel within the limits with its page and its footer row."""
    from app.commands.birthdays.service import save_birthday_config
    from app.core.servers import set_feature_enabled
    from tests.generators.birthdays import saved_birthday

    await save_birthday_config(
        "123456789", "100", False, notification_time="08:00"
    )
    for index in range(25):
        month = index % months + 1
        saved_birthday("123456789", str(1000 + index), f"{month:02d}-{index + 1:02d}")
    await set_feature_enabled("123456789", "reminders_birthday", True)
    scenario = await scenario_factory(locale="pt-br").start_command("reminders_birthday")

    view = scenario.current_message.view
    assert_within_limits(view, f"birthday panel, 25 birthdays over {months} months")
    assert_ids_are_unique(view, "birthday panel")
    await scenario.finish()


async def test_the_block_links_card_with_a_full_list_is_within_discord_limits(
    scenario_factory,
):
    """bl-7: the custom links are drawn with Add and Remove, and twenty-five of
    them still fit, on the exceptions screen that holds them (round 5 E) and on
    the card that tells them after Voltar."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:exceptions")
    for index in range(25):
        await scenario.click("customize:own_links")
        await scenario.submit_modal({"Digite o link ou site": f"site{index}.com"})

    assert len(scenario.answers["custom_links"]) == 25
    view = scenario.current_message.view
    assert_within_limits(view, "block_links exceptions with 25 links")
    assert_ids_are_unique(view, "block_links exceptions with 25 links")
    await scenario.click("back")
    view = scenario.current_message.view
    assert_within_limits(view, "block_links card with 25 links")
    assert_ids_are_unique(view, "block_links card with 25 links")
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
    ("reaction_roles", "menu"): {
        "channel": "1",
        "title": "x" * 50,
        "text": "y" * 100,
        "footer": "z" * 100,
        "image": "https://cdn.example/menu.png",
        "mode": "one",
        "roles": [str(role) for role in range(1, 11)],
    },
    ("default_roles", "rung"): {
        "time": "P521W", "amount": "521", "unit": "weeks", "role": "1",
    },
    ("reminders_birthday", "birthday_config"): {
        "channel": "1",
        "timezone": "UTC",
        "notification_time": "10:00",
        "mention": "roles",
        "mention_roles": ["1", "2"],
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
        "notification_messages": ["a" * 100, "b" * 100, "c" * 100],
        "mention": "roles",
        "mention_roles": ["1", "2"],
    },
    ("notifications_kick", "notification"): {
        "channel": "1",
        "streamer": "xqcow",
        "notification_messages": ["a" * 100, "b" * 100, "c" * 100],
        "mention": "roles",
        "mention_roles": ["1", "2"],
    },
    ("notifications_youtube_video", "notification"): {
        "channel": "1",
        "youtuber": "pewdiepie",
        "youtuber_avatar": "https://cdn.example/avatar.png",
        "notification_messages": ["a" * 100, "b" * 100, "c" * 100],
        "mention": "roles",
        "mention_roles": ["1", "2"],
    },
    ("notifications_youtube_live", "notification"): {
        "channel": "1",
        "youtuber": "pewdiepie",
        "youtuber_avatar": "https://cdn.example/avatar.png",
        "notification_messages": ["a" * 100, "b" * 100, "c" * 100],
        "mention": "roles",
        "mention_roles": ["1", "2"],
    },
}


def _every_step(steps):
    from app.forms.form.form_yaml import ListStep

    for step in steps:
        yield step
        if isinstance(step, ListStep):
            yield from _every_step(step.steps)


def test_every_card_of_every_form_is_listed_at_its_largest():
    from app.forms.form.form_yaml import CardStep, DefinitionRegistry

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
    from app.forms.discord.views import layout_of
    from app.forms.form.actions.action import Context
    from app.forms.form.actions.configuration_card import render
    from app.forms.form.form_state import Answer, Origin, Setup, new_session
    from app.forms.form.form_yaml import (
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
    rungs = tuple(
        {"time": Answer(f"P{days}D"), "role": Answer(str(index))}
        for index, days in enumerate((1, 3, 7, 14, 30))
    )
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin("123456789", "555", locale),
        ttl_seconds=60,
        answers={
            card_key: Answer(None, LARGEST_CARDS[(form, card_key)]),
            "custom_links": Answer(links),
            "role_ladder": Answer(rungs),
        },
    ).at(card_key)
    designs = {design.value: f"https://cdn.example/{design.value}.png"
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
    assert_within_limits(view, f"{form}.{card_key} {locale}")
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

    from app.forms.discord.views import layout_of
    from app.forms.form.actions.action import Context
    from app.forms.form.actions.configuration_card import render
    from app.forms.form.form_state import Answer, Origin, Setup, new_session
    from app.forms.form.form_yaml import (
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
        assert_within_limits(view, f"block_links {name} in {mode}")
        assert drawn <= estimate, f"{name}: {drawn} drawn, estimated {estimate}"
        print(f"block_links {name} {mode} {locale}: {drawn} components")


@pytest.mark.parametrize(
    "form",
    [
        "notifications_twitch",
        "notifications_kick",
        "notifications_youtube_video",
        "notifications_youtube_live",
    ],
)
async def test_a_full_notification_panel_with_its_item_buttons_is_within_discord_limits(
    scenario_factory, deps, form
):
    """N5 (round 2): every item carries Edit, Preview and Remove, and the list
    title counts its items; a list at its most items still fits."""
    from app.forms.form.form_yaml import registry
    from tests.behavioral.flows import module_of
    from tests.behavioral.flows.common import seed_document

    paths = module_of(form)
    most = registry.get(form).composition.items.max
    items = [paths.notification(str(100 + index), f"name{index}") for index in range(most)]
    seed_document(
        deps, form, {**paths.ENABLED, "notifications": {"style": "composition", "values": items}}
    )
    scenario = await scenario_factory(locale="pt-br").start_command(form)

    view = scenario.current_message.view
    assert_within_limits(view, f"{form} panel with {most} items")
    assert_ids_are_unique(view, f"{form} panel with {most} items")
    await scenario.finish()
