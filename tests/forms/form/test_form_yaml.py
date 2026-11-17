"""The compiler accepts every declared form and refuses what the models cannot hold."""

import typing
from collections import Counter
from pathlib import Path

import pytest
import yaml

from app.core import commands as command_registry
from app.core.commands import RefusedFile, copy_files, form_files
from app.core.conditions import Scope
from app.forms.form.actions.configuration_card import SECTION_KINDS
from app.forms.form.form_yaml import (
    CompileError,
    DefinitionRegistry,
    DiskSource,
    Section,
    Text,
    Vocabulary,
    compile_form,
    overridden_text,
    shared_vocabulary,
    written_out,
)
from app.forms.form.responses.summary import item_name

pytestmark = pytest.mark.unit

FORMS = DiskSource().list()
INVALID = Path(__file__).parent / "fixtures" / "invalid"
VOCABULARY = Path(__file__).resolve().parents[3] / "app" / "forms" / "vocabulary.yml"

REJECTED = [
    ("unknown_field.yml", "colour: unknown field"),
    ("missing_locale.yml", "pt-br"),
    ("forward_reference.yml", "'mode' is not produced earlier"),
    ("self_reference.yml", "'name' is not produced earlier"),
    ("value_not_an_option.yml", "'c' is not an option of 'mode'"),
    ("bad_section_type.yml", "rainbow"),
    ("unknown_validator.yml", "unknown validator 'unicorn'"),
    (
        "validator_without_its_params.yml",
        "takes the params ['error', 'id', 'item', 'service']",
    ),
    ("unknown_transform.yml", "unknown transform 'unicorn'"),
    ("unknown_normalizer.yml", "unknown normalizer 'unicorn'"),
    ("group_override_unknown_key.yml", "'nao_existe' is not produced earlier"),
    ("keys_value_names_no_input.yml", "keys.value names none of the inputs"),
    ("modal_with_six_inputs.yml", "a modal holds at most 5 inputs"),
    ("unkeyed_input_without_value.yml", "inputs without a key need keys.value"),
    (
        "field_edit_inside_a_list.yml",
        "edit: a card inside a list is edited whole, not by field",
    ),
    ("list_without_max.yml", "items.max: Field required"),
    ("last_step_not_review.yml", "the last step is review"),
    ("item_edit_over_the_limit.yml", "at most 3 items can each carry an Edit"),
    ("lookup_answer_without_a_field.yml", "lookup_answers names service.field"),
    ("image_unknown.yml", "image: 'avatar' is no state key or lookup answer"),
    ("step_image_unknown.yml", "image: 'avatar' is no lookup answer of the step"),
    ("fills_into_unknown.yml", "fills: 'messages' is no state key of the card"),
    ("step_override_unknown_key.yml", "'nao_existe' is not produced earlier"),
    ("unique_key_unknown.yml", "unique.key: 'nickname' is no item key"),
    ("search_without_a_member.yml", "unique.key is picked on a member select"),
    (
        "item_remove_without_item_edit.yml",
        "remove: a Remove beside each item needs edit: item",
    ),
    (
        "item_remove_without_last_item_warning.yml",
        "last_item_warning: removing the last item of a required list",
    ),
    ("group_button_without_a_label.yml", "button.label: Field required"),
    ("selects_without_a_select.yml", "selects.selects: Tuple should have at least 1"),
    ("group_holding_an_unknown_section.yml", "sections: 'places' is no section"),
    ("group_holding_a_group.yml", "'inner' is a group, and a group holds no group"),
    (
        "preview_required_without_an_error.yml",
        "a preview that requires keys names its error",
    ),
    ("preview_required_unknown_key.yml", "preview.required: 'nick' unknown"),
    (
        "unknown_ref.yml",
        "unknown_ref.name: label: ${ref.chanel} is no text of app/forms/vocabulary.yml;"
        " did you mean ${ref.channel}?",
    ),
    ("legacy_action.yml", "Unable to extract tag using discriminator 'type'"),
    ("legacy_condition.yml", "condition: unknown field"),
    ("bare_parent_key.yml", "'mode' is not produced earlier"),
    ("hyphenated_key.yml", "customize-label: unknown field"),
    ("button_on_a_select.yml", "button: unknown field"),
    ("unknown_root_field.yml", "colour: unknown field"),
    ("form_key_written.yml", "key: unknown field, the key is the folder"),
    ("unknown_group.yml", "'nope' is no entry of groups"),
    ("unused_group.yml", "groups.settings: no step or field names it"),
    ("group_inside_a_list.yml", "a list item names no group"),
    (
        "field_without_a_label_or_an_owner.yml",
        "'date' is owned by no section, so it writes its own label",
    ),
    ("default_for_an_unknown_key.yml", "defaults: 'colour' unknown"),
    ("override_with_its_text_flat.yml", "overrides.0.en-us: unknown field"),
    ("context_unknown.yml", "${context.server_nickname} is no context entry"),
    ("override_description_after_its_own_answer.yml", "'name' is not produced earlier"),
    ("single_choice_unique.yml", "unique: unknown field"),
]


