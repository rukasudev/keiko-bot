"""The `when` evaluator: one grammar, explicit scopes, an explanation per rule."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.settings.form.form_yaml import All, AnyOf, Leaf, When, same_value

EMPTY: tuple[Any, ...] = (None, "", [], (), {})


@dataclass(frozen=True)
class Scope:
    """The values a rule may name: this session's, the parent's, the item's."""

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


def _count(value: Any) -> int:
    if value in EMPTY:
        return 0
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return len(value) if isinstance(value, (list, tuple, set, dict)) else 1


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
    if leaf.absent is not None:
        return (value in EMPTY) == leaf.absent
    if leaf.count is not None:
        size = _count(value)
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
    if leaf.absent is not None:
        return f"{leaf.key} {'absent' if leaf.absent else 'not absent'}"
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
