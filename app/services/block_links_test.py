"""Unit tests for the block_links matcher and config normalizer (pure logic).

History note: the previous version of this file PINNED three production bugs
as expected behavior (mutate-during-iteration leaving an allowed link marked
as blocked, ValueError for www./trailing-slash links, and exact-string-only
matching that kept `twitter.com/user/...` blocked under an allowed Twitter).
Per the bug-fix protocol these tests now assert the CORRECT behavior and were
written before the fix (red first).
"""
import pytest

from app.services.block_links import (
    MessageSubject,
    evaluate_message,
    find_blocked_links,
    matches_domain,
    matches_exact,
    normalize_block_links_config,
)
from app.settings.form.responses.links import parse_link


def _config(mode="block_all", domains=None, entries=None):
    return {
        "mode": mode,
        "allowed_links": {"style": "bullet", "values": domains or []},
        "custom_links": {"style": "composition", "values": entries or []},
    }


def _entry(link, match="domain"):
    return {
        "link": {"value": link},
        "match_type": {"value": "label ignored", "_raw_value": match},
    }


class TestMatchesDomain:
    def test_exact_host(self):
        assert matches_domain(parse_link("https://twitter.com"), "twitter.com")

    def test_subdomain_matches(self):
        assert matches_domain(parse_link("https://m.youtube.com/watch"), "youtube.com")
        assert matches_domain(parse_link("https://open.spotify.com/track/1"), "spotify.com")

    def test_lookalike_does_not_match(self):
        assert not matches_domain(parse_link("https://notyoutube.com"), "youtube.com")
        assert not matches_domain(parse_link("https://youtube.com.evil.tld"), "youtube.com")

    def test_quick_pick_aliases(self):
        assert matches_domain(parse_link("https://youtu.be/abc"), "youtube.com")
        assert matches_domain(parse_link("https://x.com/user"), "twitter.com")
        assert matches_domain(parse_link("https://discord.com/invite/abc"), "discord.gg")


class TestMatchesExact:
    def test_ignores_scheme_www_trailing_slash_and_case(self):
        stored = "youtube.com/watch"
        assert matches_exact(parse_link("http://WWW.youtube.com/watch/"), parse_link(stored))

    def test_different_path_does_not_match(self):
        assert not matches_exact(
            parse_link("https://youtube.com/other"), parse_link("youtube.com/watch")
        )

    def test_stored_query_must_be_subset(self):
        stored = parse_link("youtube.com/watch?v=abc")
        assert matches_exact(parse_link("https://youtube.com/watch?v=abc&si=track"), stored)
        assert not matches_exact(parse_link("https://youtube.com/watch?v=other"), stored)

    def test_stored_without_query_matches_any_query(self):
        stored = parse_link("youtube.com/watch")
        assert matches_exact(parse_link("https://youtube.com/watch?v=abc"), stored)


class TestFindBlockedLinksBlockAll:
    def test_all_allowed_links_pass(self):
        """Was the mutate-during-iteration bug: two allowed links used to
        leave one behind and get the message deleted anyway."""
        config = _config(domains=["twitter.com", "instagram.com"])
        links = ["https://twitter.com", "https://instagram.com"]
        assert find_blocked_links(links, config) == []
        assert links == ["https://twitter.com", "https://instagram.com"]  # no mutation

    def test_www_and_trailing_slash_are_allowed(self):
        """Was the ValueError bug: any www./trailing-slash variant of an
        allowed site raised instead of matching."""
        config = _config(domains=["twitter.com"])
        assert find_blocked_links(["https://www.twitter.com"], config) == []
        assert find_blocked_links(["https://twitter.com/"], config) == []

    def test_path_under_allowed_domain_is_allowed(self):
        """Design change from the old exact-string match: allowing a website
        now allows its real links, not only the bare homepage."""
        config = _config(domains=["twitter.com"])
        assert find_blocked_links(
            ["https://twitter.com/user/status/123"], config
        ) == []

    def test_non_allowed_link_is_blocked(self):
        config = _config(domains=["instagram.com"])
        assert find_blocked_links(["https://twitter.com"], config) == ["https://twitter.com"]

    def test_everything_blocked_without_rules(self):
        config = _config(domains=[], entries=[])
        assert find_blocked_links(["https://anything.com/x"], config) == ["https://anything.com/x"]

    def test_custom_domain_entry_allows(self):
        config = _config(entries=[_entry("meusite.com.br")])
        assert find_blocked_links(["https://blog.meusite.com.br/post"], config) == []

    def test_custom_exact_entry_allows_only_that_link(self):
        config = _config(entries=[_entry("youtube.com/watch?v=abc", match="exact")])
        assert find_blocked_links(["https://youtube.com/watch?v=abc"], config) == []
        assert find_blocked_links(["https://youtube.com/watch?v=zzz"], config) == [
            "https://youtube.com/watch?v=zzz"
        ]

    def test_empty_links(self):
        assert find_blocked_links([], _config(domains=["twitter.com"])) == []


