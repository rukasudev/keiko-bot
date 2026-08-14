"""Layer-2 contract tests: every real form YAML must resolve against the
engine's registries. No Discord involved.

A failure here means a YAML references a capability that does not exist
(or a registry lost a name a YAML still uses) — exactly the class of bug
that otherwise only appears when a user reaches that step in Discord.
"""
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from app.components.modals import ModalValidations
from app.constants import FormConstants
from app.services.transforms import RESPONSE_TRANSFORMS
from app.services.utils import parse_form_yaml_to_dict
from app.views.summary_card import SECTION_TYPES, _collect_state_keys

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

ALL_FORMS = [
    "block_links",
    "default_roles",
    "notifications_twitch",
    "notifications_youtube_video",
    "reminders_birthday",
    "stream_elements_commands",
    "welcome_messages",
]

DISPATCHABLE_ACTIONS = {
    FormConstants.MODAL_ACTION_KEY,
    FormConstants.OPTIONS_ACTION_KEY,
    FormConstants.ROLES_ACTION_KEY,
    FormConstants.AVAILABLE_ROLES_ACTION_KEY,
    FormConstants.CHANNELS_ACTION_KEY,
    FormConstants.RESUME_ACTION_KEY,
    FormConstants.BUTTON_ACTION_KEY,
    FormConstants.FORM_ACTION_KEY,
    FormConstants.COMPOSITION_ACTION_KEY,
    FormConstants.MULTI_SELECT_ACTION_KEY,
    FormConstants.DESIGN_SELECT_ACTION_KEY,
    FormConstants.FILE_UPLOAD_ACTION_KEY,
    FormConstants.USER_SELECT_ACTION_KEY,
    FormConstants.MONTH_SELECT_ACTION_KEY,
    FormConstants.SUMMARY_CARD_ACTION_KEY,
    FormConstants.CONFIGURATION_CARD_ACTION_KEY,
}

# Value styles resolved by format_values_by_style (app/services/utils.py)
# plus "composition", rendered separately by get_styled_composition_values.
KNOWN_VALUE_STYLES = {
    "channel", "role", "user", "bullet", "numbered", "code",
    "boolean", "boolean-mode", "mm_dd", "composition",
}
# options[].style maps to discord.ButtonStyle via Form._get_option_styles.
KNOWN_OPTION_BUTTON_STYLES = {"primary", "secondary", "success", "danger"}