@pytest.mark.parametrize("form", FORMS)
def test_every_shipped_form_compiles(form):
    definition = compile_form(form, DiskSource().load(form))
    assert definition.key == form
    assert definition.steps[0].type == "intro"
    assert definition.steps[-1].type == "review"


def test_the_registry_loads_every_form_at_boot():
    registry = DefinitionRegistry()
    loaded = registry.load_all()
    assert {definition.key for definition in loaded} == set(FORMS)
    assert registry.get("block_links").version == 1


def test_a_list_item_names_the_mode_of_its_form_through_parent():
    """The compiler used to rewrite a bare outer key to `parent.` with a warning;
    the form writes the scope itself and a bare one is refused."""
    definition = compile_form("block_links", DiskSource().load("block_links"))
    match_type = definition.composition.steps[1]
    assert match_type.overrides[0].when.key == "parent.mode"


def test_a_title_override_may_read_the_steps_own_answer():
    """The first override that holds wins, so the rule of every override up to the
    last one that writes a description is read before the step is answered; a title
    override after it is read once the step is answered."""
    raw = yaml.safe_load(
        (INVALID / "override_description_after_its_own_answer.yml").read_text()
    )
    raw["steps"][2]["overrides"].reverse()

    step = compile_form("fixture", raw).steps[2]

    assert [item.title is not None for item in step.overrides] == [False, True]


def test_a_form_text_reads_a_command_name_from_the_context():
    raw = yaml.safe_load((INVALID / "context_unknown.yml").read_text())
    raw["steps"][0]["description"] = {
        "en-us": "Run `${context.commands.setup}` on ${context.server_name}",
        "pt-br": "Rode `${context.commands.setup}` em ${context.server_name}",
    }

    intro = compile_form("fixture", raw).steps[0]

    assert intro.description.en_us == "Run `/setup` on ${context.server_name}"
    assert intro.description.pt_br == "Rode `/configurar` em ${context.server_name}"


def test_composition_limits_come_from_the_yaml():
    source = DiskSource()
    twitch = compile_form("notifications_twitch", source.load("notifications_twitch"))
    birthday = compile_form("reminders_birthday", source.load("reminders_birthday"))
    assert twitch.items.max == 3
    assert birthday.items.unique_key() == "user"


