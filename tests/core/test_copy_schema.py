"""The copy schema: one model per primitive, composed into a command's `copy.yml` and
into each shared file, every key named by the schema and every field described."""

from typing import Any

import pytest
import yaml
from pydantic import BaseModel, RootModel, ValidationError

from app.core import copy_schema, i18n
from app.core.commands import SHARED_COPY, Text, command_for
from app.core.copy_schema import (
    SHARED_FILES,
    CommandCopy,
    CommandPlaces,
    CommandUi,
    validated,
)
from app.core.facts import ContextEntry, FactSpec, GlobalCatalog
from app.core.feature_health import Stats
from app.core.i18n import Plural
from app.core.utils import SetupRow
from app.forms.features.feature import Panel, PanelInfo, PanelInfoOverride, PanelPages
from app.forms.form.form_yaml import shared_vocabulary
from app.translator import Slash, SlashCommand, SlashParam
from app.ui.buttons import ButtonCopy
from app.ui.embed import (
    Announcement,
    AnnouncementDefaults,
    Card,
    CardOverride,
    CardRow,
    ErrorBlock,
    FieldOverride,
    Response,
    ResponseField,
    ResponseOverride,
)
from app.ui.records import Records, RecordsDetail, RecordsFilter
from tests.helpers.copy import SAMPLE

pytestmark = pytest.mark.unit

MODELS: tuple[type[BaseModel], ...] = (
    ResponseOverride,
    FieldOverride,
    CardOverride,
    PanelInfoOverride,
    FactSpec,
    ContextEntry,
    GlobalCatalog,
    Plural,
    Slash,
    SlashParam,
    SlashCommand,
    SetupRow,
    Stats,
    ErrorBlock,
    Response,
    ResponseField,
    Records,
    RecordsDetail,
    RecordsFilter,
    ButtonCopy,
    Panel,
    PanelInfo,
    PanelPages,
    Announcement,
    AnnouncementDefaults,
    Card,
    CardRow,
    CommandPlaces,
    CommandUi,
    CommandCopy,
    copy_schema.SharedCommands,
)
TEXT = {"en-us": "Ok", "pt-br": "Ok"}


def _sample() -> dict[str, Any]:
    loaded = yaml.safe_load(SAMPLE)
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_every_copy_model_refuses_a_key_it_does_not_name(model: type[BaseModel]):
    assert model.model_config.get("extra") == "forbid"
    assert model.model_config.get("frozen") is True


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_every_field_says_what_it_makes_the_bot_do(model: type[BaseModel]):
    undescribed = [
        name for name, field in model.model_fields.items() if not field.description
    ]
    assert undescribed == [], f"describe {model.__name__}: {undescribed}"
    assert model.__doc__, f"{model.__name__} says what it is"


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_every_key_of_a_copy_model_is_snake_case(model: type[BaseModel]):
    assert [
        name for name in model.model_fields if not i18n.SNAKE_KEY.fullmatch(name)
    ] == []


def test_a_copy_that_writes_every_block_validates():
    copy = CommandCopy.model_validate(_sample())

    assert copy.ui.responses["check"].overrides[0].title == Text.model_validate(
        {"en-us": "🚫 Blocked", "pt-br": "🚫 Bloqueado"}
    )
    assert copy.facts["shape"].values == ("round", "square")
    assert copy.ui.records["saved"].filter is not None
    assert copy.ui.announcement and copy.ui.announcement.defaults
    assert list(copy.ui.responses["check"].fields) == ["how", "files", "author"]
    assert copy.command.slash and copy.command.panel


@pytest.mark.parametrize("where", ["", "command", "ui"])
def test_every_level_of_a_copy_is_closed(where: str):
    sample = _sample()
    if where:
        sample[where] = {**sample[where], "response": {"title": TEXT}}
    else:
        sample["response"] = {"title": TEXT}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommandCopy.model_validate(sample)


def test_a_copy_writes_its_facts_then_the_fixed_places_then_what_it_draws():
    assert list(CommandCopy.model_fields) == ["facts", "command", "ui"]
    assert list(CommandPlaces.model_fields) == ["slash", "setup", "stats", "panel"]
    assert list(CommandUi.model_fields) == [
        "cards",
        "texts",
        "records",
        "responses",
        "errors",
        "buttons",
        "announcement",
    ]


@pytest.mark.parametrize(
    "text",
    ["Hello {member}", "{date} is saved"],
)
def test_a_copy_text_refuses_a_braced_placeholder(text: str):
    block = {"title": {"en-us": text, "pt-br": "Ok"}, "message": TEXT}

    with pytest.raises(ValidationError, match=r"copy names a value as \$"):
        ErrorBlock.model_validate(block)


