"""Catalog contract for product analytics.

The catalog (app/analytics/catalog.yml) is the only place an event name is
allowed to be born. This suite fails the build when the code and the catalog
disagree, so nobody has to remember to keep them in sync — the point of having
a catalog at all.
"""
import ast
import os
import re

import pytest
import yaml
from pydantic import ValidationError

from app.constants import Commands as constants
from app.core import analytics, analytics_writer
from app.forms.features import feature_keys

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("analytics")]

APP_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "app")

MINIMAL_DECLARATION = {
    "version": 1,
    "collection": "activity",
    "description": "Something happened.",
    "recorded_in": "app/core/events.py",
    "actor": "user",
    "props": ["host"],
}


def _emit_literals():
    """Every string literal handed to a record or emit call under app/.

    `record` is the service entry point; `emit` and `emit_event` are the form
    engine's, whose events the adapter hands to `record` with the session.
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
                if called not in ("record", "emit", "emit_event"):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    literals.append((first.value, os.path.relpath(path, APP_ROOT)))
    return literals


def test_the_catalog_file_is_read_through_its_schema():
    """Broke as: the catalog was read as a plain dict, so a misspelled `lifetim:` or a
    field nothing reads (`cardinality_risk`, `results`) sat in the file without a trace.
    Guaranteed: every field the file writes is one the schema declares, and the readers
    hand out the typed declarations."""
    raw = yaml.safe_load(analytics.CATALOG_FILE.read_text(encoding="utf-8"))

    assert set(raw) == set(analytics.Catalog.model_fields)
    for name, declaration in raw["events"].items():
        assert set(declaration) <= set(analytics.EventDeclaration.model_fields), name
    assert all(
        isinstance(declaration, analytics.EventDeclaration)
        for declaration in analytics.events_catalog().values()
    )
    assert analytics.catalog() is analytics.catalog(), "the file is read once"


@pytest.mark.parametrize(
    "change, refusal",
    [
        ({"lifetim": {"inc": {"totals.errors": 1}}}, "extra_forbidden"),
        ({"cardinality_risk": "low"}, "extra_forbidden"),
        ({"required": ["host"]}, "extra_forbidden"),
        ({"collection": "audit"}, "literal_error"),
        ({"actor": "member"}, "literal_error"),
        ({"description": "  "}, "string_too_short"),
        ({"version": 0}, "greater_than_equal"),
        ({"private_props": ["link"]}, "keeps private a prop it does not declare"),
        ({"collection": "none", "props": ["link"], "private_props": ["link"]},
         "keeps a prop private but writes no document"),
        ({"tally": {"name": "blocked", "by": "link", "label": "site"}},
         "tallies a prop it does not declare"),
        ({"tally": {"name": "blocked", "label": "site"}}, "missing"),
        ({"scope": "guild"}, "literal_error"),
        ({"lifetime": {"when": {"found": True}, "inc": {"totals.errors": 1}}},
         "advances its lifetime on a prop it does not declare"),
        ({"counts_as_value": True, "lifetime": {"inc": {"totals.errors": 1}}},
         "writes a lifetime and counts as value"),
        ({"scope": "keiko", "lifetime": {"inc": {"totals.errors": 1}}},
         "writes a lifetime but names no server"),
        ({"lifetime": {"inc": {"totals.errors": 0}}}, "greater_than"),
        ({"lifetime": {"inc": {"totals.errors": True}}}, "int_type"),
        ({"lifetime": {"set": {"removed_at": "now"}}}, "literal_error"),
        ({"lifetime": {"min": {"joined_at": None}}}, "literal_error"),
        ({"lifetime": {"counts_admin": "yes"}}, "bool_type"),
        ({"lifetime": {"removed_props": {"setups_completed": "totals.setups_completed"}}},
         "which this block does not write once per server"),
        ({"lifetime": {"inc": {"features.{feature}.reports": 1},
                       "removed_props": {"reports": "features.{feature}.reports"}}},
         "which this block does not write once per server"),
        ({"lifetime": {"inc": {"totals.errors": 1}, "every": {"x": 1}}}, "extra_forbidden"),
    ],
)
def test_the_schema_refuses_a_declaration_it_cannot_honour(change, refusal):
    """Guaranteed: a field nothing reads, a value outside its vocabulary, a reference to
    a prop the event does not declare and two fields that would silently override each
    other are refused when the catalog is read, never ignored."""
    analytics.EventDeclaration.model_validate(MINIMAL_DECLARATION)

    with pytest.raises(ValidationError) as refused:
        analytics.EventDeclaration.model_validate({**MINIMAL_DECLARATION, **change})

    assert refusal in str(refused.value) or refusal in {
        error["type"] for error in refused.value.errors()
    }


def test_the_root_refuses_a_vocabulary_no_event_reads():
    """Broke as: `results` listed three values for a `result` prop no event declares,
    so every `result` was dropped anyway. Guaranteed: the root holds only what a reader
    reads."""
    with pytest.raises(ValidationError, match="results"):
        analytics.Catalog.model_validate(
            {"sources": ["slash"], "results": ["success"], "events": {}}
        )


def test_a_result_prop_is_dropped_as_undeclared_like_any_other():
    analytics.reset()

    envelope = analytics.record(
        "command.invoked", props={"command": "ping", "source": "slash", "result": "success"}
    )

    assert envelope is not None and "result" not in envelope["props"]
    assert analytics.stats()["undeclared_props"] == 1


def test_event_names_follow_the_domain_dot_fact_convention():
    domains = {"command", "feature", "setup", "config", "guild", "value", "help",
               "dashboard", "report", "link", "keiko"}
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
    assert not unknown, f"record() calls outside the catalog: {unknown}"


def test_every_declared_event_is_actually_emitted_somewhere():
    """A catalog entry nobody emits is documentation rot, and it makes the
    dashboards promise numbers that will always be zero."""
    emitted = {event for event, _ in _emit_literals()}
    emitted |= set(_lifecycle_events())

    never_emitted = set(analytics.events_catalog()) - emitted
    assert not never_emitted, (
        f"declared in catalog.yml but never emitted: {sorted(never_emitted)}"
    )


def test_every_file_a_declaration_cites_exists():
    """Broke as: the lifecycle events cited app/forms/features/generic.py and
    report.submitted cited app/commands/report/service.py, two files that do not exist, so
    whoever followed `recorded_in` to an event landed nowhere. Guaranteed: every path
    a `recorded_in` names is a file of the repository."""
    repository = os.path.dirname(APP_ROOT)
    cited = {
        (name, path)
        for name, declaration in analytics.events_catalog().items()
        for path in re.findall(r"app/[\w/]+\.\w+", declaration.recorded_in)
    }

    missing = sorted(
        (name, path)
        for name, path in cited
        if not os.path.isfile(os.path.join(repository, path))
    )
    assert cited, "no declaration cites a file, so the pattern no longer matches"
    assert not missing, f"recorded_in cites files that do not exist: {missing}"


def _lifecycle_events():
    """Lifecycle events reach `record` through the commit's map, not literally."""
    from app.core.cogs import LIFECYCLE_ANALYTICS_EVENTS

    return LIFECYCLE_ANALYTICS_EVENTS.values()


