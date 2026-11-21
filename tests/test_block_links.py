"""Block links at runtime: a message or an edit with a blocked link is deleted and
answered in the channel, each blocked link is recorded with the rule that decided
it, recording never breaks the moderation, and the "Check Link Blocking" message
command answers why a link was (or was not) blocked, in the three items the
moderation itself decides on. Which link a rule blocks is pinned as pure logic in
tests/test_block_links_matcher.py."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.commands.block_links.service import (
    MessageSubject,
    check_edited_message,
    check_message,
    evaluate_message,
    link_check_embed,
    send_link_check_message,
)
from app.constants import Commands, Style
from app.core import analytics, i18n
from app.core.utils import fill, ml
from app.forms.form.form_yaml import CardStep, registry
from app.ui.embed import Response
from tests.generators import moderations
from tests.generators.events import blocked_links
from tests.mocks import create_message


async def block(deps, guild, **saved):
    """Save the server's block links, in the shape the card saves it."""
    saved.setdefault("allowed_links", [])
    await moderations.block_links(deps.mongo_client, guild_id=str(guild.id), **saved)


async def recorded(guild):
    """The blocked links once the analytics writer wrote its batch."""
    await analytics.flush()
    return blocked_links(str(guild.id))


def edit(guild, channel, message_id, data):
    """The raw payload Discord sends for an edited message."""
    return SimpleNamespace(
        guild_id=guild.id, channel_id=channel.id, message_id=message_id, data=data
    )


def fetches_of(channel, monkeypatch):
    """The messages Discord is asked to fetch from `channel`, in order."""
    asked = []
    fetch = channel.fetch_message

    async def fetch_message(message_id):
        asked.append(message_id)
        return await fetch(message_id)

    monkeypatch.setattr(channel, "fetch_message", fetch_message)
    return asked


class TestBlockLinksCheckMessage:
    """What a message with a link meets in the channel."""

    async def test_a_blocked_link_is_deleted_and_answered_naming_its_author(
        self, deps, guild, channel, member
    ):
        """The message goes, and the server's answer reaches the channel with
        `{user}` filled with the author's mention."""
        await block(deps, guild, allowed_links=["twitter"], answer="Sem links, {user}!")
        msg = create_message(channel, member, "Check this https://spam-site.com")

        await check_message(str(guild.id), msg)

        msg.assert_deleted()
        assert [sent.content for sent in channel.get_all_messages()] == [
            f"Sem links, {member.mention}!"
        ]

    async def test_a_listed_link_without_a_scheme_is_deleted_in_allow_all_mode(
        self, deps, guild, channel, member
    ):
        """With every link allowed but a list, a listed link written without
        http(s):// still goes; its choice saved with the raw value alone decides."""
        await block(
            deps,
            guild,
            mode="allow_all",
            custom_links=[
                {"link": {"value": "bit.ly/golpe"}, "match_type": {"_raw_value": "exact"}}
            ],
        )
        msg = create_message(channel, member, "corre em bit.ly/golpe")

        await check_message(str(guild.id), msg)

        msg.assert_deleted()

    @pytest.mark.parametrize("saved", [None, {"enabled": False}], ids=["unset", "disabled"])
    async def test_nothing_happens_when_block_links_is_not_on(
        self, deps, guild, channel, member, saved
    ):
        if saved is not None:
            await block(deps, guild, **saved)
        msg = create_message(channel, member, "Check https://spam-site.com")

        await check_message(str(guild.id), msg)

        msg.assert_not_deleted()
        assert channel.get_all_messages() == []

    async def test_a_message_without_a_link_is_left_alone(
        self, deps, guild, channel, member
    ):
        await block(deps, guild)
        msg = create_message(channel, member, "Hello world, no links here!")

        await check_message(str(guild.id), msg)

        msg.assert_not_deleted()
        assert channel.get_all_messages() == []


class TestBlockLinksEditedMessages:
    """A message edited after it was sent (reported in a manual test)."""

    async def test_blocks_a_link_added_by_editing_the_message(
        self, deps, guild, bot, channel, member
    ):
        """Bug reportado: mandar uma mensagem sem link e depois edita-la
        incluindo um link burlava o bloqueio, porque so existia listener
        de on_message."""
        await block(deps, guild)
        msg = channel.register_message(
            create_message(channel, member, "Agora vai https://spam-site.com")
        )
        bot.get_channel = MagicMock(return_value=channel)

        await check_edited_message(
            bot, edit(guild, channel, msg.id, {"content": "Agora vai https://spam-site.com"})
        )

        msg.assert_deleted()

    @pytest.mark.parametrize(
        "data",
        [{"embeds": [{"url": "x"}]}, {"content": "corrigindo o texto"}],
        ids=["only-a-link-preview", "no-link-in-the-new-text"],
    )
    async def test_an_edit_with_no_new_link_fetches_nothing(
        self, deps, guild, bot, channel, monkeypatch, data
    ):
        """Discord sends an edit when it attaches a link's preview, and an edit can
        drop the link: with no new link there is nothing to check again, and fetching
        the message would cost one HTTP call per preview."""
        await block(deps, guild)
        bot.get_channel = MagicMock(return_value=channel)
        asked = fetches_of(channel, monkeypatch)

        await check_edited_message(bot, edit(guild, channel, 500, data))

        assert asked == []

    async def test_ignores_edits_from_bots(self, deps, guild, bot, channel, bot_member):
        await block(deps, guild)
        msg = channel.register_message(
            create_message(channel, bot_member, "https://spam-site.com")
        )
        bot.get_channel = MagicMock(return_value=channel)

        await check_edited_message(
            bot, edit(guild, channel, msg.id, {"content": "https://spam-site.com"})
        )

        msg.assert_not_deleted()


