"""Self-tests for the golden transcript mechanism.

A golden that silently accepts a changed screen protects nothing: these pin
what enters a file, what a mismatch says, and what each allowed delta forgives.
"""
from types import SimpleNamespace

import pytest

from tests.behavioral.harness import golden
from tests.behavioral.harness.errors import ScenarioAssertionError

pytestmark = pytest.mark.behavioral


def _event(seq, kind, actor="bot", **extra):
    return {"seq": seq, "actor": actor, "kind": kind, "message": "M1",
            "step": None, **extra}


def _scenario(events):
    return SimpleNamespace(outputs=events, transcript="(transcript)")


def _button(label, action=None):
    return {"type": "button", "label": label, "action": action,
            "style": "success", "disabled": False}


# ------------------------------------------------------------------ projection

def test_component_ids_never_enter_a_golden():
    events = [_event(1, "send", components=[
        _button("Confirm", action="card_done"),
        {"type": "container", "children": [_button("Back", action="picker_back")]},
    ])]
    projected = golden.project(events)
    buttons = [projected[0]["components"][0],
               projected[0]["components"][1]["children"][0]]
    assert all("action" not in button for button in buttons)
    assert events[0]["components"][0]["action"] == "card_done", "projection copies"


def test_record_then_assert_round_trips(tmp_path):
    scenario = _scenario([_event(1, "send", embed={"title": "Oi"})])
    path = tmp_path / "form" / "setup_happy.pt-br.json"
    golden.record(scenario, path)
    golden.assert_golden(scenario, path)
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_missing_golden_says_how_to_record_it(tmp_path):
    with pytest.raises(ScenarioAssertionError) as failure:
        golden.assert_golden(_scenario([]), tmp_path / "none.json")
    assert "--update-golden" in str(failure.value)


# -------------------------------------------------------------------- mismatch

def test_mismatch_names_the_first_differing_event_in_transcript_form(tmp_path):
    path = tmp_path / "g.json"
    golden.record(_scenario([
        _event(1, "send", embed={"title": "Tudo certo?"}),
        _event(2, "edit", embed={"title": "Ativado"}),
    ]), path)
    changed = _scenario([
        _event(1, "send", embed={"title": "Tudo certo?"}),
        _event(2, "edit", embed={"title": "Ativado!"}),
    ])
    with pytest.raises(ScenarioAssertionError) as failure:
        golden.assert_golden(changed, path)
    message = str(failure.value)
    assert "first difference at event 2" in message
    assert 'expected: [02] BOT  edit M1' in message
    assert '"Ativado!"' in message
    assert "--- golden" in message and "+++ actual" in message


def test_mismatch_reports_extra_events(tmp_path):
    path = tmp_path / "g.json"
    golden.record(_scenario([_event(1, "send")]), path)
    with pytest.raises(ScenarioAssertionError) as failure:
        golden.assert_golden(_scenario([_event(1, "send"), _event(2, "delete")]), path)
    assert "expected 1 events, got 2" in str(failure.value)
    assert "only in actual: [02] BOT  delete" in str(failure.value)


# -------------------------------------------------------------- transport folding

def test_transport_is_folded_before_comparing():
    through_followup = [_event(1, "click", actor="user"), _event(2, "defer", ephemeral=True),
                        _event(3, "followup_edit", embed={"title": "x"})]
    through_response = [_event(1, "click", actor="user"),
                        _event(2, "edit", embed={"title": "x"})]
    left, right = golden.apply_deltas(through_followup, through_response, [])
    assert left == right
    assert [event["kind"] for event in right] == ["click", "edit"]


def test_a_thinking_defer_stays_visible():
    thinking = [_event(1, "defer", thinking=True, ephemeral=True), _event(2, "followup_send")]
    silent = [_event(1, "defer", ephemeral=True), _event(2, "followup_send")]
    left, right = golden.apply_deltas(thinking, silent, [])
    assert left != right


# ---------------------------------------------------------------- allowed deltas

def test_deltas_are_only_applied_when_allowed():
    expected = [_event(1, "delete", message="M1"),
                _event(2, "followup_send", message="M2")]
    actual = [_event(1, "followup_send", message="M2"),
              _event(2, "delete", message="M1")]
    left, right = golden.apply_deltas(expected, actual, [])
    assert left != right
