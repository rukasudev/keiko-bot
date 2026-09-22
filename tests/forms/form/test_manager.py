"""The manager panel's value lines, read the way an admin sees them."""

from app.settings.form.actions.action import PanelRow
from app.settings.form.manager import panel_groups, row_value
from app.settings.form.responses.styles import format_value


def _row(value, style=None):
    return PanelRow(key="k", title="Title", value=value, style=style)


def _grouped(**fields):
    return PanelRow(
        group="exceptions", group_title="Exceptions", group_declared=True, **fields
    )


def test_a_group_heading_carries_no_emoji_of_its_own():
    """Lucas: the panel emojis piled up. Every line already leads with its own
    icon, so the heading above them repeated one more."""
    rows = (
        _grouped(
            key="allowed_links",
            title="Websites",
            value=["youtube.com"],
            style="bullet",
            icon="🔗",
            target="card/allowed_links",
        ),
        _grouped(
            key="custom_links",
            title="Your links",
            value=[{}],
            style="composition",
            icon="📌",
            target="custom_links",
        ),
    )

    heading = panel_groups(rows, "Block Links", "pt-br")[0].heading

    assert heading == "### Exceptions"


def test_a_list_with_no_item_reads_on_one_line():
    """Broke as: "Your links:" and "None" came out on two lines, because an
    empty list was formatted as a block of items."""
    text, is_block = row_value(_row([], "composition"), "pt-br")

    assert (text, is_block) == ("Nada ainda", False)


def test_an_empty_list_of_a_declared_group_offers_add_instead_of_edit():
    """Lucas: the Exceptions heading showed one button for the popular sites
    and none for his own links, because an empty list has nothing to edit."""
    rows = (
        _grouped(
            key="allowed_links",
            title="Websites",
            value=[],
            style="bullet",
            icon="🔗",
            target="card/allowed_links",
            edit_label="Free a website",
        ),
        _grouped(
            key="custom_links",
            title="Your links",
            value=[],
            style="composition",
            icon="📌",
            target="custom_links",
            edit_label="Free specific links",
        ),
    )

    parts = panel_groups(rows, "Block Links", "pt-br")[0].parts

    assert [part.button for part in parts] == [
        "edit:card/allowed_links",
        "add:custom_links",
    ], "the button names the list it adds to, so its id never repeats the Add below"
    assert [part.label for part in parts] == ["Free a website", "Free specific links"]
    assert parts[1].emoji == "➕"


def test_an_empty_list_setting_reads_none_without_a_code_block():
    """Broke as: block_links with no popular site allowed showed six backticks."""
    for locale, none in (("pt-br", "Nada ainda"), ("en-us", "None")):
        for row in (
            _row({"style": "bullet", "values": []}),
            _row([], "channel"),
            _row([], "role"),
            _row([], "code"),
        ):
            text, is_block = row_value(row, locale)
            assert (text, is_block) == (none, False), row


def test_an_empty_list_formats_to_nothing_instead_of_an_empty_code_block():
    assert format_value([], "bullet", "pt-br") == ""
    assert format_value([], "numbered", "pt-br") == ""


def _links_document(count):
    from tests.behavioral.golden.paths.block_links import ENABLED

    document = dict(ENABLED)
    document["custom_links"] = {
        "style": "composition",
        "values": [
            {"link": {"value": f"site{index}.com", "title": "Link", "style": "code"}}
            for index in range(count)
        ],
    }
    return document


def _reachable(count):
    from app.settings.form.form_yaml import registry
    from app.settings.form.manager import (
        every_setting_has_a_button,
        panel_groups,
        panel_rows,
    )

    definition = registry.get("block_links")
    document = _links_document(count)
    rows = panel_rows(definition, document, "pt-br")
    groups = panel_groups(rows, "Bloquear Links", "pt-br")
    return every_setting_has_a_button(definition, document, groups, "pt-br", rows)


def test_a_list_longer_than_the_screen_keeps_the_panel_edit():
    """Broke as: a heading's own screen draws only the first three items, but
    the panel counted every item of the list as covered and dropped the global
    Edit, which was the only way to reach the fourth."""
    from app.constants import ViewConstants

    shown = ViewConstants.GROUP_ITEMS_SHOWN

    assert _reachable(shown) is True, "the screen buttons all of them"
    assert _reachable(shown + 1) is False, "the panel keeps its Edit for the rest"


def test_a_boolean_stored_as_a_string_reads_as_no():
    """Broke as: a styled option persists "True"/"False" as a STRING, and the
    old formatter read "False" as truthy and showed "Sim". The guarantee moved
    here with the formatter when the old engine's copy was deleted."""
    assert format_value("False", "boolean", "pt-br") == "Não"
    assert format_value("false", "boolean", "pt-br") == "Não"
    assert format_value("True", "boolean", "pt-br") == "Sim"
    assert format_value(False, "boolean", "pt-br") == "Não"
    assert format_value(True, "boolean", "en-us") == "Yes"


def test_the_code_style_keeps_one_value_inline_and_a_list_in_a_block():
    """The `code` style renders a URL monospaced on the panel and the review:
    one value inline, several inside a block. Moved here with the formatter."""
    assert format_value("meusite.com.br", "code", "pt-br") == "`meusite.com.br`"
    listed = format_value(["a.com", "b.com"], "code", "pt-br")
    assert listed.startswith("\n```") and "a.com\nb.com" in listed