class TestBlockLinksRecords:
    """O que eu bloqueei fica registrado para a listagem e as estatisticas."""

    @pytest.fixture(autouse=True)
    async def youtube_allowed(self, deps, guild):
        await block(deps, guild, allowed_links=["youtube.com"], answer="Nada de links!")

    async def test_records_the_blocked_link_with_the_rule_that_decided_it(
        self, guild, channel, member
    ):
        msg = create_message(channel, member, "olha https://spam-site.com/promo")

        await check_message(str(guild.id), msg)

        [record] = await recorded(guild)
        assert record["props"]["host"] == "spam-site.com"
        assert record["props"]["reason"] == "blocked-no-rule"
        assert record["actor"] == {"kind": "user", "id": str(member.id)}
        assert record["props"]["deleted"] is True
        assert "ts" in record

    async def test_allowed_link_records_nothing(self, guild, channel, member):
        msg = create_message(channel, member, "https://youtube.com/watch?v=abc")

        await check_message(str(guild.id), msg)

        msg.assert_not_deleted()
        assert await recorded(guild) == []

    async def test_a_failed_deletion_is_recorded_as_not_deleted(
        self, guild, channel, member, monkeypatch
    ):
        """Faltou permissao de apagar mensagens: o registro precisa existir
        marcado como nao apagado, senao o dono do servidor nunca descobre."""
        msg = create_message(channel, member, "https://spam-site.com")
        monkeypatch.setattr(msg, "delete", AsyncMock(side_effect=RuntimeError("missing permissions")))

        with pytest.raises(RuntimeError):
            await check_message(str(guild.id), msg)

        [record] = await recorded(guild)
        assert record["props"]["deleted"] is False

    async def test_recording_never_breaks_the_moderation(
        self, guild, channel, member, monkeypatch
    ):
        """A escrita de auditoria e fail-soft: se o banco cair, a mensagem
        ainda tem que ser apagada."""
        msg = create_message(channel, member, "https://spam-site.com")
        monkeypatch.setattr(analytics, "record", MagicMock(side_effect=RuntimeError("mongo down")))

        await check_message(str(guild.id), msg)

        msg.assert_deleted()

    async def test_records_are_capped_per_message(self, guild, channel, member):
        links = " ".join(f"https://spam{index}.com" for index in range(9))
        msg = create_message(channel, member, links)

        await check_message(str(guild.id), msg)

        assert len(await recorded(guild)) == Commands.BLOCK_LINKS_EVENTS_MAX_PER_MESSAGE


CHECK = "block_links.ui.responses.check"
CHECK_ITEMS = ("feature", "exemptions", "links")


def _diagnosed_cog(**overrides):
    cog = {
        "enabled": True,
        "mode": "block_all",
        "allowed_roles": {"values": []},
        "allowed_chats": {"values": []},
        "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
        "custom_links": {"style": "composition", "values": []},
        "answer": "Nada de links por aqui!",
    }
    cog.update(overrides)
    return cog


def _drawn(evaluation, locale):
    """The value of each of the three items as the check draws them."""
    embed = link_check_embed(evaluation, locale)
    items = [field for field in embed.fields if field.name != Style.BLANK_FIELD_NAME]
    assert len(items) == len(CHECK_ITEMS), "the check draws exactly three items"
    return dict(zip(CHECK_ITEMS, (field.value for field in items)))


def _evaluate(content, cog=None, roles=(), channel="1"):
    return evaluate_message(
        MessageSubject(author_role_ids=tuple(roles), channel_id=channel, content=content),
        cog if cog is not None else _diagnosed_cog(),
        full=True,
    )


def _check_copy(*, would_block=True, feature_state="active"):
    """The check's copy as the facts choose it: its title and its feature line."""
    found, _ = i18n.resolved(CHECK, Response, {
        "block_links.facts.would_block": would_block,
        "global.facts.feature_state": feature_state,
        "block_links.facts.mode": "",
    })
    return found


def _check_line(field, line, locale, **values):
    return fill(ml(f"{CHECK}.fields.{field}.lines.{line}", locale=locale), **values)


