"""`docs/yaml-reference.html` is the YAML reference generated from the schemas: it is
current, every field of every schema says what it does and names its role, the
generator shows what a reader needs of each field (its role, its type, its rules, a
real example), and every text has a current Portuguese in
`scripts/keiko/yamldoc/translations.yml`.

The models still being rewritten are listed in `UNDESCRIBED` by type; a listed model
whose fields are all described fails, so the list only shrinks. The types not
translated yet are listed in `UNTRANSLATED` the same way.
"""

import json
import os
import re
import sys

import pytest
from pydantic import BaseModel, Field

from tests.helpers.yaml_schemas import FLOW, Flow, Loose

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scripts.keiko.yamldoc import DOCS, reference  # noqa: E402
from scripts.keiko.yamldoc.files import read_file  # noqa: E402
from scripts.keiko.yamldoc.index import REFERENCE  # noqa: E402
from scripts.keiko.yamldoc.page import Documented, payload, render  # noqa: E402
from scripts.keiko.yamldoc.schema import ROLES, read_schema  # noqa: E402
from scripts.keiko.yamldoc.translations import (  # noqa: E402
    Translations,
    load_translations,
    read_translations,
    texts,
)

from app.core.commands import YAML_TYPES, YamlType  # noqa: E402

pytestmark = pytest.mark.unit

UNDESCRIBED: dict[str, frozenset[str]] = {
}

UNTRANSLATED: frozenset[str] = frozenset()

FLOW_PATH = "fixtures/demo/flow.yml"
FLOW_PT = """\
flow.yml/Flow.version:
  en-us: The version a session is pinned to.
  pt-br: A versão em que uma sessão fica presa.
flow.yml/Flow.steps:
  en-us: The screens.
  pt-br: As telas.
"""


def _schemas():
    for kind in YAML_TYPES:
        model = kind.model()
        if model is not None:
            yield pytest.param(kind.name, read_schema(model), id=kind.name)


def _texts():
    for kind in YAML_TYPES:
        model = kind.model()
        schema = read_schema(model) if model is not None else None
        yield pytest.param(
            kind.name, texts(kind.name, kind.purpose, schema), id=kind.name
        )


def test_the_yaml_reference_is_current():
    page = DOCS / REFERENCE
    current = page.read_text(encoding="utf-8") if page.exists() else ""

    assert current == reference(), "docs/yaml-reference.html is stale: run make docs"


@pytest.mark.parametrize("name, schema", list(_schemas()))
def test_every_field_says_what_it_does(name, schema):
    listed = UNDESCRIBED.get(name, frozenset())
    missing = sorted(
        f"{model}.{field}"
        for model, fields in schema.undescribed().items()
        if model not in listed
        for field in fields
    )

    assert not missing, (
        f"{name}: give these fields a Field(description=...), one sentence saying "
        f"what the field makes the bot do: {missing}"
    )


@pytest.mark.parametrize("name, schema", list(_schemas()))
def test_the_undescribed_list_only_shrinks(name, schema):
    undescribed = schema.undescribed()
    done = sorted(
        model
        for model in UNDESCRIBED.get(name, frozenset())
        if model not in undescribed
    )

    assert not done, (
        f"{name}: every field of {done} is described now, or the model is gone: "
        "remove them from UNDESCRIBED in tests/test_yaml_reference.py"
    )


def test_the_undescribed_list_names_registered_types_only():
    registered = {kind.name for kind in YAML_TYPES if kind.schema}

    assert set(UNDESCRIBED) <= registered, sorted(set(UNDESCRIBED) - registered)


def test_a_field_without_a_description_is_found():
    assert read_schema(Loose).undescribed() == {"Loose": ("unsaid",)}


@pytest.mark.parametrize("name, schema", list(_schemas()))
def test_every_field_names_its_role(name, schema):
    unroled = sorted(
        f"{model}.{field}"
        for model, fields in schema.unroled().items()
        for field in fields
    )

    assert not unroled, (
        f'{name}: give these fields a role, json_schema_extra={{"role": ...}} with '
        f"one of {', '.join(ROLES)}: {unroled}"
    )


