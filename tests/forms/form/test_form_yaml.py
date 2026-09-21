"""The compiler accepts the seven forms and refuses what the models cannot hold."""

from pathlib import Path

import pytest
import yaml

from app.settings.form.form_yaml import (
    CompileError,
    DefinitionRegistry,
    DiskSource,
    compile_form,
    compile_with_warnings,
)

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
    ("composition_without_max.yml", "max: a composition declares max"),
    ("last_step_not_review.yml", "the last step is review"),
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


@pytest.mark.parametrize("fixture,message", REJECTED, ids=[n for n, _ in REJECTED])
def test_invalid_definitions_are_refused_with_the_reason(fixture, message):
    raw = yaml.safe_load((INVALID / fixture).read_text(encoding="utf-8"))
    with pytest.raises(CompileError) as failure:
        compile_form(fixture.removesuffix(".yml"), raw)
    assert message in str(failure.value)
