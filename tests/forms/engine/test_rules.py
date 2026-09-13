"""The when grammar: every leaf, every combinator, every scope."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.forms.definitions.compiler import compile_form, options_for, produced_keys
from app.forms.definitions.schema import (
    All,
    AnyOf,
    CardStep,
    CompositionStep,
    Count,
    Leaf,
    Not,
    SingleChoiceStep,
)
from app.forms.definitions.source import DiskSource
from app.forms.engine.rules import Scope, evaluate, explain

pytestmark = pytest.mark.unit

CASES = [
    (Leaf(key="mode", is_="a"), {"mode": "a"}, True),
    (Leaf(key="mode", is_="a"), {"mode": "b"}, False),
    (Leaf(key="flag", is_=True), {"flag": "True"}, True),
    (Leaf(key="flag", is_=True), {"flag": False}, False),
    (Leaf(key="mode", in_=("a", "b")), {"mode": "b"}, True),
    (Leaf(key="mode", not_in=("a",)), {"mode": "b"}, True),
    (Leaf(key="mode", not_in=("a",)), {}, True),
    (Leaf(key="gate", not_in=(False,)), {"gate": "False"}, False),
    (Leaf(key="link", matches="[/?]"), {"link": "site.com/x"}, True),
    (Leaf(key="link", matches="[/?]"), {"link": "site.com"}, False),
    (Leaf(key="chats", present=True), {"chats": ["1"]}, True),
    (Leaf(key="chats", present=True), {"chats": []}, False),
    (Leaf(key="chats", absent=True), {}, True),
    (Leaf(key="links", count=Count(min=1)), {"links": ["a"]}, True),
    (Leaf(key="links", count=Count(min=1)), {"links": []}, False),
    (Leaf(key="links", count=Count(max=2)), {"links": [1, 2, 3]}, False),
    (All(all=(Leaf(key="a", is_=1), Leaf(key="b", is_=2))), {"a": 1, "b": 2}, True),
    (All(all=(Leaf(key="a", is_=1), Leaf(key="b", is_=2))), {"a": 1, "b": 3}, False),
    (AnyOf(any=(Leaf(key="a", is_=1), Leaf(key="b", is_=2))), {"a": 0, "b": 2}, True),
    (Not(not_=Leaf(key="a", is_=1)), {"a": 1}, False),
    (None, {}, True),
]


@pytest.mark.parametrize("rule,values,expected", CASES)
def test_leaves_and_combinators(rule, values, expected):
    assert evaluate(rule, Scope(values)) is expected


def test_scopes_walk_to_the_parent_and_the_item():
    parent = Scope({"mode": "block_all"})
    scope = Scope({"link": "x"}, parent=parent, item={"link": "y"}, items_count=2)
    assert evaluate(Leaf(key="parent.mode", is_="block_all"), scope)
    assert evaluate(Leaf(key="item.link", is_="y"), scope)
    assert evaluate(Leaf(key="items.count", count=Count(min=2, max=2)), scope)
    assert not evaluate(Leaf(key="parent.missing", present=True), scope)


def test_explain_says_what_was_compared_and_how_it_went():
    text = explain(All(all=(Leaf(key="mode", is_="a"),)), Scope({"mode": "b"}))
    assert text == "all(mode is 'a' (no, got 'b'))"


def _steps_with_rules(definition):
    for step in definition.steps:
        if isinstance(step, CompositionStep):
            yield from ((s, definition.composition) for s in step.steps)
        yield step, None


def _choices(steps, key):
    options = options_for(steps, key)
    if options:
        return st.sampled_from([option.value for option in options])
    return st.sampled_from(["x", "site.com/path", None])


@pytest.mark.parametrize("form", DiskSource().list())
def test_every_conditional_step_is_reachable_by_some_answers(form):
    """Branch coverage of the definition: no step is dead by construction."""
    definition = compile_form(form, DiskSource().load(form))
    for step, composition in _steps_with_rules(definition):
        if step.when is None:
            continue
        steps = composition.steps if composition else definition.steps
        keys = [k for s in steps for k in produced_keys(s)]
        parent_keys = [k for s in definition.steps for k in produced_keys(s)]
        strategy = st.fixed_dictionaries({k: _choices(steps, k) for k in keys})
        parent_strategy = st.fixed_dictionaries(
            {k: _choices(definition.steps, k) for k in parent_keys}
        )

        reached = []

        @given(strategy, parent_strategy)
        def probe(values, parent_values):
            scope = Scope(values, parent=Scope(parent_values))
            if evaluate(step.when, scope):
                reached.append(True)

        probe()
        assert reached, f"{form}.{step.key} is never reached"


def test_card_sections_hidden_by_visible_when_are_declared_on_state_keys():
    definition = compile_form("block_links", DiskSource().load("block_links"))
    card = next(s for s in definition.steps if isinstance(s, CardStep))
    popular = next(s for s in card.sections if s.key == "allowed_links")
    assert evaluate(popular.visible_when, Scope({"mode": "block_all"}))
    assert not evaluate(popular.visible_when, Scope({"mode": "allow_all"}))


def test_single_choice_options_drive_the_typed_comparison():
    definition = compile_form("block_links", DiskSource().load("block_links"))
    gate = next(s for s in definition.steps if isinstance(s, SingleChoiceStep))
    assert [o.value for o in gate.options] == [True, False]
    composition = definition.composition
    assert evaluate(composition.when, Scope({"add_custom": True}))
    assert not evaluate(composition.when, Scope({"add_custom": "False"}))