def test_an_item_is_named_by_what_makes_it_unique():
    """An item reads as its `unique.key` value, or as its title and number."""
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
def test_the_notification_lists_remove_each_item_on_its_own(form):
    """N5 (round 2): each streamer or channel leaves on its own; round 3 took
    the pause off; round 4 counts the items on the list's title line."""
    composition = compile_form(form, DiskSource().load(form)).composition
    assert composition.remove == "item"
    assert "$count" in composition.counter.get("pt-br")


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
    described = overridden_text(
        composition.description,
        composition.overrides,
        "description",
        Scope({"mode": mode}),
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
    from app.forms.form.copy import error_message

    card = compile_form(form, DiskSource().load(form)).composition.steps[0]
    error = card.preview.error
    english = error_message(error, {}, "en-us")

    assert card.preview.required.all == (needs,)
    assert error.startswith(f"{form}."), "the error is the command's own copy"
    assert english != error
    assert error_message(error, {}, "pt-br") != english, "pt-br holds its own copy"


@pytest.mark.parametrize("fixture,message", REJECTED, ids=[n for n, _ in REJECTED])
def test_invalid_definitions_are_refused_with_the_reason(fixture, message):
    raw = yaml.safe_load((INVALID / fixture).read_text(encoding="utf-8"))
    with pytest.raises(CompileError) as failure:
        compile_form(fixture.removesuffix(".yml"), raw)
    assert message in str(failure.value)


TYPOS = [
    (
        "stream_elements_commands",
        "${answer.streamer}",
        "${answer.streamr}",
        "confirm: description.en-us: ${answer.streamr}: 'streamr' is not produced "
        "earlier; did you mean ${answer.streamer}?",
    ),
    (
        "stream_elements_commands",
        "${answer.streamer}",
        "${answr.streamer}",
        "answr is no kind of token (answer, plural, context, ref); did you mean "
        "${answer.streamer}?",
    ),
    (
        "stream_elements_commands",
        "${answer.streamer}",
        "${answer.streamer",
        "is never closed by a }",
    ),
    (
        "stream_elements_commands",
        "${answer.streamer}",
        "{answer:streamer}",
        "{answer:streamer} is the old spelling: write ${answer.streamer}",
    ),
    (
        "stream_elements_commands",
        "${context.prefix}",
        "${context.prefx}",
        "${context.prefx} is no context entry of app/core/global.yml; did you mean "
        "${context.prefix}?",
    ),
    (
        "block_links",
        "${answer.link:host|",
        "${answer.link:hots|",
        "'hots' is no format (channel, role, user, bullet, code_block, numbered, "
        "code, boolean, customized, month_day, host); did you mean :host?",
    ),
    (
        "default_roles",
        "${plural.default_roles|",
        "${plural.default_role|",
        "'default_role' is not produced earlier; did you mean ${plural.default_roles}?",
    ),
    (
        "default_roles",
        "${context.members_count}",
        "${context.members_cont}",
        "did you mean ${context.members_count}?",
    ),
    (
        "notifications_youtube_video",
        "$item",
        "$itme",
        "$itme is no fact of app/core/global.yml; did you mean $item?",
    ),
    (
        "notifications_youtube_video",
        "$max",
        "$maxx",
        "$maxx is no fact of app/core/global.yml; did you mean ${max}x or $max?",
    ),
    (
        "notifications_youtube_video",
        "$count",
        "$counts",
        "did you mean ${count}s or $count?",
    ),
    (
        "notifications_twitch",
        "${ref.streamer}",
        "${ref.stremer}",
        "${ref.stremer} is no text of app/forms/vocabulary.yml; did you mean "
        "${ref.streamer}?",
    ),
]


@pytest.mark.parametrize(
    "form,written,typo,message",
    TYPOS,
    ids=[f"{form}: {typo}" for form, _written, typo, _message in TYPOS],
)
def test_a_typo_in_a_token_of_a_shipped_form_fails_its_compile(
    form, written, typo, message
):
    """A probe of the syntax study: each of these compiled and showed wrong before
    the one syntax (an answer never produced, a format no one knows, a fact the
    engine never fills); now the compiler names the form, the step, the text and the
    token, with the closest declared name."""
    import json

    source = json.dumps(DiskSource().load(form))
    assert written in source, "the probe edits a token the form writes"
    raw = json.loads(source.replace(written, typo, 1))

    with pytest.raises(CompileError) as failure:
        compile_form(form, raw)
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
    from app.forms.form.form_yaml import CV2_MAX_COMPONENTS, card_components

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
        "button_options": 2,
        "group": 2,
        "multi_select": 2,
        "modal_input": 2,
        "selects": 4,
        "list": 3,
    }
    birthday = compile_form(
        "reminders_birthday", DiskSource().load("reminders_birthday")
    ).step("birthday_config")
    mention = next(s for s in birthday.sections if s.type == "boolean_toggle")
    assert mention.controls_drawn() == 3