@pytest.mark.parametrize("name, found", list(_texts()))
def test_every_text_has_a_current_portuguese(name, found):
    gaps = load_translations().gaps(found)

    if name in UNTRANSLATED:
        assert gaps, (
            f"{name} is translated now: remove it from UNTRANSLATED in "
            "tests/test_yaml_reference.py"
        )
    else:
        assert not gaps, (
            f"{name}: translate these texts in scripts/keiko/yamldoc/translations.yml, "
            "each with the English it translates (stale: the English changed since): "
            + ", ".join(f"{key} ({gap})" for key, gap in gaps.items())
        )


def test_the_untranslated_list_names_registered_types_only():
    registered = {kind.name for kind in YAML_TYPES}

    assert UNTRANSLATED <= registered, sorted(UNTRANSLATED - registered)


def test_every_translation_translates_a_text_that_exists():
    known = [key for param in _texts() for key in param.values[1]]

    orphans = load_translations().orphans(known)

    assert not orphans, (
        "scripts/keiko/yamldoc/translations.yml translates texts no schema has any "
        f"more (renamed or removed): re-key or delete {orphans}"
    )


def _flow_type() -> YamlType:
    return YamlType(
        name="flow.yml",
        files="fixtures/*/flow.yml",
        schema="tests.helpers.yaml_schemas:Flow",
        purpose="A flow of `screens`.",
        readers=("app/flows.py: reads it",),
    )


def _documented() -> Documented:
    schema = read_schema(Flow)
    reading = read_file(schema, FLOW, FLOW_PATH)
    return Documented(_flow_type(), {FLOW_PATH: FLOW}, schema, reading)


def _flow(translations: Translations = Translations()) -> dict:
    return payload([_documented()], translations)["types"][0]


def _field(flow: dict, model: str, name: str) -> dict:
    found = next(item for item in flow["models"] if item["n"] == model)
    return next(item for item in found["f"] if item["n"] == name)


def _fixture_page() -> str:
    return render([_documented()], read_translations(FLOW_PT))


def test_the_reference_shows_every_field_with_what_a_reader_needs():
    flow = _flow()

    assert flow["id"] == "flow-yml"
    assert flow["purpose"] == ["A flow of `screens`."]
    assert flow["readers"] == ["app/flows.py: reads it"]
    assert flow["to"] == {"k": "model", "n": "Flow"}
    assert [model["n"] for model in flow["models"]] == [
        "Flow",
        "IntroStep",
        "PickStep",
        "Words",
    ]
    assert _field(flow, "Flow", "version") | {"x": None} == {
        "n": "version",
        "s": "version",
        "t": {"k": "scalar", "n": "integer"},
        "mk": "number",
        "r": "identity",
        "d": ["The version a session is pinned to."],
        "df": "1",
        "u": [1, 1],
        "x": None,
    }
    steps = _field(flow, "Flow", "steps")
    assert (steps["s"], steps["q"], steps["mk"]) == ("steps[]", True, "list")
    assert steps["to"] == {"k": "kinds", "n": "Step"}
    assert _field(flow, "IntroStep", "lines")["ru"] == [
        ["minimum", 1, False],
        ["maximum", 5, False],
    ]
    assert _field(flow, "PickStep", "options")["ru"] == [["minItems", 1, False]]
    assert _field(flow, "PickStep", "style")["t"] == {
        "k": "enum",
        "v": ["buttons", "select"],
    }
    assert _field(flow, "IntroStep", "texts")["o"] == ["# open: text names -> Words"]
    assert _field(flow, "Flow", "names")["o"] == [
        "# open: answer keys -> the label of each"
    ]
    assert _field(flow, "PickStep", "options")["x"] == {
        "f": FLOW_PATH,
        "l": 18,
        "c": "options:\n  - red\n  - blue",
    }


def test_a_bilingual_text_is_a_leaf_with_its_own_mark():
    flow = _flow()
    title = _field(flow, "IntroStep", "title")
    words = next(model for model in flow["models"] if model["n"] == "Words")

    assert title["mk"] == "i18n"
    assert "to" not in title
    assert words["text"] is True


def test_a_union_names_its_kinds_and_the_keys_they_share():
    (step,) = _flow()["kinds"]

    assert (step["n"], step["key"], step["shared"]) == (
        "Step",
        "kind",
        ["key", "title"],
    )
    assert [(member["tag"], member["model"]) for member in step["m"]] == [
        ("intro", "IntroStep"),
        ("pick", "PickStep"),
    ]
    assert step["m"][1]["x"]["l"] == 12
    assert step["m"][1]["x"]["c"].startswith("kind: pick\nkey: color\n")


