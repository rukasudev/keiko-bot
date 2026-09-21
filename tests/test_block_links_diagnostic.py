"""Testes do diagnostico "Validar Bloqueio de Link" (app command de mensagem).

O comando existe para responder uma pergunta so: por que esse link foi (ou
nao foi) bloqueado. Ele mostra sempre 3 itens, e a resposta precisa bater com
o que a moderacao realmente faz.
"""
import pytest

from app.services.block_links import (
    MessageSubject,
    evaluate_message,
    parse_evaluation_to_fields,
    send_link_check_message,
)
from tests.mocks import create_message


def _cog(**overrides):
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


def _evaluate(content, cog=None, roles=(), channel="1"):
    return evaluate_message(
        MessageSubject(author_role_ids=tuple(roles), channel_id=channel, content=content),
        cog if cog is not None else _cog(),
        full=True,
    )


class TestDiagnosticFields:
    @pytest.mark.parametrize("locale", ["pt-br", "en-us"])
    def test_always_answers_with_exactly_three_items(self, locale):
        fields = parse_evaluation_to_fields(
            _evaluate("https://spam.com"), locale
        )

        assert len(fields) == 3
        for field in fields:
            assert field["title"] and field["value"]
            assert "commands.commands" not in field["value"], (
                f"missing localization key leaked into the copy: {field}"
            )

    @pytest.mark.parametrize("locale", ["pt-br", "en-us"])
    def test_exemptions_item_reports_role_and_channel_either_way(self, locale):
        """"Quais regras passou e quais nao passou": as duas linhas aparecem
        sempre, isentas ou nao."""
        exempt = parse_evaluation_to_fields(
            _evaluate(
                "https://spam.com",
                cog=_cog(allowed_roles={"values": ["77"]},
                         allowed_chats={"values": ["1"]}),
                roles=("77",),
            ),
            locale,
        )[1]["value"]
        assert exempt.count("\n") == 1, "uma linha de cargo e uma de canal"
        assert "<@&77>" in exempt
        assert "<#1>" in exempt

        plain = parse_evaluation_to_fields(_evaluate("https://spam.com"), locale)[1]["value"]
        assert plain.count("\n") == 1
        assert plain != exempt

    def test_link_item_names_the_rule_that_decided_each_link(self):
        value = parse_evaluation_to_fields(
            _evaluate("https://youtube.com/watch?v=1 e https://spam.com"), "pt-br"
        )[2]["value"]

        assert "youtube.com" in value
        assert "✅" in value and "🚫" in value

    def test_link_item_is_capped_and_announces_the_remainder(self):
        links = " ".join(f"https://spam{index}.com" for index in range(9))
        value = parse_evaluation_to_fields(_evaluate(links), "pt-br")[2]["value"]

        assert value.count("\n") == 5, "5 links + a linha do resto"
        assert "4" in value.split("\n")[-1]
        assert len(value) <= 1024, "limite de um campo de embed"

    def test_paused_feature_is_reported_apart_from_missing_configuration(self):
        paused = parse_evaluation_to_fields(
            _evaluate("https://spam.com", cog=_cog(enabled=False)), "pt-br"
        )[0]["value"]
        missing = parse_evaluation_to_fields(
            _evaluate("https://spam.com", cog={}), "pt-br"
        )[0]["value"]

        assert paused != missing
        assert "⏸️" in paused

    def test_mode_is_named_with_the_same_label_the_picker_shows(self):
        value = parse_evaluation_to_fields(_evaluate("https://spam.com"), "pt-br")[0]["value"]
        assert "Bloquear todos" in value


class TestSendLinkCheckMessage:
    @pytest.mark.asyncio
    async def test_answers_ephemerally_with_the_three_items(
        self, mock_cache, mongodb, guild, bot, channel, member, interaction
    ):
        mock_cache.return_value = _cog()
        message = create_message(channel, member, "olha https://spam.com")

        await send_link_check_message(interaction, message)

        sent = interaction._responses[-1]
        assert sent["type"] == "send_message"
        assert sent["ephemeral"] is True
        assert len(sent["embed"].fields) == 3
        assert "🚫" in sent["embed"].title

    @pytest.mark.asyncio
    async def test_title_says_the_message_would_pass_when_nothing_is_blocked(
        self, mock_cache, mongodb, guild, bot, channel, member, interaction
    ):
        mock_cache.return_value = _cog()
        message = create_message(channel, member, "https://youtube.com/watch?v=1")

        await send_link_check_message(interaction, message)

        assert "✅" in interaction._responses[-1]["embed"].title