def test_only_sections_that_keep_a_default_tell_whether_they_were_customized():
    keeping_a_default = {
        name for name, kind in SECTION_KINDS.items() if kind.custom is not None
    }
    assert keeping_a_default == {"title_content", "file_upload"}


def _text(word):
    return {"en-us": word, "pt-br": f"{word} (pt)"}


INTRO = {
    "type": "intro",
    "key": "form",
    "title": _text("🧪 Fixture"),
    "description": _text("intro"),
}
NAME = {
    "type": "text",
    "key": "name",
    "title": _text("Name"),
    "description": _text("type"),
    "label": _text("Name"),
}
REVIEW = {"type": "review", "key": "confirm"}
CHANNEL = {
    "type": "channel_select",
    "key": "channel",
    "emoji": "📺",
    "label": _text("Channel"),
    "keys": {"value": "channel"},
}


def _card(**fields):
    return {
        "type": "card",
        "key": "card",
        "description": _text("card"),
        "sections": [CHANNEL],
        **fields,
    }


def _list(*steps):
    return {
        "type": "list",
        "key": "items",
        "items": {"max": 2},
        "title": _text("Items"),
        "description": _text("list"),
        "steps": list(steps),
    }


def _compiled(*steps, review=REVIEW, vocabulary=None):
    return compile_form("fixture", {"steps": [INTRO, *steps, review]}, vocabulary)


def test_a_step_without_a_footer_reads_the_standard_one_at_every_depth():
    """F54: the bug-report footer was written out 28 times across the forms."""
    definition = _compiled(_list(NAME))
    footer = shared_vocabulary().fallbacks.footer

    assert [step.footer for step in definition.steps] == [footer] * 3
    assert definition.composition.steps[0].footer == footer
    assert footer.get("pt-br") == "• Use o comando `/reportar` para me contar um bug"


def test_a_step_that_writes_footer_null_keeps_no_footer():
    assert _compiled({**NAME, "footer": None}).step("name").footer is None


def test_the_review_reads_the_intro_title_and_the_standard_description():
    definition = _compiled(NAME)
    review = definition.step("confirm")

    assert review.title == definition.steps[0].title
    assert review.description == shared_vocabulary().fallbacks.review_description
    assert "`/${context.command}`" in review.description.get("en-us")


def test_a_card_of_the_form_reads_the_intro_title_and_a_list_card_writes_its_own():
    definition = _compiled(_card())
    assert definition.step("card").title == definition.steps[0].title

    with pytest.raises(CompileError, match="a card inside a list writes its title"):
        _compiled(_list(_card()))


def test_a_step_keeps_every_default_it_writes_itself():
    review = {
        **REVIEW,
        "title": _text("Own"),
        "description": _text("own"),
        "footer": _text("• own"),
    }
    definition = _compiled(_card(title=_text("Card")), review=review)
    confirm = definition.step("confirm")

    assert definition.step("card").title.get("en-us") == "Card"
    assert (confirm.title.get("en-us"), confirm.description.get("en-us")) == (
        "Own",
        "own",
    )
    assert confirm.footer.get("pt-br") == "• own (pt)"


