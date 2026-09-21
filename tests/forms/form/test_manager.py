"""The manager panel's value lines, read the way an admin sees them."""

from app.settings.form.actions.action import PanelRow
from app.settings.form.manager import row_value
from app.settings.form.responses.styles import format_value


def _row(value, style=None):
    return PanelRow(key="k", title="Title", value=value, style=style)


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