class TestFindBlockedLinksAllowAll:
    def test_only_listed_entries_are_blocked(self):
        config = _config(mode="allow_all", entries=[_entry("spam.com")])
        assert find_blocked_links(["https://youtube.com/watch"], config) == []
        assert find_blocked_links(["https://spam.com/offer"], config) == ["https://spam.com/offer"]

    def test_exact_entry_blocks_only_that_link(self):
        config = _config(mode="allow_all", entries=[_entry("bit.ly/golpe", match="exact")])
        assert find_blocked_links(["https://bit.ly/golpe"], config) == ["https://bit.ly/golpe"]
        assert find_blocked_links(["https://bit.ly/legit"], config) == []

    def test_quick_picks_are_irrelevant_in_allow_all(self):
        config = _config(mode="allow_all", domains=["twitter.com"], entries=[])
        assert find_blocked_links(["https://twitter.com"], config) == []


class TestNormalizeLegacyConfig:
    def test_legacy_labels_translate_to_domains(self):
        legacy = {
            "guild_id": "1", "enabled": True,
            "allowed_chats": {"style": "channel", "values": "100"},
            "allowed_roles": {"style": "role", "values": "201"},
            "allowed_links": ["Youtube", "Discord"],
            "answer": "no links!",
        }
        config = normalize_block_links_config(legacy)
        assert config["mode"] == "block_all"
        assert config["allowed_links"]["values"] == ["youtube.com", "discord.gg"]

    def test_legacy_scalar_label(self):
        config = normalize_block_links_config({"allowed_links": "Twitter", "answer": "x"})
        assert config["allowed_links"]["values"] == ["twitter.com"]

    def test_unknown_legacy_label_is_skipped_without_error(self):
        """Was a KeyError: any unexpected label crashed enforcement."""
        config = normalize_block_links_config({"allowed_links": ["Youtube", "whatever"]})
        assert config["allowed_links"]["values"] == ["youtube.com"]

    def test_scalar_envelopes_are_coerced_to_lists(self):
        """Was the inert-exemption bug: a single configured role/channel was
        checked char-by-char / by substring."""
        config = normalize_block_links_config({
            "mode": "block_all",
            "allowed_chats": {"style": "channel", "values": "100"},
            "allowed_roles": {"style": "role", "values": "201"},
        })
        assert config["allowed_chats"]["values"] == ["100"]
        assert config["allowed_roles"]["values"] == ["201"]

    def test_missing_keys_get_safe_defaults(self):
        config = normalize_block_links_config({"mode": "allow_all"})
        assert config["custom_links"]["values"] == []
        assert config["allowed_links"]["values"] == []
        assert config["allowed_chats"]["values"] == []
        assert config["allowed_roles"]["values"] == []

    def test_add_custom_setup_key_is_dropped(self):
        config = normalize_block_links_config({"mode": "block_all", "add_custom": {"values": True}})
        assert "add_custom" not in config

    def test_new_shape_passes_through(self):
        new_shape = _config(mode="allow_all", entries=[_entry("spam.com")])
        config = normalize_block_links_config(dict(new_shape))
        assert config["mode"] == "allow_all"
        assert config["custom_links"]["values"] == new_shape["custom_links"]["values"]


class TestNormalizeLinkTransform:
    """Transform normalize_link: valor salvo sem esquema/www/barra/fragment."""

    def _serialize(self, value):
        from app.settings.form.responses.transforms import TRANSFORMS
        return TRANSFORMS["normalize_link"].serialize({"link": value})

    def test_strips_scheme_www_trailing_slash_and_fragment(self):
        assert self._serialize("https://WWW.YouTube.com/watch?v=abc#t=10") == \
            "youtube.com/watch?v=abc"

    def test_bare_domain_variants_normalize_to_domain(self):
        assert self._serialize("https://www.meusite.com.br/") == "meusite.com.br"
        assert self._serialize("meusite.com.br") == "meusite.com.br"

    def test_query_is_preserved(self):
        assert self._serialize("bit.ly/x?id=1&b=2") == "bit.ly/x?id=1&b=2"


