"""The compiler accepts the seven forms and refuses what the models cannot hold."""

import typing
from pathlib import Path

import pytest
import yaml

from app.settings.form.actions.configuration_card import SECTION_KINDS
from app.settings.form.conditions import Scope, chosen_text
from app.settings.form.form_yaml import (
    CompileError,
    DefinitionRegistry,
    DiskSource,
    Section,
    compile_form,
    compile_with_warnings,
)
from app.settings.form.responses.summary import item_name

pytestmark = pytest.mark.unit

FORMS = (
    "block_links",
    "default_roles",
    "notifications_twitch",
    "notifications_youtube_video",
    "reminders_birthday",
    "stream_elements_commands",
    "welcome_messages",
)
INVALID = Path(__file__).parent / "fixtures" / "invalid"

REJECTED = [
    ("unknown_field.yml", "colour: unknown field"),
    ("missing_locale.yml", "pt-br"),
    ("forward_reference.yml", "'mode' is not produced earlier"),
    ("self_reference.yml", "'name' is not produced earlier"),
    ("value_not_an_option.yml", "'c' is not an option of 'mode'"),
    ("bad_section_type.yml", "rainbow"),
    ("unknown_validator.yml", "unknown validator 'validate_unicorn'"),
    ("unknown_normalizer.yml", "unknown normalizer 'unicorn'"),
    ("panel_group_title_when_unknown_key.yml", "'nao_existe' is not produced earlier"),
    ("state_value_names_no_field.yml", "state.value names none of the fields"),
    ("modal_with_six_inputs.yml", "a modal holds at most 5 inputs"),
    ("unkeyed_field_without_value.yml", "fields without a key need state.value"),
    (
        "edit_by_field_inside_a_composition.yml",
        "edit_by_field is not allowed inside a composition",
    ),
    ("composition_without_max.yml", "max: a composition declares max"),
    ("last_step_not_review.yml", "the last step is review"),
    ("edit_by_item_over_the_limit.yml", "at most 3 items can each carry an Edit"),
    ("lookup_answer_without_a_field.yml", "lookup_answers names service.field"),
    ("media_key_unknown.yml", "'avatar' is no state key or lookup answer"),
    ("step_media_key_unknown.yml", "'avatar' is no lookup answer of the step"),
    ("fills_into_unknown.yml", "fills: 'messages' is no state key of the card"),
    ("step_title_when_unknown_key.yml", "'nao_existe' is not produced earlier"),
    ("unique_by_unknown_key.yml", "unique_by: 'nickname' is no item key"),
    ("search_without_a_member.yml", "unique_by is picked on a member select"),
    (
        "remove_each_without_edit_by_item.yml",
        "a button beside each item needs edit_by_item",
    ),
    (
        "remove_each_without_only_item_warning.yml",
        "only_item_warning: removing the only item of a required list",
    ),
    ("footer_entry_without_a_label.yml", "names its button"),
    ("selects_without_a_select.yml", "selects.selects: Tuple should have at least 1"),
    ("group_holding_an_unknown_section.yml", "sections: 'places' is no section"),
    ("group_holding_a_group.yml", "'inner' is a group, and a group holds no group"),
    ("preview_needs_without_a_preview.yml", "preview_needs: the card draws no preview"),
    ("preview_needs_unknown_key.yml", "preview_needs: 'nick' unknown"),
]


@pytest.mark.parametrize("form", FORMS)
def test_every_shipped_form_compiles(form):
    definition = compile_form(form, DiskSource().load(form))
    assert definition.key == form
    assert definition.steps[0].kind == "intro"
    assert definition.steps[-1].kind == "review"


def test_the_registry_loads_every_form_at_boot():
    registry = DefinitionRegistry()
    loaded = registry.load_all()
    assert {definition.key for definition in loaded} == set(FORMS)
    assert registry.get("block_links").version == 1


def test_legacy_spellings_compile_with_a_deprecation_warning():
    compiled = compile_with_warnings("block_links", DiskSource().load("block_links"))
    assert any("`condition`" in warning for warning in compiled.warnings)
    assert any("'parent.mode'" in warning for warning in compiled.warnings)


def test_an_implicit_parent_reference_becomes_explicit():
    definition = compile_form("block_links", DiskSource().load("block_links"))
    match_type = definition.composition.steps[1]
    assert match_type.description_when[0].when.key == "parent.mode"


def test_composition_limits_come_from_the_yaml():
    source = DiskSource()
    twitch = compile_form("notifications_twitch", source.load("notifications_twitch"))
    birthday = compile_form("reminders_birthday", source.load("reminders_birthday"))
    assert twitch.items.max == 3
    assert birthday.items.unique_by == "user"


def test_an_item_is_named_by_what_makes_it_unique():
    """An item reads as its `unique_by` value, or as its title and number."""
    source = DiskSource()
    twitch = compile_form("notifications_twitch", source.load("notifications_twitch"))
    links = compile_form("block_links", source.load("block_links"))
    streamer = {"streamer": {"value": "gaules"}, "channel": {"value": "100"}}
    assert twitch.composition is not None and links.composition is not None

    assert item_name(twitch.composition, streamer, 0, "pt-br") == "gaules"
    assert item_name(links.composition, {"link": "a.com"}, 0, "pt-br") == "a.com"
    assert item_name(twitch.composition, {}, 1, "pt-br").endswith(" #2")