def walk_steps(steps: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """Yield every step, descending into composition sub-steps."""
    for step in steps:
        yield step
        yield from walk_steps(step.get("steps", []))


def walk_values(node: Any, path: str = "") -> Iterator[Tuple[str, Any]]:
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from walk_values(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_values(value, f"{path}[{index}]")


@pytest.fixture(params=ALL_FORMS)
def form_steps(request):
    steps = parse_form_yaml_to_dict(request.param)
    return request.param, steps


def test_yaml_loads_with_steps(form_steps):
    name, steps = form_steps
    assert steps, f"{name}.yml has no steps"


def test_first_step_is_form_and_last_is_resume(form_steps):
    name, steps = form_steps
    assert steps[0].get("action") == FormConstants.FORM_ACTION_KEY, name
    assert steps[-1].get("action") == FormConstants.RESUME_ACTION_KEY, name


def test_every_action_is_dispatchable(form_steps):
    name, steps = form_steps
    unknown = [
        (step.get("key"), step.get("action"))
        for step in walk_steps(steps)
        if step.get("action") not in DISPATCHABLE_ACTIONS
    ]
    assert not unknown, (
        f"{name}.yml uses actions with no handler in Form.get_action_by_type: {unknown}"
    )


def test_every_validation_resolves_to_a_validator(form_steps):
    name, steps = form_steps
    missing = []
    for path, node in walk_values(steps):
        validation = node.get("validation") if isinstance(node, dict) else None
        if isinstance(validation, str) and not callable(
            getattr(ModalValidations, validation, None)
        ):
            missing.append((path, validation))
    assert not missing, (
        f"{name}.yml references validations missing on ModalValidations: {missing}"
    )


def test_every_response_transform_is_registered(form_steps):
    name, steps = form_steps
    missing = [
        (step.get("key"), step["response_transform"])
        for step in walk_steps(steps)
        if step.get("response_transform")
        and step["response_transform"] not in RESPONSE_TRANSFORMS
    ]
    assert not missing, (
        f"{name}.yml references unregistered response_transform: {missing}"
    )


def test_every_style_is_known(form_steps):
    name, steps = form_steps
    unknown = []
    for step in walk_steps(steps):
        style = step.get("style")
        if style and style not in KNOWN_VALUE_STYLES:
            unknown.append((step.get("key"), style))
        for select in step.get("selects", []):
            if select.get("style") and select["style"] not in KNOWN_VALUE_STYLES:
                unknown.append((select.get("key"), select["style"]))
        for option in step.get("options", []):
            if isinstance(option, dict) and option.get("style") and \
                    option["style"] not in KNOWN_OPTION_BUTTON_STYLES:
                unknown.append((step.get("key"), f"option:{option['style']}"))
        for field in step.get("fields", []):
            if isinstance(field, dict) and field.get("style") and \
                    field["style"] not in KNOWN_VALUE_STYLES:
                unknown.append((field.get("key"), f"field:{field['style']}"))
    assert not unknown, f"{name}.yml uses unknown styles: {unknown}"


def test_locale_dicts_have_both_languages(form_steps):
    name, steps = form_steps
    broken = []
    for path, node in walk_values(steps):
        if not isinstance(node, dict):
            continue
        has_en, has_pt = "en-us" in node, "pt-br" in node
        if has_en != has_pt:
            broken.append(path)
    assert not broken, f"{name}.yml has locale dicts missing en-us or pt-br: {broken}"


def _response_keys_of(step) -> set:
    """Keys a step contributes to Form.responses: its own key, nested select
    keys, and (for cards) section state keys and field keys."""
    keys = {step.get("key")}
    for select in step.get("selects", []) or []:
        keys.add(select.get("key"))
    for section in step.get("sections", []) or []:
        keys.update(value for value in (section.get("state") or {}).values() if value)
    for card_field in step.get("fields", []) or []:
        if isinstance(card_field, dict):
            keys.add(card_field.get("key"))
    keys.discard(None)
    return keys


def test_conditions_reference_earlier_step_keys(form_steps):
    name, steps = form_steps
    seen = set()
    broken = []
    for step in walk_steps(steps):
        condition = step.get("condition")
        if condition and condition.get("key") not in seen:
            broken.append((step.get("key"), condition.get("key")))
        seen.update(_response_keys_of(step))
    assert not broken, (
        f"{name}.yml has conditions referencing keys not defined earlier: {broken}"
    )


def test_card_visible_when_references_card_state_keys(form_steps):
    name, steps = form_steps
    broken = []
    for step in walk_steps(steps):
        sections = step.get("sections", []) or []
        if not sections:
            continue
        state_keys = set(_collect_state_keys(sections))
        for section in sections:
            rule = section.get("visible-when")
            if rule and rule.get("key") not in state_keys:
                broken.append((section.get("key"), rule.get("key")))
    assert not broken, (
        f"{name}.yml has visible-when rules referencing unknown state keys: {broken}"
    )


def test_block_links_quick_picks_match_domain_table():
    """The card's popular-website options must stay in sync with the alias
    table the matcher expands (constants.BLOCK_LINKS_QUICK_PICK_DOMAINS)."""
    from app.constants import BLOCK_LINKS_QUICK_PICK_DOMAINS

    steps = parse_form_yaml_to_dict("block_links")
    card = next(s for s in steps if s.get("action") == "configuration_card")
    section = next(s for s in card["sections"] if s.get("key") == "allowed_links")
    option_values = {str(o.get("value")) for o in section.get("options", [])}
    assert option_values == set(BLOCK_LINKS_QUICK_PICK_DOMAINS.keys())


def test_card_sections_use_registered_types_and_valid_state_keys(form_steps):
    name, steps = form_steps
    problems = []
    for step in walk_steps(steps):
        if step.get("action") not in (
            FormConstants.SUMMARY_CARD_ACTION_KEY,
            FormConstants.CONFIGURATION_CARD_ACTION_KEY,
        ):
            continue
        sections = step.get("sections", [])
        for section in sections:
            if section.get("type") not in SECTION_TYPES:
                problems.append(
                    (step.get("key"), f"section type {section.get('type')!r}")
                )
        state_keys = set(_collect_state_keys(sections))
        field_keys = {
            field.get("key") for field in step.get("fields", [])
            if isinstance(field, dict)
        }
        valid_keys = state_keys | field_keys
        for key in step.get("required", []):
            if key not in valid_keys:
                problems.append((step.get("key"), f"required key {key!r} unknown"))
        for key in step.get("defaults", {}):
            if key not in valid_keys:
                problems.append((step.get("key"), f"default key {key!r} unknown"))
    assert not problems, f"{name}.yml card issues: {problems}"


def _split_option_label(option: Dict[str, Any], locale: str) -> Tuple[str, str]:
    """(leading emoji, remaining text) of a localized option label."""
    label = option.get("label")
    text = ((label.get(locale) if isinstance(label, dict) else label) or "").strip()
    head, _, tail = text.partition(" ")
    if tail and head[:1] and not head[:1].isalnum():
        return head, tail.strip()
    return "", text


def test_option_explanations_are_written_as_own_lines(form_steps):
    """Keiko explains choices as a scannable list, never as a paragraph.

    When a step's description spells out its own option labels, each mention
    opens its own line: led by the option's emoji when the label carries one,
    and by a "- " bullet when it does not. A bullet in front of an emoji reads
    as visual noise, and prose hiding the options reads as a wall of text.

    Pinned after the block_links match-type step shipped both explanations
    glued inside one dense paragraph, then extended when the mode picker
    shipped bullet-plus-emoji lines.
    """
    name, steps = form_steps
    problems = []
    for step in walk_steps(steps):
        explainers = []
        if step.get("action") == FormConstants.OPTIONS_ACTION_KEY:
            explainers.append((step.get("key"), step.get("description"), step.get("options")))
        for section in step.get("sections", []) or []:
            explainers.append(
                (section.get("key"), section.get("picker-description"), section.get("options"))
            )

        for key, description_dict, options in explainers:
            for locale in ("en-us", "pt-br"):
                description = (description_dict or {}).get(locale) or ""
                for option in options or []:
                    emoji, core = _split_option_label(option, locale)
                    if not core or core not in description:
                        continue
                    openers = ("- ",) if not emoji else ("- ", emoji)
                    for line in description.split("\n"):
                        if core in line and not line.lstrip().startswith(openers):
                            problems.append((key, locale, core))
                        if emoji and line.lstrip().startswith(f"- {emoji}"):
                            problems.append((key, locale, f"bullet before {emoji}"))
    assert not problems, (
        f"{name}.yml must explain each option on its own line, led by its "
        f"emoji (or a '- ' bullet when there is none): {problems}"
    )


def test_description_variants_reference_keys_produced_earlier(form_steps):
    """`description-when:` lets one step word itself differently depending on
    an earlier answer. A variant pointing at a key no step produces would
    silently never fire (or always fire), so the reference is pinned here."""
    name, steps = form_steps
    seen = set()
    broken = []
    for step in walk_steps(steps):
        for variant in step.get("description-when", []) or []:
            key = (variant.get("condition") or {}).get("key")
            if key not in seen:
                broken.append((step.get("key"), key))
        seen.update(_response_keys_of(step))
    assert not broken, (
        f"{name}.yml has description-when variants referencing keys not "
        f"defined earlier: {broken}"
    )
