"""The closing subtext line of an embed with fields is its last line, under them.

Broke as: `with_footer` (`app/ui/embed.py`) appended the closing line to the
description, which Discord draws above the fields, so the link check (once its
/report line left the raw embed footer) read "-# • Use the `/report` command ..."
under its title and above its three fields, the /report DM opened with "If you need
help ..." above the ticket's fields, and a form step showing its pick drew its
/report line above the "Selected" field. Shared behaviour: `with_footer`, which
`response_embed` and every form screen (`embed_of`) close with. Consumers that
exposed it: the link check, the /report DM. Guaranteed: an embed with fields closes
with the line as subtext in a last, full-width field under them; an embed without
fields still closes its description with it.
"""

import pytest

from app.constants import Style
from app.forms.discord.views import embed_of
from app.forms.form.components import Field, Screen
from app.ui.embed import standard_footer

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


@pytest.mark.parametrize(
    "locale,line",
    [
        (
            "en-us",
            "-# • If you need help, don't hesitate to call us on my official server "
            "with the `/support` command.",
        ),
        (
            "pt-br",
            "-# • Se precisar de ajuda, é só me chamar no meu servidor oficial com o "
            "comando `/suporte`.",
        ),
    ],
)
def test_the_report_dm_closes_with_its_support_line_under_the_ticket(locale, line):
    from app.commands.report.service import ticket_embed

    embed = ticket_embed(locale, "KEIKO-7", "Broken", "It broke", "help")

    assert embed.description is None, "nothing between the title and the ticket"
    *ticket, closing = embed.fields
    assert len(ticket) == 4
    assert all(field.inline for field in ticket)
    assert (closing.name, closing.value, closing.inline) == (
        Style.BLANK_FIELD_NAME,
        line,
        False,
    )


@pytest.mark.parametrize("locale", ["en-us", "pt-br"])
def test_a_form_screen_with_fields_closes_with_its_line_under_them(locale):
    footer = standard_footer(locale)
    picked = Screen(
        title="Match", description="Pick one", footer=footer, fields=(Field("#1", "A"),)
    )
    plain = Screen(title="Match", description="Pick one", footer=footer)

    embed = embed_of(picked)

    assert embed.description == "Pick one"
    assert [(field.name, field.value) for field in embed.fields] == [
        ("#1", "A"),
        (Style.BLANK_FIELD_NAME, f"-# {footer}"),
    ]
    assert embed_of(plain).description == f"Pick one\n\n-# {footer}"
