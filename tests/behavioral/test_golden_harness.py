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


# ---------------------------------------------------------------- allowed deltas

def test_ux1_forgives_a_self_deleting_notice_only():
    expected = [_event(1, "click", actor="user"), _event(2, "edit")]
    actual = [_event(1, "click", actor="user"),
              _event(2, "followup_send", ephemeral=True, delete_after=5.0),
              _event(3, "edit")]
    left, right = golden.apply_deltas(expected, actual, ["ux-1"])
    assert left == right
    plain = [_event(1, "click", actor="user"),
             _event(2, "followup_send", ephemeral=True), _event(3, "edit")]
    left, right = golden.apply_deltas(expected, plain, ["ux-1"])
    assert left != right, "a followup without delete_after is a real difference"


def test_ux2_compares_nothing_after_a_recorded_timeout():
    expected = [_event(1, "send"), _event(2, "timeout", actor="user")]
    actual = [_event(1, "send"), _event(2, "timeout", actor="user"),
              _event(3, "edit_original", components=[])]
    left, right = golden.apply_deltas(expected, actual, ["ux-2"])
    assert left == right == [_event(1, "send")]


def test_ux3_pairs_a_public_error_with_an_ephemeral_error_embed_in_place():
    expected = [_event(1, "channel_send", content="Escolha uma opção")]
    actual = [_event(1, "followup_send", ephemeral=True,
                     embed={"title": "Ops", "color": golden.ERROR_COLOR})]
    left, right = golden.apply_deltas(expected, actual, ["ux-3"])
    assert left == right
    unrelated = [_event(1, "followup_send", ephemeral=True,
                        embed={"title": "Ops", "color": 0x4F97F9})]
    left, right = golden.apply_deltas(expected, unrelated, ["ux-3"])
    assert left != right


def test_ux4_accepts_either_replacement_order_and_renumbers():
    expected = [_event(1, "delete", message="M1"),
                _event(2, "followup_send", message="M2")]
    actual = [_event(1, "followup_send", message="M2"),
              _event(2, "delete", message="M1")]
    left, right = golden.apply_deltas(expected, actual, ["ux-4"])
    assert left == right
    assert [event["seq"] for event in right] == [1, 2]


def test_ux5_forgives_deleting_the_card_after_the_final_message():
    expected = [_event(1, "followup_send", message="M2")]
    actual = [_event(1, "followup_send", message="M2"), _event(2, "delete", message="M1")]
    left, right = golden.apply_deltas(expected, actual, ["ux-5"])
    assert left == right
    stray = [_event(1, "delete", message="M1"), _event(2, "followup_send", message="M2")]
    left, right = golden.apply_deltas(expected, stray, ["ux-5"])
    assert left != right, "a delete that does not follow the final message stays"


def test_deltas_are_only_applied_when_allowed():
    expected = [_event(1, "delete", message="M1"),
                _event(2, "followup_send", message="M2")]
    actual = [_event(1, "followup_send", message="M2"),
              _event(2, "delete", message="M1")]
    left, right = golden.apply_deltas(expected, actual, [])
    assert left != right