def test_a_ref_reads_its_shared_text_wherever_a_text_is_allowed():
    vocabulary = Vocabulary.model_validate(
        {
            "fallbacks": {
                "footer": _text("• foot"),
                "review_description": _text("sum"),
            },
            "texts": {"channel": _text("Channel")},
        }
    )
    ref = "${ref.channel}"
    pick = {
        "type": "single_choice",
        "key": "pick",
        "title": ref,
        "description": _text("pick"),
        "options": [{"label": ref, "value": "a"}],
    }
    card = _card(
        title=ref, sections=[{**CHANNEL, "label": ref, "screen": {"title": ref}}]
    )
    definition = _compiled(pick, card, vocabulary=vocabulary)
    channel = Text.model_validate(_text("Channel"))
    section = definition.step("card").sections[0]

    assert definition.step("pick").title == channel
    assert definition.step("pick").options[0].label == channel
    assert (section.label, section.own_screen_title()) == (channel, channel)
    assert definition.step("confirm").description.get("en-us") == "sum"
    assert definition.step("confirm").footer == Text.model_validate(_text("• foot"))


def test_an_unknown_ref_in_a_list_names_the_item_step_and_the_field():
    unknown = {**CHANNEL, "label": "${ref.nope}"}
    with pytest.raises(CompileError) as failure:
        _compiled(_list(_card(title=_text("Item"), sections=[unknown])))
    assert str(failure.value) == (
        "fixture.card: sections[0].label: ${ref.nope} is no text of "
        "app/forms/vocabulary.yml"
    )


def _texts(value):
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    if not isinstance(value, dict):
        return []
    if set(value) == {"en-us", "pt-br"}:
        return [(value["en-us"], value["pt-br"])]
    return [text for item in value.values() for text in _texts(item)]


def test_no_text_is_written_out_three_times_across_the_forms():
    """F54: a text three fields share is a ref of the shared vocabulary or a default."""
    written = Counter(
        text for form in FORMS for text in _texts(DiskSource().load(form))
    )
    assert [en for (en, _), count in written.items() if count >= 3] == []


def _yaml_only(text):
    marks = {yaml.AnchorToken: "&", yaml.AliasToken: "*"}
    return [
        f"line {token.start_mark.line + 1}: {marks[type(token)]}{token.value}"
        for token in yaml.scan(text, Loader=yaml.SafeLoader)
        if type(token) in marks
    ]


COMMAND_FILES = [*copy_files().values(), *form_files().values()]


@pytest.mark.parametrize(
    "path",
    [*COMMAND_FILES, VOCABULARY],
    ids=[*(f"{path.parent.name}/{path.name}" for path in COMMAND_FILES), "vocabulary"],
)
def test_no_command_and_not_the_shared_vocabulary_writes_a_yaml_anchor_or_alias(path):
    found = _yaml_only(path.read_text(encoding="utf-8"))
    assert found == [], (
        f"{path.name}: {', '.join(found)}: a database document cannot hold a YAML "
        "anchor or alias, write a ${ref.<name>} or leave the default"
    )


def test_an_anchor_and_its_alias_are_both_found():
    written = "a: &label {en-us: A, pt-br: A}\nb: *label\n"
    assert _yaml_only(written) == ["line 1: &label", "line 2: *label"]


@pytest.mark.parametrize(
    "written,refusal",
    [
        ("steps:\n  - title: &hi {en-us: Hi, pt-br: Oi}\n", "is an anchor or an alias"),
        ("steps:\n  - key: one\n    key: two\n", "key twice"),
    ],
    ids=["anchor", "key twice"],
)
def test_a_form_yml_is_read_as_strictly_as_a_copy_yml(
    tmp_path, monkeypatch, written, refusal
):
    path = tmp_path / "form.yml"
    path.write_text(written, encoding="utf-8")
    monkeypatch.setattr(command_registry, "form_files", lambda: {"default_roles": path})

    with pytest.raises(RefusedFile, match=refusal):
        DiskSource().load("default_roles")


@pytest.mark.parametrize(
    "form,step,title",
    [
        ("welcome_messages", "confirm", "🎉 Welcome Messages"),
        ("welcome_messages", "welcome_config", "🎉 Welcome Messages"),
        ("notifications_twitch", "channel", "Text Channel"),
        ("stream_elements_commands", "streamer", "Streamer"),
    ],
)
def test_a_report_names_a_step_by_the_text_its_ref_or_default_holds(form, step, title):
    """The journey and the admin reports read the form outside the compiler: a step
    left to a default or naming a ref read as its key or as another of its texts."""
    from app.core.utils import describe_step

    assert describe_step(form, step) == title


