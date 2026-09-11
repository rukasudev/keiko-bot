"""Every command is instrumented, and none of them was instrumented.

The architectural claim of this whole feature is that product analytics lives
in the engine, not in commands. This suite is what makes that claim falsifiable:
it opens every YAML-driven command through the real engine and requires the
same events to come out of each one, without a line of per-command code.

If someone adds a command and it is not measured, one of these fails.
"""
import ast
import os

import pytest

from app.constants import Commands as constants
from app.services import analytics, analytics_reports, config_state

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("analytics")]

APP_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "app")

ENGINE_FILES = [
    "services/analytics.py",
    "services/analytics_sink.py",
    "services/cogs.py",
    "views/form.py",
    "views/form_state.py",
    "views/confirm_action.py",
    "components/modals.py",
    "views/summary_card.py",
    "services/moderations.py",
]


@pytest.mark.parametrize("command_key", constants.COMMANDS_LIST)
async def test_opening_any_command_reports_the_same_two_events(
    scenario_factory, analytics_events, command_key
):
    """Every feature reports that a setup started and which step it showed —
    from `send_command_form_message` and the form decorator, nowhere else."""
    scenario = await scenario_factory(locale="pt-br").start(command_key)
    await scenario.confirm()          # leaves the intro embed, renders step one

    opened = [e for e in analytics_events if e["event"] == "feature.setup_opened"]
    steps = [e for e in analytics_events if e["event"] == "setup.step_viewed"]

    assert len(opened) == 1, f"{command_key} did not report its setup opening"
    assert opened[0]["feature"] == command_key
    assert opened[0]["session_id"]
    assert opened[0]["source"] == "slash"

    assert steps, f"{command_key} did not report any step"
    for step in steps:
        assert step["feature"] == command_key
        assert step["session_id"] == opened[0]["session_id"], (
            "every event of one attempt must share the session id"
        )
        assert step["props"]["step_key"]
        assert step["props"]["step_action"]

    await scenario.finish()


@pytest.mark.parametrize("command_key", constants.COMMANDS_LIST)
async def test_cancelling_any_command_reports_a_discard_and_no_completion(
    scenario_factory, analytics_events, command_key
):
    scenario = await scenario_factory(locale="pt-br").start(command_key)
    await scenario.click("cancel")
    await scenario.click("Descartar tudo")

    discarded = [e for e in analytics_events if e["event"] == "setup.discarded"]
    assert discarded, f"{command_key} did not report its discard"
    assert discarded[0]["feature"] == command_key
    assert not [e for e in analytics_events if e["event"] == "setup.completed"]

    await scenario.finish()


@pytest.mark.parametrize("command_key", constants.COMMANDS_LIST)
async def test_no_command_ever_leaks_what_was_typed(
    scenario_factory, analytics_events, command_key
):
    """The privacy rule is structural, so it must hold for every command
    without any of them opting in."""
    scenario = await scenario_factory(locale="pt-br").start(command_key)

    for event in analytics_events:
        for key, value in (event.get("props") or {}).items():
            assert key not in ("value", "answer", "text", "content", "raw_value"), (
                f"{command_key} reported a raw answer in {event['event']}"
            )
            assert len(str(value)) <= constants.ANALYTICS_MAX_VALUE_LENGTH

    await scenario.finish()


def test_the_instrumented_engine_has_no_per_command_branch():
    """The anti-pattern this architecture exists to avoid, as an assertion."""
    offenders = []
    for relative in ENGINE_FILES:
        path = os.path.join(APP_ROOT, relative)
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            source = ast.dump(node)
            if "command_key" not in source and "feature" not in source:
                continue
            if not any(isinstance(op, (ast.Eq, ast.In)) for op in node.ops):
                continue
            for compared in node.comparators:
                if isinstance(compared, ast.Constant) and isinstance(compared.value, str):
                    offenders.append(f"{relative}:{node.lineno}")

    assert not offenders, (
        "instrumentation must never branch on which command it is measuring: "
        f"{offenders}"
    )


def test_a_new_command_needs_no_analytics_code_to_be_measured():
    """The events every YAML command inherits from the engine for free."""
    inherited = {
        "feature.setup_opened",
        "setup.step_viewed",
        "setup.step_completed",
        "setup.step_back",
        "setup.validation_failed",
        "setup.required_missing",
        "setup.completed",
        "setup.discarded",
        "setup.abandoned",
        "setup.discard_recovered",
        "feature.manager_opened",
        "config.changed",
        "feature.enabled",
        "feature.paused",
        "feature.unpaused",
        "feature.disabled",
        "feature.item_added",
        "feature.item_removed",
        "command.invoked",
        "command.failed",
    }
    declared = set(analytics.events_catalog())
    assert inherited <= declared

    remaining = declared - inherited
    assert remaining == {
        "guild.joined", "guild.removed", "guild.greeting_sent",
        "help.opened", "dashboard.opened", "report.submitted",
        "value.delivered", "value.blocked_by_permission",
        "feature.action_performed", "feature.tested",
    }, (
        "only guild lifecycle, support commands and domain-specific value "
        "delivery should ever need an explicit call site"
    )


def test_every_configuration_provider_resolves_to_something_callable():
    """The registry is resolved by name at call time, so nothing else checks it.

    A rename or a move passes every test in this repository and breaks
    `/admin insights` in production — and the failure it restores is the one
    that reported settings used by every guild as used by nobody.
    """
    for feature, path in config_state.PROVIDERS.items():
        assert feature in constants.COMMANDS_LIST, (
            f"{feature} is not a command, so no report will ever ask for it"
        )
        provider = config_state._resolve(path)
        assert callable(provider), f"{path} is not callable"


def test_every_configuration_provider_answers_in_the_shape_the_form_names(deps):
    """The point of the registry: a provider speaks YAML keys, not storage keys."""
    for feature in config_state.PROVIDERS:
        states = config_state.feature_config_states(feature)
        assert isinstance(states, list)
        assert all(isinstance(state, dict) for state in states)

        keys = {field["key"] for field in analytics_reports.configurable_fields(feature)}
        assert keys, f"{feature} declares no configurable field"
        for state in states:
            assert keys & set(state), (
                f"{feature} answered with none of the keys its form declares"
            )