@pytest.mark.parametrize(
    "form", ["notifications_twitch", "notifications_youtube_video"]
)
def test_the_notification_lists_remove_each_item(form):
    """N5 (round 2): each streamer or channel leaves on its own; round 3 took
    the pause off; round 4 counts the items on the list's title line."""
    composition = compile_form(form, DiskSource().load(form)).composition
    assert composition.items.remove_each
    assert "$count" in composition.title_count.get("pt-br")


@pytest.mark.parametrize(
    "mode,line",
    [
        (
            "block_all",
            "Seus próprios sites ou links que eu sempre libero, um de cada vez!",
        ),
        (
            "allow_all",
            "Seus próprios sites ou links que eu bloqueio, deixando todos os outros "
            "passarem. Um de cada vez!",
        ),
    ],
)
def test_the_custom_links_say_what_the_mode_in_force_does_with_them(mode, line):
    """The writing-style review: one sentence covered both modes and named an
    "allow-all" mode that no longer exists; each mode now reads its own."""
    composition = compile_form(
        "block_links", DiskSource().load("block_links")
    ).composition
    described = chosen_text(
        composition.description, composition.description_when, Scope({"mode": mode})
    )
    assert described.get("pt-br") == line
    assert "permitir todos" not in described.get("pt-br")
    assert "allow-all" not in described.get("en-us")


@pytest.mark.parametrize(
    "form,needs",
    [("notifications_twitch", "streamer"), ("notifications_youtube_video", "youtuber")],
)
def test_the_notification_previews_wait_for_who_they_announce(form, needs):
    """H (round 6): the preview names its key and an error both locales hold."""
    from app.services.utils import ml

    card = compile_form(form, DiskSource().load(form)).composition.steps[0]
    error = f"errors.{card.preview_needs.error}.message"

    assert card.preview_needs.keys == (needs,)
    assert ml(error, "en-us") != error
    assert ml(error, "pt-br") != ml(error, "en-us"), "pt-br holds its own copy"


@pytest.mark.parametrize("fixture,message", REJECTED, ids=[n for n, _ in REJECTED])
def test_invalid_definitions_are_refused_with_the_reason(fixture, message):
    raw = yaml.safe_load((INVALID / fixture).read_text(encoding="utf-8"))
    with pytest.raises(CompileError) as failure:
        compile_form(fixture.removesuffix(".yml"), raw)
    assert message in str(failure.value)


def _declared_types():
    members = typing.get_args(typing.get_args(Section)[0])
    return {member.model_fields["type"].annotation.__args__[0] for member in members}


def test_every_declared_section_type_has_a_kind_and_no_kind_is_left_over():
    """Every card section type the YAML may declare is one `SectionKind`."""
    assert set(SECTION_KINDS) == _declared_types()


def test_a_selects_section_owns_the_key_of_every_select_it_holds():
    """bl-6: a rule, a required key or a summary may name a select's key."""
    definition = compile_form("block_links", DiskSource().load("block_links"))
    card = definition.step("link_settings")
    exempt = next(section for section in card.sections if section.key == "exempt")

    assert [select.key for select in exempt.picks()] == [
        "allowed_chats",
        "allowed_roles",
    ]
    assert {"allowed_chats", "allowed_roles"} <= set(card.state_keys())


def test_the_card_estimate_counts_the_controls_each_section_draws_on_it():
    """N4: each select drawn on the card takes its row and its place, a yes or
    no takes a row of two buttons, and a typed field keeps its row of buttons."""
    from app.settings.form.form_yaml import CV2_MAX_COMPONENTS, card_components

    definition = compile_form("block_links", DiskSource().load("block_links"))
    card = definition.step("link_settings")
    exempt = next(section for section in card.sections if section.key == "exempt")
    one_select = exempt.model_copy(update={"selects": exempt.selects[:1]})
    narrower = card.model_copy(
        update={
            "sections": tuple(
                one_select if section is exempt else section
                for section in card.sections
            )
        }
    )
    kinds = {section.type: section.controls_drawn() for section in card.sections}

    assert card_components(card) == card_components(narrower) + 4, (
        "a select fewer is a row and a place fewer under each group holding it"
    )
    assert card_components(card) <= CV2_MAX_COMPONENTS
    assert kinds == {
        "button-options": 2,
        "group": 2,
        "multi-select": 2,
        "modal-input": 2,
        "selects": 4,
        "list": 3,
    }
    birthday = compile_form(
        "reminders_birthday", DiskSource().load("reminders_birthday")
    ).step("birthday_config")
    mention = next(s for s in birthday.sections if s.type == "boolean-toggle")
    assert mention.controls_drawn() == 3


def test_only_sections_that_keep_a_default_tell_whether_they_were_customized():
    keeping_a_default = {
        name for name, kind in SECTION_KINDS.items() if kind.custom is not None
    }
    assert keeping_a_default == {"title-content", "file-upload"}
