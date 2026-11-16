"""The `when` grammar a form and a copy file write, its evaluator and the overrides
it picks: pure, no Discord and nothing of the app."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

EMPTY: tuple[Any, ...] = (None, "", [], (), {})

Scalar = Union[str, bool, int]


def same_value(left: Any, right: Any) -> bool:
    """Equality across the spellings a value takes in YAML and in answers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return str(left).lower() == str(right).lower()
    return str(left) == str(right)


class Rule(BaseModel):
    """Base of every model of the grammar: frozen, strict about unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Count(Rule):
    """Bounds on how many values an answer holds."""

    min: int | None = Field(
        None,
        description="The fewest values the rule accepts.",
        json_schema_extra={"role": "conditions"},
    )
    max: int | None = Field(
        None,
        description="The most values the rule accepts.",
        json_schema_extra={"role": "conditions"},
    )


class Leaf(Rule):
    """One test on one key of the scope chain, with exactly one operator."""

    key: str = Field(
        description="The value the rule tests: in a form, an answer key, behind "
        "`parent.` or `item.` for an outer session or a list item, or "
        "`items.count`; in a copy file, a fact by its address "
        "(`<owner>.facts.<name>`).",
        examples=[
            "mode",
            "parent.mode",
            "items.count",
            "global.facts.feature_state",
        ],
        json_schema_extra={"role": "data"},
    )
    is_: Scalar | None = Field(
        None,
        alias="is",
        description="Holds while the value equals this one.",
        json_schema_extra={"role": "conditions"},
    )
    in_: tuple[Scalar, ...] | None = Field(
        None,
        alias="in",
        description="Holds while the value is one of these.",
        json_schema_extra={"role": "conditions"},
    )
    not_in: tuple[Scalar, ...] | None = Field(
        None,
        description="Holds while the value is none of these.",
        json_schema_extra={"role": "conditions"},
    )
    matches: str | None = Field(
        None,
        description="Holds while the value matches this regular expression.",
        examples=["[/?]"],
        json_schema_extra={"role": "conditions"},
    )
    present: bool | None = Field(
        None,
        description="Holds while the value is filled, or while it is empty when "
        "`false`.",
        json_schema_extra={"role": "conditions"},
    )
    count: Count | None = Field(
        None,
        description="Holds while the number of values is within these bounds.",
        json_schema_extra={"role": "conditions"},
    )

    @model_validator(mode="after")
    def _one_operator(self) -> Leaf:
        named = [name for name, value in self.operators().items() if value is not None]
        if len(named) != 1:
            raise ValueError(
                f"a rule on {self.key!r} names exactly one operator, not {named}"
            )
        return self

    def operators(self) -> dict[str, Any]:
        """Each operator the grammar knows, by the name a file writes it under."""
        return {
            "is": self.is_,
            "in": self.in_,
            "not_in": self.not_in,
            "matches": self.matches,
            "present": self.present,
            "count": self.count,
        }

    def compared(self) -> list[Scalar]:
        """The values the rule compares its key against, under is, in and not_in."""
        values = list(self.in_ or ()) + list(self.not_in or ())
        if self.is_ is not None:
            values.append(self.is_)
        return values


class All(Rule):
    """True when every rule holds."""

    all: tuple[When, ...] = Field(
        description="The rules that must all hold.",
        json_schema_extra={"role": "conditions"},
    )


class AnyOf(Rule):
    """True when at least one rule holds."""

    any: tuple[When, ...] = Field(
        description="The rules one of which must hold.",
        json_schema_extra={"role": "conditions"},
    )


class Not(Rule):
    """True when the rule does not hold."""

    not_: When = Field(
        alias="not",
        description="The rule that must not hold.",
        json_schema_extra={"role": "conditions"},
    )


When = Union[Leaf, All, AnyOf, Not]


class Override(Rule):
    """Base of an item of `overrides`: a rule and the fields it replaces while the rule
    holds; the first item whose rule holds wins and replaces only what it writes."""

    when: When = Field(
        description="The rule under which this item's fields replace the block's.",
        json_schema_extra={"role": "conditions"},
    )

    @model_validator(mode="after")
    def _replaces_a_field(self) -> Override:
        if not self.written():
            raise ValueError("an override replaces at least one field besides `when`")
        return self

    def written(self) -> dict[str, Any]:
        """The fields this item replaces, by name, the ones it writes."""
        return {
            name: value
            for name, value in self.__dict__.items()
            if name != "when" and value is not None
        }


All.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()
Override.model_rebuild()

Chosen = TypeVar("Chosen", bound=Override)


@dataclass(frozen=True)
class Scope:
    """The values a rule may name: this session's, the parent's, the item's; for copy,
    the facts the code passed, by address."""

    values: Mapping[str, Any]
    parent: Scope | None = None
    item: Mapping[str, Any] | None = None
    items_count: int | None = None

    def resolve(self, key: str) -> Any:
        """The value behind `key`, walking `parent.` and `item.` prefixes."""
        if key.startswith("parent."):
            return self.parent.resolve(key[len("parent.") :]) if self.parent else None
        if key.startswith("item."):
            return (self.item or {}).get(key[len("item.") :])
        if key == "items.count":
            return self.items_count
        return self.values.get(key)


def count_of(value: Any) -> int:
    """How many values an answer holds: none when empty, one for a single value."""
    if value in EMPTY:
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return len(value) if isinstance(value, (list, tuple, set, dict)) else 1


def leaves(rule: When | None) -> list[Leaf]:
    """Every leaf of `rule`, in the order written."""
    if rule is None:
        return []
    if isinstance(rule, Leaf):
        return [rule]
    if isinstance(rule, All):
        return [leaf for child in rule.all for leaf in leaves(child)]
    if isinstance(rule, AnyOf):
        return [leaf for child in rule.any for leaf in leaves(child)]
    return leaves(rule.not_)


def _leaf(leaf: Leaf, value: Any) -> bool:
    if leaf.is_ is not None:
        return same_value(value, leaf.is_)
    if leaf.in_ is not None:
        return any(same_value(value, option) for option in leaf.in_)
    if leaf.not_in is not None:
        return not any(same_value(value, option) for option in leaf.not_in)
    if leaf.matches is not None:
        return re.search(leaf.matches, str(value or "")) is not None
    if leaf.present is not None:
        return (value not in EMPTY) == leaf.present
    if leaf.count is not None:
        size = count_of(value)
        low = leaf.count.min if leaf.count.min is not None else size
        high = leaf.count.max if leaf.count.max is not None else size

        return low <= size <= high
    return True


def evaluate(rule: When | None, scope: Scope) -> bool:
    """True when `rule` holds in `scope`; a missing rule always holds."""
    if rule is None:
        return True
    if isinstance(rule, Leaf):
        return _leaf(rule, scope.resolve(rule.key))
    if isinstance(rule, All):
        return all(evaluate(child, scope) for child in rule.all)
    if isinstance(rule, AnyOf):
        return any(evaluate(child, scope) for child in rule.any)
    return not evaluate(rule.not_, scope)


def winner(overrides: Sequence[Chosen], scope: Scope | None) -> Chosen | None:
    """The first override whose rule holds in `scope`; None when none holds or there
    is no scope to read."""
    if scope is None:
        return None
    return next((item for item in overrides if evaluate(item.when, scope)), None)


def _describe_leaf(leaf: Leaf) -> str:
    if leaf.is_ is not None:
        return f"{leaf.key} is {leaf.is_!r}"
    if leaf.in_ is not None:
        return f"{leaf.key} in {list(leaf.in_)!r}"
    if leaf.not_in is not None:
        return f"{leaf.key} not in {list(leaf.not_in)!r}"
    if leaf.matches is not None:
        return f"{leaf.key} matches {leaf.matches!r}"
    if leaf.present is not None:
        return f"{leaf.key} {'present' if leaf.present else 'not present'}"
    return f"{leaf.key} count in {leaf.count}"


def explain(rule: When | None, scope: Scope) -> str:
    """The rule and how it went, for the journey: `mode is 'custom' (yes)`."""
    if rule is None:
        return "no rule"
    if isinstance(rule, Leaf):
        value = scope.resolve(rule.key)
        outcome = "yes" if _leaf(rule, value) else "no"
        return f"{_describe_leaf(rule)} ({outcome}, got {value!r})"
    if isinstance(rule, All):
        return "all(" + "; ".join(explain(child, scope) for child in rule.all) + ")"
    if isinstance(rule, AnyOf):
        return "any(" + "; ".join(explain(child, scope) for child in rule.any) + ")"
    return "not(" + explain(rule.not_, scope) + ")"
