"""The copy schema: one model per primitive, composed into a command's `copy.yml` and
into each shared file, every key named by the schema and every field described."""

from typing import Any

import pytest
import yaml
from pydantic import BaseModel, RootModel, ValidationError

from app.core import copy_schema, i18n
from app.core.commands import Text, command_for
from app.core.copy_schema import SHARED_FILES, CommandCopy, validated
from app.core.feature_health import Stats
from app.core.i18n import Plural, Variant
from app.core.utils import SetupRow
from app.forms.features.feature import Panel, PanelInfo, PanelPages
from app.forms.form.form_yaml import shared_vocabulary
from app.translator import Slash, SlashCommand, SlashParam
from app.ui.buttons import ButtonCopy
from app.ui.embed import (
    Announcement,
    AnnouncementDefaults,
    Card,
    CardRow,
    ErrorBlock,
    Response,
    ResponseField,
)
from app.ui.records import Records, RecordsDetail, RecordsFilter
from tests.helpers.copy import SAMPLE

pytestmark = pytest.mark.unit

MODELS: tuple[type[BaseModel], ...] = (
    Variant,
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

    assert copy.responses["check"].variants["blocked"].title == Text.model_validate(
        {"en-us": "🚫 Blocked", "pt-br": "🚫 Bloqueado"}
    )
    assert copy.records["saved"].filter is not None
    assert copy.announcement and copy.announcement.defaults
    assert list(copy.responses["check"].fields) == ["how", "files", "author"]


def test_the_top_level_of_a_copy_is_closed():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommandCopy.model_validate({**_sample(), "response": {"title": TEXT}})


def test_the_blocks_closed_by_the_schema_come_before_the_open_ones():
    closed = ["slash", "setup", "stats", "panel", "announcement"]
    assert list(CommandCopy.model_fields)[: len(closed)] == closed


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
    defaults = Announcement.model_validate(_sample()["announcement"]).defaults

    assert defaults is not None and defaults.title.en_us == "Hello {user}!"


def test_a_variant_writes_its_title_its_description_or_both():
    with pytest.raises(ValidationError, match="a variant writes"):
        Variant.model_validate({})
    assert Variant.model_validate({"description": TEXT}).title is None


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
        ("welcome_messages", {"slash": {"name": TEXT}}, "setup, stats"),
        ("translate", {"messages": {"hint": TEXT}}, "slash"),
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
