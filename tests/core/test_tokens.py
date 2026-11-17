"""The one syntax of a Keiko text (`app/core/tokens.py`): how each token is read, the
one pass that fills it, and the paths it never reads."""

import re
from types import SimpleNamespace

import pytest

from app.commands.stream_elements.service import (
    create_response_embed,
    parse_placeholders,
)
from app.commands.welcome.service import render_welcome_message
from app.core.tokens import (
    Token,
    TokenError,
    fact_names,
    finished,
    included,
    pieces,
    plain,
    resolved,
    tokens,
)
from app.core.utils import fill, fill_placeholders
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.unit

ADMIN_TYPED = "$count ${context.prefix} $$ ${prefix}ping ${random.1-10} {context:x}"


@pytest.mark.parametrize(
    "written,token",
    [
        ("$count", Token("fact", "count", "$count")),
        ("${prefix}", Token("fact", "prefix", "${prefix}")),
        (
            "${answer.link:host|youtube.com}",
            Token(
                "answer",
                "link",
                "${answer.link:host|youtube.com}",
                format="host",
                fallback="youtube.com",
            ),
        ),
        (
            "${answer.link|a|b}",
            Token("answer", "link", "${answer.link|a|b}", fallback="a|b"),
        ),
        (
            "${plural.roles|Role|Roles}",
            Token(
                "plural",
                "roles",
                "${plural.roles|Role|Roles}",
                one="Role",
                many="Roles",
            ),
        ),
        (
            "${context.commands.setup}",
            Token("context", "commands.setup", "${context.commands.setup}"),
        ),
        ("${ref.channel}", Token("ref", "channel", "${ref.channel}")),
    ],
    ids=["fact", "glued fact", "answer", "fallback", "plural", "context", "ref"],
)
def test_each_kind_of_token_is_read_with_what_it_carries(written, token):
    assert tokens(f"a {written} b") == (token,)


def test_a_name_is_read_whole_up_to_the_first_character_no_name_holds():
    assert fact_names("$count. $count_x $Count $counts, $page/$pages") == [
        "count",
        "count_x",
        "Count",
        "counts",
        "page",
        "pages",
    ]


def test_a_dollar_before_no_name_is_text_and_a_doubled_one_is_one_dollar():
    assert pieces("R$ 5, $5, $$count and $") == ("R$ 5, $5, $count and $",)
    assert plain("Run ${prefix}ping for {user}") == "Run ping for {user}"


@pytest.mark.parametrize(
    "written,refusal",
    [
        ("${prefix", "is never closed"),
        ("${contxt.commands.setup}", "did you mean ${context.commands.setup}?"),
        ("${prefix x}", "is no token"),
        ("${answer.link:Host}", "is written ${answer.<key>}"),
        ("${answer.x|${context.prefix}}", "holds another token"),
        ("{answer:link:host|a}", "write ${answer.link:host|a}"),
        ("{ref: channel}", "write ${ref.channel}"),
    ],
)
def test_the_syntax_refuses_what_it_cannot_read(written, refusal):
    with pytest.raises(TokenError, match=re.escape(refusal)):
        pieces(written)


def test_a_pass_reads_leniently_what_only_the_loaders_refuse():
    assert resolved("${prefix {answer:x}", lambda token: "!") == "${prefix {answer:x}"


def test_one_pass_fills_each_token_and_never_reads_a_value_it_put_in():
    text = "$user said ${answer.x} in ${context.server_name}"
    values = {"fact": "$count", "answer": "${context.server_name}", "context": "Keiko"}

    shown = resolved(text, lambda token: values[token.kind])

    assert shown == "$count said ${context.server_name} in Keiko"


def test_an_earlier_pass_keeps_what_the_last_one_fills_and_escapes_what_it_put_in():
    loaded = resolved(
        "$$5 at ${context.commands.setup} for $count",
        lambda token: "/set$up" if token.kind == "context" else None,
        last=False,
    )

    assert loaded == "$$5 at /set$$up for $count"
    assert resolved(loaded, lambda token: "2") == "$5 at /set$up for 2"


def test_a_text_with_no_token_left_is_finished_and_one_with_a_token_waits():
    assert finished("It costs $$5 or $$USD") == "It costs $5 or $USD"
    assert finished("$$5 for $count") == "$$5 for $count"


def test_a_shared_text_is_included_as_written_so_its_tokens_are_read_with_the_rest():
    shared = {"footer": "Run ${context.commands.report} for $count"}

    assert included("${ref.footer}!", shared.__getitem__) == (
        "Run ${context.commands.report} for $count!"
    )
    assert included("no ref $$5", shared.__getitem__) == "no ref $$5"


def test_fill_reads_a_glued_name_and_refuses_a_given_name_written_in_braces():
    assert fill("Try `${prefix}ping`", prefix="!") == "Try `!ping`"
    with pytest.raises(ValueError, match="copy names a value as"):
        fill("Hi {user}", user="x")


def test_an_admin_template_is_filled_by_its_own_braces_and_never_by_keiko_tokens():
    """The messages an admin writes are filled by `fill_placeholders`, one `{name}`
    at a time; Keiko's syntax does not run over them, so whatever an admin typed that
    looks like it stays as typed."""
    guild = create_guild(id=1, name="Keiko Club")
    member = create_member(guild, id=111, name="TestUser")
    member._user = SimpleNamespace(id=111)

    shown = render_welcome_message(f"Hi {{user}} {ADMIN_TYPED}", member)

    assert shown == f"Hi <@!111> {ADMIN_TYPED}"
    assert fill_placeholders(ADMIN_TYPED, {"count": "1"}) == ADMIN_TYPED


def test_a_streamelements_reply_keeps_its_own_variables_out_of_keiko_tokens():
    """A reply Keiko fetches writes StreamElements' `$(touser)`, `${touser}` and
    `${random.1-10}`, filled by `parse_placeholders` alone; the embed puts the reply
    in as it is, and only the footer, Keiko's copy, is filled by the one syntax."""
    guild = create_guild(id=1, name="Keiko Club")
    member = create_member(guild, id=111, name="TestUser")
    reply = parse_placeholders(
        "Hi ${touser}, $(touser) ${random.1-1} $count ${context.prefix} $$", member
    )

    embed = create_response_embed("mouse", reply, member, "gaules", "ks!", "en-us")

    assert (
        reply
        == f"Hi {member.mention}, {member.mention} 1 $count ${{context.prefix}} $$"
    )
    assert embed.description == reply
    assert "ks!commands" in embed.footer.text and "$" not in embed.footer.text
