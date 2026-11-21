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
    templated,
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


def test_an_admin_template_fills_each_of_its_variables_once_and_keeps_the_rest():
    """The birthday card's preview lines filled one variable after the other, so a
    server named with a brace read that brace as a variable too: `{server}` showed
    `Club May 12` for a server named `Club {date}`."""
    values = {"server": "Club {date}", "date": "May 12", "user": "<@1>"}

    shown = templated("{server} on {date} for {user} {foo} {USER} $count", values)

    assert shown == "Club {date} on May 12 for <@1> {foo} {USER} $count"
    assert templated(ADMIN_TYPED, {}) == ADMIN_TYPED


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


@pytest.mark.parametrize(
    "text,values,shown",
    [
        ("Página $page de $pages", {"page": 1, "pages": 1}, "Página 1 de 1"),
        ("$pages pages, page $page", {"pages": 3, "page": 2}, "3 pages, page 2"),
        ("$a and $b", {"a": "$b", "b": "x"}, "$b and x"),
        ("$count of $total", {"count": 2}, "2 of $total"),
    ],
    ids=[
        "name-inside-another",
        "longer-name-first",
        "value-not-read",
        "name-not-given",
    ],
)
def test_fill_fills_each_name_once_and_keeps_a_name_not_given(text, values, shown):
    """Keiko's own `$name` lines: a name that starts another fills only its own
    placeholder, a value is never read as a placeholder, a name not given stays."""
    assert fill(text, **values) == shown


@pytest.mark.parametrize(
    "template,values,required,shown",
    [
        ("{a} and {b}", {"a": "1", "b": "2"}, None, "1 and 2"),
        ("{a} {b}", {"a": "{b}", "b": "x"}, None, "{b} x"),
        (
            "{a:>99} {A} {a.real} {a[0]} {other}",
            {"a": "1"},
            None,
            "{a:>99} {A} {a.real} {a[0]} {other}",
        ),
        ("Live!", {"link": "https://x"}, "link", "Live!\nhttps://x"),
        ("Go {link} now", {"link": "https://x"}, "link", "Go https://x now"),
        ("{LINK}", {"link": "https://x"}, "link", "{LINK}\nhttps://x"),
    ],
    ids=[
        "each-filled",
        "value-not-read",
        "other-braces-kept",
        "required-added",
        "required-present",
        "other-capitals-are-text",
    ],
)
def test_an_admin_template_fills_its_placeholders_and_adds_the_required_one(
    template, values, required, shown
):
    """The one filler behind the templates an admin writes: each `{name}` once, a
    value never read as a template, anything else in braces as typed, and the
    required placeholder added on its own line when not written in lowercase."""
    assert fill_placeholders(template, values, required=required) == shown