def test_the_written_out_form_holds_every_text_the_vocabulary_lends_it():
    raw = DiskSource().load("welcome_messages")
    steps = written_out("welcome_messages", raw)["steps"]

    assert steps[-1]["title"] == steps[0]["title"] == steps[1]["title"]
    assert steps[-1]["description"]["en-us"].startswith("Here's a summary")
    assert {step["footer"]["en-us"] for step in steps} == {
        shared_vocabulary().fallbacks.footer.get("en-us")
    }
    assert steps[1]["sections"][0]["label"] == {"en-us": "Channel", "pt-br": "Canal"}
    assert "footer" not in raw["steps"][0], "the source document is left as written"


def test_a_rule_names_exactly_one_operator():
    """Broke as: a `when` leaf writing `not_in` and `matches` was compiled and then
    evaluated only by its first operator, the other dropped in silence (the
    evaluator in `conditions.py` reads one). Every rule of every form goes through
    the same `Leaf`, so a leaf with two operators is now refused at compile."""
    twice = {
        "type": "single_choice",
        "key": "pick",
        "title": _text("Pick"),
        "description": _text("pick"),
        "when": {"key": "name", "not_in": ["a"], "matches": "b"},
        "options": [{"label": _text("A"), "value": "a"}],
    }
    with pytest.raises(CompileError, match="exactly one operator"):
        _compiled(NAME, twice)


def test_a_text_step_takes_no_required_since_its_input_always_is():
    """Broke as: `required: false` on a text step compiled and changed nothing, the
    modal kept its input required (`required=step.required or True`). The input of
    a text step is always required, so the field is gone and writing it is refused."""
    from app.forms.form.actions import modal
    from app.forms.form.form_state import Setup, new_session
    from tests.forms.replay import ORIGIN

    with pytest.raises(CompileError, match="required: unknown field"):
        _compiled({**NAME, "required": False})
    definition = _compiled(NAME)
    session = new_session(("fixture", 1), Setup(), ORIGIN, ttl_seconds=60)
    context = modal.Context(definition=definition, locale="en-us")
    drawn = modal.render(definition.step("name"), session, context)
    assert [field.required for field in drawn.components[0].inputs] == [True]


def test_a_card_header_names_a_picture_of_keiko_that_exists():
    """Broke as: a header `thumbnail` naming no picture of Keiko compiled and drew
    no thumbnail, in silence (`KeikoIcons.BY_NAME.get(name, "")`). Every card
    header reads that map, so an unknown name is now refused at compile."""
    from app.constants import KeikoIcons

    header = {"title": _text("Card"), "thumbnail": "IMAGE_01"}
    with pytest.raises(CompileError, match="'default', 'nerd' or 'cake'"):
        _compiled(_card(title=_text("Card"), header=header))
    for form in FORMS:
        for step in compile_form(form, DiskSource().load(form)).steps:
            header = getattr(step, "header", None)
            if header is not None:
                assert header.thumbnail in KeikoIcons.BY_NAME, (form, step.key)


def test_the_vocabulary_is_read_as_strictly_as_a_form_yml(tmp_path):
    """Broke as: `vocabulary.yml` was read with `yaml.safe_load`, so a text written
    twice kept its last spelling in silence while every form.yml and copy.yml
    refused it. Every form reads the vocabulary, so it now goes through
    `strict_yaml` like them."""
    from app.forms.form.form_yaml import vocabulary_from

    path = tmp_path / "vocabulary.yml"
    path.write_text(
        "fallbacks:\n"
        "  footer: {en-us: F, pt-br: F}\n"
        "  summary: {en-us: S, pt-br: S}\n"
        "texts:\n"
        "  title: {en-us: A, pt-br: A}\n"
        "  title: {en-us: B, pt-br: B}\n",
        encoding="utf-8",
    )
    with pytest.raises(RefusedFile, match="title twice"):
        vocabulary_from(path)


