"""Catalog contract for product analytics.

The catalog (app/analytics/catalog.yml) is the only place an event name is
allowed to be born. This suite fails the build when the code and the catalog
disagree, so nobody has to remember to keep them in sync — the point of having
a catalog at all.
"""
import ast
import os

import pytest

from app.constants import Commands as constants
from app.services import analytics, analytics_sink

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("analytics")]

APP_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "app")

REQUIRED_DECLARATION_KEYS = {"version", "class", "description", "emitted_at", "actor"}
VALID_CLASSES = {"event", "counter", "audit"}
VALID_ACTORS = {"user", "system", "job"}


def _emit_literals():
    """Every string literal handed to an emit call under app/.

    `emit` is the service entry point; `emit_event` is the form-engine mixin
    that fills the session context in before calling it.
    """
    literals = []
    for root, _, files in os.walk(APP_ROOT):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called not in ("emit", "emit_event", "emit_from_view"):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    literals.append((first.value, os.path.relpath(path, APP_ROOT)))
    return literals


def test_every_declaration_carries_the_required_schema():
    for name, declaration in analytics.events_catalog().items():
        missing = REQUIRED_DECLARATION_KEYS - set(declaration)
        assert not missing, f"{name} is missing {sorted(missing)} in catalog.yml"
        assert set(declaration["class"]) <= VALID_CLASSES, name
        assert declaration["actor"] in VALID_ACTORS, name
        assert declaration["description"].strip(), name


def test_event_names_follow_the_domain_dot_fact_convention():
    domains = {"command", "feature", "setup", "config", "guild", "value", "help",
               "dashboard", "report"}
    for name in analytics.events_catalog():
        domain, _, fact = name.partition(".")
        assert fact, f"{name} must be <domain>.<fact>"
        assert domain in domains, f"{name} uses an undeclared domain"
        assert name.islower(), name


def test_every_emit_in_the_code_uses_a_catalog_name():
    declared = analytics.events_catalog()
    unknown = [
        (event, path) for event, path in _emit_literals() if event not in declared
    ]
    assert not unknown, f"emit() calls outside the catalog: {unknown}"


def test_every_declared_event_is_actually_emitted_somewhere():
    """A catalog entry nobody emits is documentation rot, and it makes the
    dashboards promise numbers that will always be zero."""
    emitted = {event for event, _ in _emit_literals()}
    emitted |= set(_lifecycle_events())

    never_emitted = set(analytics.events_catalog()) - emitted
    assert not never_emitted, (
        f"declared in catalog.yml but never emitted: {sorted(never_emitted)}"
    )


def _lifecycle_events():
    """Lifecycle events reach `emit` through insert_cog_event, not literally."""
    from app.services.cogs import LIFECYCLE_ANALYTICS_EVENTS

    return LIFECYCLE_ANALYTICS_EVENTS.values()


def test_lifecycle_events_are_emitted_through_the_audit_facade():
    """The state changes already flowed through insert_cog_event, so the
    product view costs no new call sites — the point of section 9.3."""
    from app.services.cogs import LIFECYCLE_ANALYTICS_EVENTS

    declared = analytics.events_catalog()
    for audit_key, event in LIFECYCLE_ANALYTICS_EVENTS.items():
        assert event in declared, f"{audit_key} maps to an undeclared event"
        assert declared[event].get("audit"), f"{event} must also be an audit record"


def test_audit_events_declare_the_audit_class():
    for name, declaration in analytics.events_catalog().items():
        if declaration.get("audit"):
            assert "audit" in declaration["class"], name


def test_high_volume_events_never_become_documents():
    """value.delivered and feature.action_performed are the volume drivers;
    keeping them out of the `event` class is what keeps storage bounded."""
    for name in ("value.delivered", "feature.action_performed"):
        declaration = analytics.events_catalog()[name]
        assert declaration["class"] == ["counter"], (
            f"{name} would create one document per delivery"
        )


def test_profile_rules_only_reference_catalog_events():
    declared = analytics.events_catalog()
    unknown = [name for name in analytics_sink.PROFILE_RULES if name not in declared]
    assert not unknown, f"PROFILE_RULES references undeclared events: {unknown}"


def test_unknown_events_are_dropped_instead_of_stored():
    analytics.reset()
    assert analytics.emit("totally.made_up", value=1) is None
    assert analytics.stats()["unknown"] == 1
    assert analytics.drain() == []


def test_emit_never_raises_when_the_envelope_is_hostile():
    analytics.reset()

    class Exploding:
        def __str__(self):
            raise RuntimeError("boom")

    assert analytics.emit("command.invoked", command=Exploding(), source="slash")
    assert analytics.emit("command.invoked", command="ping", source="slash")


def test_free_text_never_survives_sanitization():
    typed = "my server invite is discord.gg/secret and my token is abc"
    clean = analytics.sanitize_props({
        "error_key": "link-not-recognized",
        "typed_value": typed,
        "payload": {"nested": "object"},
        "count": 3,
        "flag": True,
    })

    assert clean["error_key"] == "link-not-recognized"
    assert clean["count"] == 3
    assert clean["flag"] is True
    assert "payload" not in clean
    assert len(clean["typed_value"]) <= 64


def test_props_are_capped_so_one_call_cannot_explode_a_document():
    clean = analytics.sanitize_props({f"key_{i}": i for i in range(50)})
    assert len(clean) == 12


def test_only_values_the_yaml_itself_lists_may_be_recorded():
    """The gate is the configuration, not a hardcoded list of step types: a
    field is safe to tabulate exactly when the YAML declares where its value
    comes from. Channels and roles resolve to ids at runtime and never qualify.
    """
    assert analytics.records_raw_choice({"type": "button-options", "options": [{"value": "block_all"}]})
    assert analytics.records_raw_choice({"designs": [{"key": "server_blur"}]})
    assert analytics.records_raw_choice({"type": "boolean-toggle"})

    assert not analytics.records_raw_choice({"type": "modal-input"})
    assert not analytics.records_raw_choice({"type": "channels", "key": "allowed_chats"})
    assert not analytics.records_raw_choice({"type": "available_roles"})
    assert not analytics.records_raw_choice({"action": "file_upload"})
    assert not analytics.records_raw_choice(None)


def test_no_free_text_or_id_field_of_any_form_is_ever_tabulated():
    """Swept across every real form, so a new field cannot opt itself in."""
    from app.services.analytics_reports import configurable_fields

    for feature in constants.COMMANDS_LIST:
        for field in configurable_fields(feature):
            if not field["closed_vocabulary"]:
                continue
            assert field["action"] not in (
                "modal", "modal-input", "title-content", "file_upload",
                "channels", "channel-select", "roles", "available_roles",
                "user_select", "composition",
            ), f"{feature}.{field['key']} ({field['action']}) must not expose values"


def test_disabling_analytics_makes_emit_a_no_op():
    analytics.reset()
    analytics.configure(_FakeConfig(enabled=False))
    try:
        assert analytics.emit("command.invoked", command="ping", source="slash") is None
        assert analytics.drain() == []
    finally:
        analytics.configure(_FakeConfig(enabled=True))


class _FakeConfig:
    def __init__(self, enabled):
        self.ANALYTICS_ENABLED = enabled

    def is_prod(self):
        return False
