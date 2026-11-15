"""The when grammar: every leaf, every combinator, every scope."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.forms.form.conditions import Scope, chosen_text, evaluate, explain
from app.forms.form.form_yaml import (
    All,
    AnyOf,
    CardStep,
    ContentStep,
    Count,
    DiskSource,
    Leaf,
    ListStep,
    Not,
    SingleChoiceStep,
    Text,
    Variant,
    compile_form,
    options_for,
    produced_keys,
)

pytestmark = pytest.mark.unit


def _leaf(key, **operator):
    named = {name.rstrip("_"): value for name, value in operator.items()}
    return Leaf.model_validate({"key": key, **named})


CASES = [
    (_leaf("mode", is_="a"), {"mode": "a"}, True),
    (_leaf("mode", is_="a"), {"mode": "b"}, False),
    (_leaf("flag", is_=True), {"flag": "True"}, True),
    (_leaf("flag", is_=True), {"flag": False}, False),
    (_leaf("mode", in_=("a", "b")), {"mode": "b"}, True),
    (_leaf("mode", not_in=("a",)), {"mode": "b"}, True),
    (_leaf("mode", not_in=("a",)), {}, True),
    (_leaf("gate", not_in=(False,)), {"gate": "False"}, False),
    (_leaf("link", matches="[/?]"), {"link": "youtube.com"}, False),
    (_leaf("link", matches="[/?]"), {"link": "youtube.com/watch"}, True),
    (_leaf("link", matches="[/?]"), {"link": None}, False),
    (_leaf("link", matches="[/?]"), {"link": "site.com/x"}, True),
    (_leaf("link", matches="[/?]"), {"link": "site.com"}, False),
    (_leaf("chats", present=True), {"chats": ["1"]}, True),
    (_leaf("chats", present=True), {"chats": []}, False),
    (_leaf("chats", present=False), {}, True),
    (_leaf("links", count=Count(min=1)), {"links": ["a"]}, True),
    (_leaf("links", count=Count(min=1)), {"links": []}, False),
    (_leaf("links", count=Count(max=2)), {"links": [1, 2, 3]}, False),
    (All(all=(_leaf("a", is_=1), _leaf("b", is_=2))), {"a": 1, "b": 2}, True),
    (All(all=(_leaf("a", is_=1), _leaf("b", is_=2))), {"a": 1, "b": 3}, False),
    (AnyOf(any=(_leaf("a", is_=1), _leaf("b", is_=2))), {"a": 0, "b": 2}, True),
    (Not.model_validate({"not": _leaf("a", is_=1)}), {"a": 1}, False),
    (None, {}, True),
]


@pytest.mark.parametrize("rule,values,expected", CASES)
def test_leaves_and_combinators(rule, values, expected):
    assert evaluate(rule, Scope(values)) is expected


def test_scopes_walk_to_the_parent_and_the_item():
    parent = Scope({"mode": "block_all"})
    scope = Scope({"link": "x"}, parent=parent, item={"link": "y"}, items_count=2)
    assert evaluate(_leaf("parent.mode", is_="block_all"), scope)
    assert evaluate(_leaf("item.link", is_="y"), scope)
    assert evaluate(_leaf("items.count", count=Count(min=2, max=2)), scope)
    assert not evaluate(_leaf("parent.missing", present=True), scope)


def test_explain_says_what_was_compared_and_how_it_went():
    text = explain(All(all=(_leaf("mode", is_="a"),)), Scope({"mode": "b"}))
    assert text == "all(mode is 'a' (no, got 'b'))"


def _steps_with_rules(definition):
    for step in definition.steps:
        if isinstance(step, ListStep):
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
        if not isinstance(step, ContentStep) or step.when is None:
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


def test_card_sections_hidden_by_their_rule_are_declared_on_state_keys():
    definition = compile_form("block_links", DiskSource().load("block_links"))
    card = next(s for s in definition.steps if isinstance(s, CardStep))
    popular = next(s for s in card.sections if s.key == "allowed_links")
    assert evaluate(popular.when, Scope({"mode": "block_all"}))
    assert not evaluate(popular.when, Scope({"mode": "allow_all"}))


def test_single_choice_options_drive_the_typed_comparison():
    """Block links' add_custom gate is gone (bl-7); the contract runs on the
    fixture form with the same hidden gate before a list."""
    from tests.forms.form.gate_fixtures import gated_list

    definition = gated_list()
    gate = next(s for s in definition.steps if isinstance(s, SingleChoiceStep))
    assert [o.value for o in gate.options] == [True, False]
    composition = definition.composition
    assert evaluate(composition.when, Scope({"add_now": True}))
    assert not evaluate(composition.when, Scope({"add_now": "False"}))


def _text(word):
    return Text.model_validate({"en-us": word, "pt-br": word})


def test_the_first_variant_whose_rule_holds_is_the_text_shown():
    """Titles and descriptions picked their variant in four places, and the panel's
    group description kept the last match while the others kept the first."""
    variants = (
        Variant(when=_leaf("mode", is_="a"), title=_text("first")),
        Variant(when=_leaf("mode", in_=("a", "b")), title=_text("second")),
    )

    def shown(scope):
        return chosen_text(_text("default"), variants, "title", scope).en_us

    assert shown(Scope({"mode": "a"})) == "first"
    assert shown(Scope({"mode": "b"})) == "second"
    assert shown(Scope({"mode": "c"})) == "default"
    assert shown(None) == "default"
    assert chosen_text(None, (), "title", Scope({})) is None


def test_a_variant_replaces_only_the_texts_it_writes():
    """One variant list holds a step's titles and descriptions: the title skips a
    variant that writes only a description, and the description reads the first
    variant that writes one."""
    variants = (
        Variant(when=_leaf("mode", is_="a"), description=_text("described")),
        Variant(when=_leaf("mode", is_="a"), title=_text("titled")),
    )
    scope = Scope({"mode": "a"})

    assert chosen_text(_text("t"), variants, "title", scope).en_us == "titled"
    assert chosen_text(_text("d"), variants, "description", scope).en_us == "described"


def test_a_variant_that_replaces_nothing_is_refused():
    with pytest.raises(ValueError, match="replaces a title, a description or both"):
        Variant(when=_leaf("mode", is_="a"))