@pytest.mark.parametrize(
    "written,refusal",
    [
        ("Turn on `/${context.comand}`", "did you mean ${context.command}?"),
        ("For $cout", "$cout is no fact of app/core/global.yml; did you mean $count?"),
        ("See ${ref.title}", "a shared text names no other shared text"),
        ("Use {context:commands.report}", "write ${context.commands.report}"),
    ],
)
def test_the_vocabulary_names_only_what_the_catalogs_declare(
    tmp_path, written, refusal
):
    from app.core.facts import TextError
    from app.forms.form.form_yaml import vocabulary_from

    path = tmp_path / "vocabulary.yml"
    path.write_text(
        "fallbacks:\n"
        f"  footer: {{en-us: '{written}', pt-br: F}}\n"
        "  review_description: {en-us: S, pt-br: S}\n"
        "texts:\n"
        "  title: {en-us: A, pt-br: A}\n",
        encoding="utf-8",
    )
    with pytest.raises(TextError) as failure:
        vocabulary_from(path)
    assert "fallbacks.footer.en-us: " in str(failure.value)
    assert refusal in str(failure.value)


@pytest.mark.parametrize("form", ["default_roles", "block_links"])
def test_a_press_on_a_single_choice_answers_the_value_it_names(form):
    """`unique: true` was written on both single choices and changed nothing: a
    button answers one value, kept as itself (proven equal with and without the
    flag before it was removed). The value stays typed as its option declares."""
    from app.forms.form.actions import options
    from app.forms.form.form_state import Setup, new_session
    from tests.forms.replay import ORIGIN

    definition = DefinitionRegistry().get(form)
    inner = definition.composition.steps if definition.composition else ()
    step = next(
        one for one in (*definition.steps, *inner) if one.type == "single_choice"
    )
    session = new_session((form, 1), Setup(), ORIGIN, ttl_seconds=60)
    for option in step.options:
        for payload in (str(option.value), option.value, [str(option.value)]):
            assert options.parse(step, payload, session, None)[step.key].raw == (
                option.value
            )


def test_a_step_appears_everywhere_or_on_the_panel():
    """`appears_in` is one enum for steps and sections; the panel edits every step,
    so a step that would appear only in the setup is refused."""
    assert _compiled({**NAME, "appears_in": "panel"}).step("name").appears_in == (
        "panel"
    )
    with pytest.raises(CompileError, match="a step appears everywhere or on the"):
        _compiled({**NAME, "appears_in": "setup"})


def test_a_card_whose_preview_waits_on_nothing_writes_true():
    """A card's preview is written the way the review's is."""
    assert _compiled(_card(title=_text("Card"), preview=True)).step("card").previewed()
    with pytest.raises(
        CompileError, match="a preview that waits on nothing is written"
    ):
        _compiled(_card(title=_text("Card"), preview={}))


@pytest.mark.parametrize(
    "field,reference",
    [
        ("transform", {"name": "normalize_link", "params": {"x": "y"}}),
        ("normalize", {"name": "lowercase", "params": {"x": "y"}}),
    ],
)
def test_a_transform_or_a_normalizer_takes_no_params(field, reference):
    """Validation, transform and normalize name a registered extension the same
    way; only a validator declares params."""
    with pytest.raises(CompileError, match=r"takes the params \[\]"):
        _compiled({**NAME, field: reference})


def test_the_schema_leaves_the_texts_the_compiler_fills_optional():
    """The reference marked a review's title and description and a card's title as
    required while the compiler fills them from the intro and the fallbacks."""
    from app.forms.form.form_yaml import FormFile

    definitions = FormFile.model_json_schema()["$defs"]
    review, card = definitions["ReviewStep"], definitions["CardStep"]

    assert {"title", "description"}.isdisjoint(review["required"])
    assert "title" not in card["required"]
    assert "default" not in review["properties"]["title"]
    assert "intro" in review["properties"]["title"]["description"]
    assert "review_description" in review["properties"]["description"]["description"]