class TestEvaluateMessage:
    """The single decision path: enforcement short-circuits, the diagnostic
    walks every gate, and the two can never disagree."""

    def _cogs(self, **overrides):
        cogs = {"enabled": True, **_config()}
        cogs.update(overrides)
        return cogs

    def _subject(self, content="https://spam.com", roles=(), channel="1"):
        return MessageSubject(
            author_role_ids=tuple(roles), channel_id=channel, content=content
        )

    def test_reports_every_gate_when_full(self):
        cogs = self._cogs(allowed_roles={"values": ["77"]},
                          allowed_chats={"values": ["1"]})
        evaluation = evaluate_message(
            self._subject(roles=("77",), channel="1"), cogs, full=True
        )

        assert [gate.key for gate in evaluation.gates] == [
            "feature", "role", "channel", "links", "rules",
        ]
        assert evaluation.gate("role").reason == "role-exempt"
        assert evaluation.gate("role").detail == ("77",)
        assert evaluation.gate("channel").reason == "channel-exempt"
        assert evaluation.would_block is False

    def test_enforcement_stops_at_the_first_failed_gate(self):
        cogs = self._cogs(allowed_roles={"values": ["77"]})
        evaluation = evaluate_message(self._subject(roles=("77",)), cogs)

        assert [gate.key for gate in evaluation.gates] == ["feature", "role"]
        assert evaluation.would_block is False

    def test_paused_configuration_never_blocks(self):
        evaluation = evaluate_message(
            self._subject(), self._cogs(enabled=False), full=True
        )
        assert evaluation.gate("feature").reason == "paused"
        assert evaluation.would_block is False

    def test_missing_configuration_is_reported_apart_from_paused(self):
        evaluation = evaluate_message(self._subject(), None, full=True)
        assert evaluation.gate("feature").reason == "not-configured"
        assert evaluation.would_block is False

    @pytest.mark.parametrize("mode,domains,entries,link,reason,rule", [
        ("block_all", ["youtube.com"], [], "https://youtube.com/watch?v=1",
         "allowed-by-popular", "youtube.com"),
        ("block_all", [], [_entry("twitch.tv/jway", "exact")], "https://twitch.tv/jway",
         "allowed-by-custom", "twitch.tv/jway"),
        ("block_all", [], [_entry("meusite.com.br")], "https://meusite.com.br/promo",
         "allowed-by-custom", "meusite.com.br"),
        ("block_all", [], [], "https://spam.com", "blocked-no-rule", None),
        ("allow_all", [], [_entry("bit.ly/golpe", "exact")], "https://bit.ly/golpe",
         "blocked-by-custom", "bit.ly/golpe"),
        ("allow_all", [], [], "https://spam.com", "allowed-by-default", None),
    ])
    def test_each_link_names_the_rule_that_decided_it(
            self, mode, domains, entries, link, reason, rule):
        cogs = {"enabled": True, **_config(mode=mode, domains=domains, entries=entries)}
        evaluation = evaluate_message(self._subject(content=link), cogs, full=True)

        verdict = evaluation.links[0]
        assert verdict.reason == reason
        assert verdict.rule == rule
        assert verdict.blocked == reason.startswith("blocked")

    @pytest.mark.parametrize("mode", ["block_all", "allow_all"])
    @pytest.mark.parametrize("roles", [(), ("77",)])
    @pytest.mark.parametrize("channel", ["1", "9"])
    @pytest.mark.parametrize("content", [
        "sem link nenhum",
        "https://spam.com",
        "https://youtube.com/watch?v=1 e https://bit.ly/golpe",
    ])
    def test_full_and_short_circuit_evaluations_never_disagree(
            self, mode, roles, channel, content):
        """The anti-drift contract: the diagnostic explains exactly what
        enforcement does. If these ever diverge, the context menu would be
        lying to the user about their own server.

        The verdict per link is deliberately still computed when an earlier
        gate already spared the message, so the diagnostic can say "this link
        breaks a rule, but the channel is exempt". What must never differ is
        the outcome, and the blocked list whenever that outcome is a block."""
        cogs = {
            "enabled": True,
            "allowed_roles": {"values": ["77"]},
            "allowed_chats": {"values": ["1"]},
            **_config(mode=mode, domains=["youtube.com"],
                      entries=[_entry("bit.ly/golpe", "exact")]),
        }
        subject = self._subject(content=content, roles=roles, channel=channel)

        enforcement = evaluate_message(subject, cogs)
        diagnostic = evaluate_message(subject, cogs, full=True)

        assert enforcement.would_block == diagnostic.would_block
        if diagnostic.would_block:
            assert enforcement.blocked_links == diagnostic.blocked_links

    def test_enforcement_matches_the_legacy_matcher(self):
        """find_blocked_links is now a wrapper over the same verdicts."""
        config = normalize_block_links_config(
            {"enabled": True, **_config(domains=["youtube.com"])}
        )
        links = ["https://youtube.com/watch?v=1", "https://spam.com"]
        evaluation = evaluate_message(
            self._subject(content=" ".join(links)), {"enabled": True, **_config(domains=["youtube.com"])}
        )
        assert list(evaluation.blocked_links) == find_blocked_links(links, config)

    def test_legacy_document_still_evaluates(self):
        legacy = {"enabled": True, "allowed_links": ["Youtube"], "answer": "x"}
        evaluation = evaluate_message(
            self._subject(content="https://youtube.com/watch?v=1"), legacy, full=True
        )
        assert evaluation.links[0].reason == "allowed-by-popular"
        assert evaluation.answer == "x"