def test_a_copy_text_names_a_value_with_a_dollar():
    block = {"title": TEXT, "message": {"en-us": "Hi $member", "pt-br": "Oi $member"}}

    assert ErrorBlock.model_validate(block).message.en_us == "Hi $member"


def test_the_announcement_defaults_are_admin_templates_with_braces():
    defaults = Announcement.model_validate(_sample()["ui"]["announcement"]).defaults

    assert defaults is not None and defaults.title.en_us == "Hello {user}!"


def test_an_override_writes_its_rule_and_at_least_one_field():
    rule = {"key": "global.facts.feature_state", "is": "paused"}

    with pytest.raises(ValidationError, match="replaces at least one field"):
        ResponseOverride.model_validate({"when": rule})
    with pytest.raises(ValidationError, match="when"):
        ResponseOverride.model_validate({"title": TEXT})
    assert (
        ResponseOverride.model_validate({"when": rule, "description": TEXT}).title
        is None
    )


def test_an_enum_fact_names_its_values_and_no_other_fact_does():
    with pytest.raises(ValidationError, match="an enum fact names its `values`"):
        FactSpec.model_validate({"type": "enum", "description": "x"})
    with pytest.raises(ValidationError, match="a text fact names no `values`"):
        FactSpec.model_validate({"type": "text", "values": ["a"], "description": "x"})


def test_a_plural_reads_one_for_a_single_count_and_other_for_the_rest():
    one = {"en-us": "1 sample", "pt-br": "1 amostra"}
    plural = Plural.model_validate({"one": one, "other": TEXT})

    assert plural.for_count(1).en_us == "1 sample"
    assert [plural.for_count(count).en_us for count in (0, 2, 40)] == ["Ok"] * 3
    with pytest.raises(ValidationError, match="other"):
        Plural.model_validate({"one": one})


def test_a_response_tells_an_absent_footer_from_a_null_one():
    absent = Response.model_validate({"title": TEXT, "description": TEXT})
    null = Response.model_validate({"title": TEXT, "description": TEXT, "footer": None})

    assert "footer" not in absent.model_fields_set
    assert "footer" in null.model_fields_set and null.footer is None


def test_a_button_may_leave_its_caption_out():
    assert ButtonCopy.model_validate({"label": TEXT}).description is None


def test_a_context_menu_writes_no_caption():
    assert Slash.model_validate({"name": TEXT}).description is None


@pytest.mark.parametrize(
    "key,document,missing",
    [
        (
            "welcome_messages",
            {"command": {"slash": {"name": TEXT}}},
            "command.setup, command.stats",
        ),
        ("translate", {"ui": {"texts": {"hint": TEXT}}}, "command.slash"),
    ],
    ids=["on setup", "with cogs"],
)
def test_a_command_copy_writes_what_its_declaration_needs(
    key: str, document: dict[str, Any], missing: str
):
    command = command_for(key)

    assert command.on_setup or command.cogs
    with pytest.raises(ValueError, match=f"{key} writes no {missing}"):
        validated(document, key, shared=False)


def test_a_prefix_no_command_owns_needs_no_block():
    assert isinstance(validated({}, "sample", shared=False), CommandCopy)


def test_every_shared_copy_file_has_a_schema():
    shared = {
        path.stem for path, _prefix in i18n.files() if path.parent.name == "languages"
    }

    assert shared == set(SHARED_FILES)
    assert set(SHARED_COPY) == set(SHARED_FILES), "the registry refuses these keys"


def test_a_shared_buttons_file_holds_nothing_but_buttons():
    buttons = SHARED_FILES["buttons"]

    assert issubclass(buttons, RootModel)
    buttons.model_validate({"confirm": {"label": TEXT, "description": TEXT}})
    with pytest.raises(ValidationError):
        buttons.model_validate({"cooldown": {"title": TEXT, "description": TEXT}})


def test_the_standard_footer_is_written_once_as_the_forms_fallback():
    shared = SHARED_FILES["commands"]

    with pytest.raises(ValidationError, match="footer"):
        shared.model_validate({"footer": TEXT})
    assert i18n.standard_footer() == shared_vocabulary().fallbacks.footer
    assert i18n.standard_footer().en_us.startswith("• ")


def test_an_unknown_shared_file_has_no_schema():
    with pytest.raises(ValueError, match="no schema names a shared copy file"):
        validated({}, "colors", shared=True)
