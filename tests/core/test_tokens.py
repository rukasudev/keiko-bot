"""The one syntax of a Keiko text (`app/core/tokens.py`): how each token is read and
the one pass that fills it."""

import re

import pytest

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

pytestmark = pytest.mark.unit


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