def test_lifecycle_events_are_changes_kept_for_good():
    """A lifecycle event is the history a panel lists, so it is a change: written at
    once, never in a batch that can be lost, and kept with no TTL."""
    from app.core.cogs import LIFECYCLE_ANALYTICS_EVENTS

    declared = analytics.events_catalog()
    for lifecycle_key, event in LIFECYCLE_ANALYTICS_EVENTS.items():
        assert event in declared, f"{lifecycle_key} maps to an undeclared event"
        assert declared[event].collection == "changes", event


def test_high_volume_events_never_become_documents():
    """value.delivered is the volume driver (D2 of database PR 2); counting it with
    no document is what keeps storage bounded."""
    declaration = analytics.events_catalog()["value.delivered"]
    assert declaration.collection == "none", (
        "value.delivered would create one document per delivery"
    )


def test_each_collection_keeps_its_events_as_long_as_its_ttl_says():
    """Broke as: `retention_days` was declared on every event and read by nothing,
    while one TTL index decided. Guaranteed: the retention of an event is the TTL of
    its collection, the schema's index and the constant agree, and a change has none."""
    from app.migrations import runner

    events = runner.declared_structure()["events"]

    assert events["activity"]["ts_1"]["expireAfterSeconds"] == (
        constants.EVENTS_ACTIVITY_TTL_SECONDS
    )
    assert events["interactions"]["ts_1"]["expireAfterSeconds"] == (
        constants.EVENTS_INTERACTIONS_TTL_SECONDS
    )
    assert not any("expireAfterSeconds" in spec for spec in events["changes"].values())