def _picker_label(mode, locale):
    """The label the mode picker of the block links card shows for `mode`."""
    for step in registry.get("block_links").steps:
        if not isinstance(step, CardStep):
            continue
        for section in step.sections:
            if section.key == "mode":
                for option in section.choices():
                    if str(option.value) == mode:
                        return option.label.get(locale)
    raise AssertionError(f"the mode picker has no option {mode!r}")


class TestDiagnosticFields:
    @pytest.mark.parametrize("locale", ["pt-br", "en-us"])
    def test_always_answers_with_exactly_three_items(self, locale):
        fields = _drawn(
            _evaluate("https://spam.com"), locale
        )

        assert list(fields) == ["feature", "exemptions", "links"]
        for name, value in fields.items():
            assert value
            assert "block_links." not in value, (
                f"missing localization key leaked into the copy: {name}: {value}"
            )

    @pytest.mark.parametrize("locale", ["pt-br", "en-us"])
    def test_exemptions_item_reports_role_and_channel_either_way(self, locale):
        """Both lines show, the role's and the channel's, exempt or not."""
        exempt = _drawn(
            _evaluate(
                "https://spam.com",
                cog=_diagnosed_cog(allowed_roles={"values": ["77"]},
                                   allowed_chats={"values": ["1"]}),
                roles=("77",),
            ),
            locale,
        )["exemptions"]
        assert exempt.splitlines() == [
            _check_line("exemptions", "role_exempt", locale, roles="<@&77>"),
            _check_line("exemptions", "channel_exempt", locale, channel="<#1>"),
        ]

        plain = _drawn(_evaluate("https://spam.com"), locale)["exemptions"]
        assert plain.splitlines() == [
            _check_line("exemptions", "role_not_exempt", locale),
            _check_line("exemptions", "channel_not_exempt", locale),
        ]

    def test_link_item_names_the_rule_that_decided_each_link(self):
        value = _drawn(
            _evaluate("https://youtube.com/watch?v=1 e https://spam.com"), "pt-br"
        )["links"]

        assert value.splitlines() == [
            _check_line(
                "links", "allowed_by_popular", "pt-br",
                link="https://youtube.com/watch?v=1", rule="youtube.com",
            ),
            _check_line("links", "blocked_no_rule", "pt-br", link="https://spam.com"),
        ]

    def test_link_item_is_capped_and_announces_the_remainder(self):
        links = " ".join(f"https://spam{index}.com" for index in range(9))
        value = _drawn(_evaluate(links), "pt-br")["links"]

        assert value.count("\n") == 5, "5 links and the line of the rest"
        assert value.splitlines()[-1] == _check_line("links", "more", "pt-br", count=4)
        assert len(value) <= 1024, "the limit of an embed field"

    def test_paused_feature_is_reported_apart_from_missing_configuration(self):
        paused = _drawn(
            _evaluate("https://spam.com", cog=_diagnosed_cog(enabled=False)), "pt-br"
        )["feature"]
        missing = _drawn(
            _evaluate("https://spam.com", cog={}), "pt-br"
        )["feature"]

        assert paused != missing
        assert paused == _check_copy(feature_state="paused").fields["feature"].value.get(
            "pt-br"
        )
        assert missing == _check_copy(
            feature_state="not_configured"
        ).fields["feature"].value.get("pt-br")

    def test_mode_is_named_with_the_same_label_the_picker_shows(self):
        value = _drawn(_evaluate("https://spam.com"), "pt-br")["feature"]
        assert value == fill(
            ml(f"{CHECK}.fields.feature.value", locale="pt-br"),
            mode=_picker_label("block_all", "pt-br"),
        )


class TestSendLinkCheckMessage:
    @pytest.mark.asyncio
    async def test_answers_ephemerally_with_the_three_items(
        self, mock_cache, mongodb, guild, bot, channel, member, interaction
    ):
        mock_cache.return_value = _diagnosed_cog()
        message = create_message(channel, member, "olha https://spam.com")

        await send_link_check_message(interaction, message)

        sent = interaction.get_last_response()
        assert sent["type"] == "send_message"
        assert sent["ephemeral"] is True
        items = [field.name for field in sent["embed"].fields][:-1]
        assert len(items) == 3, "the three steps, then the closing line"
        assert sent["embed"].title == _check_copy(would_block=True).title.get("en-us")

    @pytest.mark.asyncio
    async def test_title_says_the_message_would_pass_when_nothing_is_blocked(
        self, mock_cache, mongodb, guild, bot, channel, member, interaction
    ):
        mock_cache.return_value = _diagnosed_cog()
        message = create_message(channel, member, "https://youtube.com/watch?v=1")

        await send_link_check_message(interaction, message)

        assert interaction.get_last_response()["embed"].title == ml(
            f"{CHECK}.title", locale="en-us"
        )
