"""A notice's mention writes what it pings and lets Discord ping only that."""

import pytest

from app.core.mentions import Mention, mention_of


@pytest.mark.parametrize(
    "kind, roles, prefix, allowed",
    [
        ("none", None, "", {"parse": []}),
        ("everyone", None, "@everyone", {"parse": ["everyone"]}),
        ("here", ["1"], "@here", {"parse": ["everyone"]}),
        ("roles", ["11", "22"], "<@&11> <@&22>", {"parse": [], "roles": [11, 22]}),
        ("roles", "11", "<@&11>", {"parse": [], "roles": [11]}),
    ],
    ids=["nobody", "everyone", "here", "two-roles", "one-role-stored-as-itself"],
)
def test_a_mention_writes_and_allows_exactly_what_it_names(
    kind, roles, prefix, allowed
):
    mention = mention_of(kind, roles)

    assert mention.prefix == prefix
    assert mention.allowed.to_dict() == allowed


@pytest.mark.parametrize(
    "kind, roles",
    [
        (None, None),
        ("", None),
        (False, None),
        (True, None),
        ("@everyone", None),
        ("roles", None),
        ("roles", []),
        ("roles", ["", "not-an-id"]),
    ],
)
def test_a_choice_it_does_not_know_pings_nobody(kind, roles):
    assert mention_of(kind, roles) == Mention()


@pytest.mark.parametrize("kind", ["none", "everyone", "here", "roles"])
def test_only_the_chosen_mention_is_written_live(kind):
    """@everyone and @here share Discord's one `everyone` flag, so a typed one is
    written as text; a typed role or member stays a mention Discord draws, and the
    allowed mentions leave it out, so it pings nobody."""
    role, member = 123456789012345678, 234567890123456789
    typed = f"@everyone @here <@&{role}> <@{member}> <@!{member}> live!"
    mention = mention_of(kind, ["11"])

    written = mention.before(typed)

    assert written == " ".join(
        part
        for part in (
            mention.prefix,
            f"@\u200beveryone @\u200bhere <@&{role}> <@{member}> <@!{member}> live!",
        )
        if part
    )
    allowed = mention.allowed.to_dict()
    assert "users" not in allowed["parse"] and member not in allowed.get("users", [])
    assert "roles" not in allowed["parse"] and role not in allowed.get("roles", [])


def test_the_mention_goes_in_front_of_the_notice():
    assert mention_of("everyone").before("gaules is live!") == (
        "@everyone gaules is live!"
    )
    assert mention_of("none").before("gaules is live!") == "gaules is live!"
    assert mention_of("here").before(None) == "@here"
    assert mention_of("none").before(None) is None
    assert mention_of("none").before("") is None