def test_the_analytics_writer_rules_only_reference_catalog_events():
    declared = analytics.events_catalog()
    unknown = [name for name in analytics_writer.COMMAND_TALLIES if name not in declared]
    assert not unknown, f"the analytics writer's rules reference undeclared events: {unknown}"


def test_every_removed_prop_is_one_guild_removed_declares():
    """The analytics writer names no event (rule 22): how an event advances the lifetime is its
    `lifetime` block, and the schema checks each block on its own. What it cannot see from
    one event is the other: every `removed_props` key is a prop `guild.removed` declares."""
    declared = analytics.events_catalog()
    removal_props = set(declared["guild.removed"].props)
    blocks = {
        name: declaration.lifetime
        for name, declaration in declared.items()
        if declaration.lifetime is not None
    }

    assert "guild.joined" in blocks and "report.submitted" in blocks
    for name, block in blocks.items():
        for prop in block.removed_props:
            assert prop in removal_props, f"{name} reports {prop}, which guild.removed lacks"


def test_the_value_and_the_tallies_the_sink_reads_are_declared_on_their_events():
    """The analytics writer names no event of one command (rule 22): an event that is value says
    so with `counts_as_value`, whose lifetime is the writer's value rule and no block of
    its own, and a tally of Keiko's own with `tally`."""
    declared = analytics.events_catalog()

    assert set(analytics.value_events()) == {"value.delivered", "link.blocked"}
    assert all(declared[name].lifetime is None for name in analytics.value_events())
    assert declared["link.blocked"].tally == analytics.Tally(
        name="blocked", by="host", label="site"
    )
    assert analytics_writer.VALUE_RULE.written() == {
        "features.{feature}.value_count",
        "totals.value_events",
        "features.{feature}.first_value_at",
        "features.{feature}.last_value_at",
        "last_value_at",
    }


def test_unknown_events_are_dropped_instead_of_stored():
    analytics.reset()
    assert analytics.record("totally.made_up", props={"value": 1}) is None
    assert analytics.stats()["unknown"] == 1
    assert analytics.drain() == []


def test_record_never_raises_when_the_envelope_is_hostile():
    analytics.reset()

    class Exploding:
        def __str__(self):
            raise RuntimeError("boom")

    assert analytics.record(
        "command.invoked", props={"command": Exploding(), "source": "slash"}
    )
    assert analytics.record("command.invoked", props={"command": "ping", "source": "slash"})


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
    assert analytics.records_raw_choice({"type": "button_options", "options": [{"value": "block_all"}]})
    assert analytics.records_raw_choice({"type": "design_select", "options": [{"value": "server_blur"}]})
    assert analytics.records_raw_choice({"type": "boolean_toggle"})

    assert not analytics.records_raw_choice({"type": "modal_input"})
    assert not analytics.records_raw_choice({"type": "channels", "key": "allowed_chats"})
    assert not analytics.records_raw_choice({"type": "assignable_roles"})
    assert not analytics.records_raw_choice({"type": "text"})
    assert not analytics.records_raw_choice(None)


def test_no_free_text_or_id_field_of_any_form_is_ever_tabulated():
    """Swept across every real form, so a new field cannot opt itself in."""
    from app.core.analytics_reports import configurable_fields

    for feature in list(feature_keys()):
        for field in configurable_fields(feature):
            if not field["closed_vocabulary"]:
                continue
            assert field["kind"] not in (
                "text", "modal-input", "title-content", "file-upload",
                "channel-select", "user-select", "selects", "composition",
            ), f"{feature}.{field['key']} ({field['kind']}) must not expose values"


def test_disabling_analytics_makes_record_a_no_op():
    analytics.reset()
    analytics.configure(_FakeConfig(enabled=False))
    try:
        assert analytics.record(
            "command.invoked", props={"command": "ping", "source": "slash"}
        ) is None
        assert analytics.drain() == []
    finally:
        analytics.configure(_FakeConfig(enabled=True))


class _FakeConfig:
    def __init__(self, enabled):
        self.ANALYTICS_ENABLED = enabled

    def is_prod(self):
        return False
