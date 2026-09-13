"""From the YAML on disk to a frozen `FormDefinition`.

The compiler accepts the step vocabulary the seven forms were written in
(`action: modal`, `condition:`, `template-vars:`) and translates it to the
typed kinds and the `when` grammar, reporting every legacy spelling as a
warning. What the models cannot say, the semantic pass checks: every `when`
names a key produced earlier in the same or an outer scope, every `in`/`is`
value is one of the producing step's options, every registry name resolves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.forms.definitions.schema import (
    All,
    AnyOf,
    ButtonOptionsSection,
    CardStep,
    CompositionStep,
    FileUploadSection,
    FormDefinition,
    Leaf,
    ModalInputSection,
    MultiPickStep,
    MultiSelectSection,
    Option,
    SingleChoiceStep,
    Step,
    TextStep,
    TitleContentSection,
    ValueSelectSection,
    When,
)
from app.forms.engine.rules import same_value
from app.forms.extensions.transforms import TRANSFORMS
from app.forms.extensions.validators import VALIDATORS

KIND_BY_ACTION: dict[str, str] = {
    "form": "intro",
    "modal": "text",
    "file_upload": "text",
    "options": "single_choice",
    "design_select": "single_choice",
    "channels": "channel_pick",
    "roles": "role_pick",
    "available_roles": "role_pick",
    "user_select": "user_pick",
    "multi_select": "multi_pick",
    "configuration_card": "card",
    "composition": "composition",
    "button": "info",
    "resume": "review",
}

CV2_MAX_COMPONENTS = 40


class CompileError(Exception):
    """A definition that cannot become a form, with where and why."""

    def __init__(self, form: str, step_key: str | None, field: str, reason: str):
        self.form = form
        self.step_key = step_key
        self.field = field
        self.reason = reason
        where = f"{form}.{step_key}" if step_key else form
        super().__init__(f"{where}: {field}: {reason}")


@dataclass(frozen=True)
class Compiled:
    """A compiled definition and the deprecation warnings raised on the way."""

    definition: FormDefinition
    warnings: tuple[str, ...]


def compile_form(key: str, raw: Mapping[str, Any]) -> FormDefinition:
    """The definition of the form `key`, or a `CompileError`."""
    return compile_with_warnings(key, raw).definition


def compile_with_warnings(key: str, raw: Mapping[str, Any]) -> Compiled:
    """Translate, validate and check `raw`; keep the warnings alongside."""
    warnings: list[str] = []
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise CompileError(key, None, "steps", "a form declares a non-empty steps list")
    translated = [_translate_step(key, step, warnings) for step in steps]
    _qualify_scopes(key, translated, outer=(), warnings=warnings)
    payload = {"key": key, "version": int(raw.get("version", 1)), "steps": translated}
    try:
        definition = FormDefinition.model_validate(payload)
    except ValidationError as error:
        raise _as_compile_error(key, error) from None
    _check_shape(definition)
    _check_scope(definition, definition.steps, outer=())
    _check_registries(definition)
    _check_limits(definition)
    return Compiled(definition, tuple(warnings))


def _as_compile_error(form: str, error: ValidationError) -> CompileError:
    first = error.errors()[0]
    location = [str(part) for part in first["loc"]]
    step_key = None
    if len(location) >= 2 and location[0] == "steps" and location[1].isdigit():
        step_key = f"steps[{location[1]}]"
    field = ".".join(location) or "definition"
    reason = first["msg"]
    if first["type"] == "extra_forbidden":
        reason = "unknown field"
    return CompileError(form, step_key, field, reason)


def _legacy_condition(
    form: str, condition: Mapping[str, Any], warnings: list[str]
) -> Any:
    """`condition: {key, not_in, matches}` as a `when` rule."""
    key = condition.get("key")
    leaves: list[dict[str, Any]] = []
    if "not_in" in condition:
        leaves.append({"key": key, "not_in": list(condition["not_in"])})
    if "matches" in condition:
        leaves.append({"key": key, "matches": condition["matches"]})
    unknown = set(condition) - {"key", "not_in", "matches"}
    if unknown or key is None or not leaves:
        raise CompileError(
            form, None, "condition", f"unsupported condition {condition}"
        )
    warnings.append(f"{form}: `condition` on {key!r} is deprecated, use `when`")
    return leaves[0] if len(leaves) == 1 else {"all": leaves}


def _rule(form: str, raw: Mapping[str, Any], field: str, warnings: list[str]) -> Any:
    if "when" in raw:
        return raw["when"]
    if field in raw:
        return _legacy_condition(form, raw[field], warnings)
    return None


def _translate_common(
    form: str, raw: Mapping[str, Any], warnings: list[str]
) -> dict[str, Any]:
    out = {k: v for k, v in raw.items() if k not in ("action", "condition", "when")}
    rule = _rule(form, raw, "condition", warnings)
    if rule is not None:
        out["when"] = rule
    variants = out.pop("description-when", None)
    if variants:
        out["description_when"] = [
            {
                "when": _rule(form, variant, "condition", warnings),
                "text": {k: v for k, v in variant.items() if k in ("en-us", "pt-br")},
            }
            for variant in variants
        ]
    if "response_transform" in out:
        out["transform"] = out.pop("response_transform")
    return out


def _translate_step(
    form: str, raw: Mapping[str, Any], warnings: list[str]
) -> dict[str, Any]:
    action = raw.get("action")
    kind = KIND_BY_ACTION.get(str(action))
    if kind is None:
        raise CompileError(form, raw.get("key"), "action", f"unknown action {action!r}")
    out = _translate_common(form, raw, warnings)
    out["kind"] = kind
    if action == "file_upload":
        out["input"] = "file"
    if action == "available_roles":
        out["available"] = True
    if action in ("channels", "roles", "available_roles"):
        _translate_pick(form, raw, out)
    if action == "configuration_card":
        _translate_card(form, out, warnings)
    if action == "composition":
        _translate_composition(form, out, warnings)
    return out


def _translate_pick(form: str, raw: Mapping[str, Any], out: dict[str, Any]) -> None:
    if not raw.get("select", False):
        raise CompileError(
            form, raw.get("key"), "select", "only native selects are supported"
        )
    out.pop("select", None)


def _translate_card(form: str, out: dict[str, Any], warnings: list[str]) -> None:
    if "required" in out:
        out["required_keys"] = list(out.pop("required") or [])
    if "template-vars" in out:
        out["context"] = {
            name: _translate_template_var(form, out.get("key"), spec)
            for name, spec in (out.pop("template-vars") or {}).items()
        }
    header = out.get("header")
    if isinstance(header, Mapping):
        out["header"] = _translate_header(header)
    out["sections"] = [
        _translate_section(form, section, warnings)
        for section in out.get("sections", [])
    ]


def _translate_template_var(
    form: str, step_key: Any, spec: Mapping[str, Any]
) -> dict[str, Any]:
    source = spec.get("from", "response")
    if source == "response":
        return {"answer": spec.get("key"), "format": spec.get("format")}
    if source == "interaction" and spec.get("attr") == "guild.name":
        return {"context": "server_name"}
    raise CompileError(form, step_key, "template-vars", f"unsupported source {spec}")


def _translate_header(header: Mapping[str, Any]) -> dict[str, Any]:
    out = {k.replace("-", "_"): v for k, v in header.items() if k != "lines"}
    out["lines"] = [
        {
            "emoji": line.get("emoji", ""),
            "label": line.get("label"),
            "value_key": line.get("value-key"),
            "value_format": line.get("value-format"),
        }
        for line in header.get("lines", []) or []
    ]
    return out


def _translate_section(
    form: str, section: Mapping[str, Any], warnings: list[str]
) -> dict[str, Any]:
    out = {k.replace("-", "_"): v for k, v in section.items() if k != "visible-when"}
    rule = _rule(form, section, "visible-when", warnings)
    if rule is not None:
        out["visible_when"] = rule
    modal = out.get("modal")
    if isinstance(modal, Mapping):
        out["modal"] = {k.replace("-", "_"): v for k, v in modal.items()}
    return out


def _translate_composition(form: str, out: dict[str, Any], warnings: list[str]) -> None:
    if "max" not in out:
        raise CompileError(form, out.get("key"), "max", "a composition declares max")
    out["items"] = {"max": out.pop("max"), "unique_by": out.pop("unique_by", None)}
    out["steps"] = [
        _translate_step(form, step, warnings) for step in out.get("steps", [])
    ]


def _raw_produced_keys(step: Mapping[str, Any]) -> list[str]:
    keys = [str(step.get("key"))]
    keys += [str(select.get("key")) for select in step.get("selects", []) or []]
    for section in step.get("sections", []) or []:
        keys += [str(v) for v in (section.get("state") or {}).values() if v]
    if step.get("kind") == "card":
        keys += [str(field.get("key")) for field in step.get("fields", []) or []]
    return keys


def _raw_leaves(rule: Any) -> list[dict[str, Any]]:
    if not isinstance(rule, Mapping):
        return []
    if "key" in rule:
        return [rule] if isinstance(rule, dict) else [dict(rule)]
    for combinator in ("all", "any"):
        if combinator in rule:
            return [leaf for child in rule[combinator] for leaf in _raw_leaves(child)]
    return _raw_leaves(rule.get("not"))


def _qualify_leaf(
    form: str,
    step_key: Any,
    leaf: dict[str, Any],
    scopes: Sequence[Sequence[str]],
    warnings: list[str],
) -> None:
    """A bare key only produced by an outer session becomes `parent.key`."""
    key = str(leaf.get("key"))
    if key.startswith(("parent.", "item.")) or key == "items.count":
        return
    if key in scopes[-1]:
        return
    for depth, produced in enumerate(reversed(scopes[:-1]), start=1):
        if key in produced:
            leaf["key"] = "parent." * depth + key
            warnings.append(
                f"{form}.{step_key}: {key!r} resolved through the parent session,"
                f" spell it {leaf['key']!r}"
            )
            return


def _qualify_scopes(
    form: str,
    steps: Sequence[dict[str, Any]],
    outer: Sequence[Sequence[str]],
    warnings: list[str],
) -> None:
    produced: list[str] = []
    for step in steps:
        scopes = [*outer, produced]
        rules = [step.get("when")] + [
            variant.get("when") for variant in step.get("description_when", [])
        ]
        for rule in rules:
            for leaf in _raw_leaves(rule):
                _qualify_leaf(form, step.get("key"), leaf, scopes, warnings)
        if step.get("kind") == "composition":
            _qualify_scopes(form, step.get("steps", []), [*outer, produced], warnings)
        produced.extend(_raw_produced_keys(step))


def _check_shape(definition: FormDefinition) -> None:
    steps = definition.steps
    if steps[0].kind != "intro":
        raise CompileError(
            definition.key, steps[0].key, "kind", "the first step is intro"
        )
    if steps[-1].kind != "review":
        raise CompileError(
            definition.key, steps[-1].key, "kind", "the last step is review"
        )
    seen: set[str] = set()
    for step in steps:
        if step.key in seen:
            raise CompileError(definition.key, step.key, "key", "duplicated step key")
        seen.add(step.key)


def produced_keys(step: Step) -> tuple[str, ...]:
    """The answer keys a step writes: its own plus its selects and card keys."""
    keys = [step.key]
    if isinstance(step, MultiPickStep):
        keys += [select.key for select in step.selects]
    if isinstance(step, CardStep):
        keys += [key for key in step.state_keys() if key not in keys]
        keys += [field.key for field in step.fields if field.key not in keys]
    return tuple(keys)


def options_for(steps: Sequence[Step], key: str) -> tuple[Option, ...] | None:
    """The declared options behind `key`, when the producing step has them."""
    for step in steps:
        if isinstance(step, SingleChoiceStep) and step.key == key and step.options:
            return step.options
        if isinstance(step, CardStep):
            for section in step.sections:
                with_options = (
                    ValueSelectSection,
                    ButtonOptionsSection,
                    MultiSelectSection,
                )
                if isinstance(section, with_options) and section.state.value == key:
                    return tuple(section.options)
    return None


def _leaves(rule: When | None) -> list[Leaf]:
    if rule is None:
        return []
    if isinstance(rule, Leaf):
        return [rule]
    if isinstance(rule, All):
        return [leaf for child in rule.all for leaf in _leaves(child)]
    if isinstance(rule, AnyOf):
        return [leaf for child in rule.any for leaf in _leaves(child)]
    return _leaves(rule.not_)


def _check_leaf(
    form: str,
    step_key: str,
    leaf: Leaf,
    scopes: Sequence[tuple[tuple[str, ...], Sequence[Step]]],
) -> None:
    key = leaf.key
    depth = 0
    while key.startswith("parent."):
        key = key[len("parent.") :]
        depth += 1
    if key.startswith("item.") or key == "items.count":
        return
    if depth >= len(scopes):
        raise CompileError(form, step_key, "when", f"{leaf.key!r} has no such scope")
    produced, steps = scopes[-1 - depth]
    if key not in produced:
        raise CompileError(
            form, step_key, "when", f"{leaf.key!r} is not produced earlier"
        )
    options = options_for(steps, key)
    values = list(leaf.in_ or ()) + list(leaf.not_in or ())
    if leaf.is_ is not None:
        values.append(leaf.is_)
    if options is None:
        return
    for value in values:
        if not any(same_value(value, option.value) for option in options):
            raise CompileError(
                form, step_key, "when", f"{value!r} is not an option of {key!r}"
            )


def _check_scope(
    definition: FormDefinition,
    steps: Sequence[Step],
    outer: Sequence[tuple[tuple[str, ...], Sequence[Step]]],
) -> None:
    produced: list[str] = []
    for step in steps:
        scopes = list(outer) + [(tuple(produced), steps)]
        for leaf in _leaves(step.when):
            _check_leaf(definition.key, step.key, leaf, scopes)
        for variant in step.description_when:
            for leaf in _leaves(variant.when):
                _check_leaf(definition.key, step.key, leaf, scopes)
        if isinstance(step, CardStep):
            _check_card(definition, step)
        if isinstance(step, CompositionStep):
            _check_scope(
                definition, step.steps, list(outer) + [(tuple(produced), steps)]
            )
        produced.extend(produced_keys(step))


def _check_card(definition: FormDefinition, card: CardStep) -> None:
    state_keys = set(card.state_keys())
    for section in card.sections:
        for leaf in _leaves(section.visible_when):
            if leaf.key not in state_keys:
                raise CompileError(
                    definition.key, card.key, "visible-when", f"{leaf.key!r} unknown"
                )
    for key in card.required_keys:
        if key not in state_keys and key not in {f.key for f in card.fields}:
            raise CompileError(definition.key, card.key, "required", f"{key!r} unknown")


def _walk(steps: Sequence[Step]) -> list[Step]:
    found: list[Step] = []
    for step in steps:
        found.append(step)
        if isinstance(step, CompositionStep):
            found.extend(_walk(step.steps))
    return found


def _check_registries(definition: FormDefinition) -> None:
    for step in _walk(definition.steps):
        names = list(_card_validations(step))
        if isinstance(step, TextStep) and step.validation:
            names.append(step.validation)
        for name in names:
            if name not in VALIDATORS:
                raise CompileError(
                    definition.key,
                    step.key,
                    "validation",
                    f"unknown validator {name!r}",
                )
        transform = step.transform if isinstance(step, (TextStep, CardStep)) else None
        if transform and transform not in TRANSFORMS:
            raise CompileError(
                definition.key,
                step.key,
                "transform",
                f"unknown transform {transform!r}",
            )


def _card_validations(step: Step) -> list[str]:
    if not isinstance(step, CardStep):
        return []
    names: list[str] = []
    for section in step.sections:
        with_modal = (TitleContentSection, FileUploadSection, ModalInputSection)
        if isinstance(section, with_modal) and section.modal.validation:
            names.append(section.modal.validation)
        if isinstance(section, ValueSelectSection):
            names += [rule.validation for rule in section.reset_on_change]
    return names


def _check_limits(definition: FormDefinition) -> None:
    for step in _walk(definition.steps):
        if not isinstance(step, CardStep):
            continue
        estimate = 1 + 3 + len(step.sections) * 5 + 1 + 2
        if estimate > CV2_MAX_COMPONENTS:
            raise CompileError(
                definition.key,
                step.key,
                "sections",
                f"about {estimate} components, Discord allows {CV2_MAX_COMPONENTS}",
            )