def test_a_shared_key_counts_its_uses_in_every_kind():
    assert _field(_flow(), "PickStep", "key")["u"] == [2, 1]


def test_the_example_file_marks_each_key_by_the_path_the_tree_names_it():
    sample = _flow()["sample"]

    assert sample["file"] == FLOW_PATH
    assert sample["lines"] == FLOW.splitlines()
    assert [18, 20, "steps[]/kind=pick/options[]", "PickStep.options"] in sample[
        "marks"
    ]
    assert [8, 11, "steps[]/kind=intro/texts{}", "IntroStep.texts"] in sample["marks"]
    assert [22, 23, "names{}", "Flow.names"] in sample["marks"]


def test_a_field_role_is_read_from_its_schema_extra():
    flow = _flow()

    assert _field(flow, "Flow", "version")["r"] == "identity"
    assert _field(flow, "PickStep", "options")["r"] == "behavior"
    assert read_schema(Flow).unroled() == {}


def test_a_field_without_a_role_is_found_and_drawn_under_other():
    schema = read_schema(Loose)
    kind = YamlType("loose.yml", "loose.yml", "tests:Loose", "Loose.", ())
    found = payload(
        [Documented(kind, {}, schema, read_file(schema, "", ""))], Translations()
    )
    fields = found["types"][0]["models"][0]["f"]

    assert schema.unroled() == {"Loose": ("said", "unsaid")}
    assert all("r" not in field for field in fields)


def test_an_unknown_role_is_refused():
    class Colored(BaseModel):
        hue: str = Field(description="A hue.", json_schema_extra={"role": "colour"})

    with pytest.raises(ValueError, match="Colored.hue: the role 'colour' is none of"):
        read_schema(Colored)


def test_the_portuguese_shows_only_while_it_translates_the_current_english():
    flow = _flow(read_translations(FLOW_PT))

    assert _field(flow, "Flow", "version")["d"] == [
        "The version a session is pinned to.",
        "A versão em que uma sessão fica presa.",
    ]
    assert _field(flow, "Flow", "steps")["d"] == ["The screens, in order."]
    assert _field(flow, "Flow", "names")["d"] == ["Labels by key."]


def test_a_missing_or_a_stale_translation_is_found():
    found = texts("flow.yml", "A flow of `screens`.", read_schema(Flow))

    gaps = read_translations(FLOW_PT).gaps(found)

    assert "flow.yml/Flow.version" not in gaps
    assert gaps["flow.yml/Flow.steps"] == "stale"
    assert gaps["flow.yml/Flow.names"] == "missing"
    assert gaps["flow.yml"] == "missing"
    assert gaps["flow.yml/Flow"] == "missing"


def test_a_translation_of_a_text_that_is_gone_is_found():
    found = texts("flow.yml", "A flow of `screens`.", read_schema(Flow))
    renamed = FLOW_PT.replace("Flow.version", "Flow.release")

    assert read_translations(renamed).orphans(list(found)) == ["flow.yml/Flow.release"]


@pytest.mark.parametrize(
    "broken",
    [
        "- a list\n",
        "flow.yml/Flow.version: A versão.\n",
        "flow.yml/Flow.version:\n  en-us: The version.\n",
        "flow.yml/Flow.version:\n  en-us: The version.\n  pt-br: [a, list]\n",
    ],
    ids=["not-a-mapping", "no-english", "no-portuguese", "not-a-text"],
)
def test_a_translation_file_of_the_wrong_shape_is_refused(broken):
    with pytest.raises(ValueError):
        read_translations(broken)


def test_the_page_embeds_the_payload_it_draws_and_fetches_nothing():
    page = _fixture_page()
    embedded = re.search(
        r'<script id="data" type="application/json">(.*?)</script>', page, re.S
    )

    assert embedded is not None
    assert json.loads(embedded.group(1)) == payload(
        [_documented()], read_translations(FLOW_PT)
    )
    assert not re.search(r"""(src|href)=["']?(https?:)?//""", page)
    assert "@import" not in page


def test_the_reference_is_the_same_every_time_it_is_built():
    assert _fixture_page() == _fixture_page()
