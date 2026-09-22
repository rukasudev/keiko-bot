"""The link parser the validator, the transform and the matcher all read."""

import pytest

from app.settings.form.responses.links import link_host, parse_link

pytestmark = pytest.mark.unit


class TestParseLink:
    """Moved here with the parser: the services copy was deleted, and this
    was its only coverage. "Which site is this" has one owner now, so the
    form validator and the runtime matcher cannot disagree."""

    def test_defaults_scheme_when_absent(self):
        assert parse_link("discord.gg/abc").host == "discord.gg"

    def test_lowercases_host_and_strips_www(self):
        parsed = parse_link("https://WWW.Youtube.com/Watch")
        assert parsed.host == "youtube.com"

    def test_strips_single_trailing_slash_and_fragment(self):
        parsed = parse_link("https://twitter.com/user/#section")
        assert parsed.path == "/user"

    def test_keeps_query(self):
        parsed = parse_link("https://youtube.com/watch?v=abc")
        assert parsed.query.get("v") == "abc"

    def test_link_host_agrees_with_parse_link(self):
        for value in ("https://WWW.Youtube.com/Watch", "discord.gg/abc", "x.com"):
            assert link_host(value) == parse_link(value).host

    def test_link_host_keeps_the_empty_guard(self):
        assert link_host("") == ""
        assert link_host(None) == ""
