"""The helpers of app/core/utils.py: links, lists, message splitting, durations,
HMAC, `ml`/`parse_locale` agreeing with the form platform, who configures Keiko, a
DM and a re-posted upload, and a text kept inside a quote."""

import hashlib
import hmac
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from app.core.utils import (
    check_two_lists_intersection,
    format_datetime_output,
    get_message_links,
    inside_quote,
    list_roles_id,
    postable_file,
    quoted_lines,
    send_dm,
    split_messages,
    verify_hmac_sha256,
)


class TestGetMessageLinks:
    """Testes para extracao de links de mensagens."""

    def test_detects_https_link(self):
        """
        Verifica que links HTTPS sao detectados corretamente.

        Input: Mensagem contendo um link HTTPS
        Output: Lista contendo o link detectado
        """
        # Arrange
        content = "Check https://example.com"

        # Act
        links = get_message_links(content)

        # Assert
        assert len(links) == 1
        assert "example.com" in links[0]

    def test_detects_http_link(self):
        """
        Verifica que links HTTP (sem SSL) sao detectados.

        Input: Mensagem contendo um link HTTP
        Output: Lista contendo o link detectado
        """
        # Arrange
        content = "Old site http://old.com"

        # Act
        links = get_message_links(content)

        # Assert
        assert len(links) == 1
        assert "old.com" in links[0]

    def test_detects_link_with_path_and_query(self):
        """
        Verifica que links com path e query string sao detectados integralmente.

        Input: Mensagem com link contendo path e parametros
        Output: Link completo com path e query
        """
        # Arrange
        content = "https://site.com/path/to/page?query=1&other=2"

        # Act
        links = get_message_links(content)

        # Assert
        assert len(links) == 1
        assert "site.com" in links[0]
        assert "path" in links[0]

    def test_detects_multiple_links(self):
        """
        Verifica que multiplos links na mesma mensagem sao todos detectados.

        Input: Mensagem com dois links diferentes
        Output: Lista com ambos os links
        """
        # Arrange
        content = "Links: https://a.com and https://b.com"

        # Act
        links = get_message_links(content)

        # Assert
        assert len(links) == 2

    def test_returns_empty_when_no_links(self):
        """
        Verifica que mensagem sem links retorna lista vazia.

        Input: Mensagem de texto sem nenhum link
        Output: Lista vazia
        """
        # Arrange
        content = "No links here, just text."

        # Act
        links = get_message_links(content)

        # Assert
        assert len(links) == 0

    def test_handles_links_in_sentence(self):
        """
        Verifica que links em meio a frases sao detectados.

        Input: Mensagem com link no meio do texto
        Output: Link extraido corretamente
        """
        # Arrange
        content = "Veja esse video https://youtube.com/watch?v=123 que legal"

        # Act
        links = get_message_links(content)

        # Assert
        assert len(links) == 1
        assert "youtube.com" in links[0]

    def test_converts_to_lowercase(self):
        """
        Verifica que links sao convertidos para lowercase.

        Input: Link com letras maiusculas
        Output: Link em lowercase
        """
        # Arrange
        content = "Check HTTPS://EXAMPLE.COM/PATH"

        # Act
        links = get_message_links(content)

        # Assert
        assert len(links) == 1
        assert links[0] == links[0].lower()


class TestCheckTwoListsIntersection:
    """Testes para verificacao de intersecao de listas."""

    def test_returns_true_when_intersection_exists(self):
        """
        Verifica que retorna True quando listas tem elementos em comum.

        Input: Duas listas com elemento "b" em comum
        Output: True
        """
        # Arrange
        list_a = ["a", "b", "c"]
        list_b = ["b", "d", "e"]

        # Act
        result = check_two_lists_intersection(list_a, list_b)

        # Assert
        assert result is True

    def test_returns_false_when_no_intersection(self):
        """
        Verifica que retorna False quando listas nao tem elementos em comum.

        Input: Duas listas sem elementos em comum
        Output: False
        """
        # Arrange
        list_a = ["a", "b", "c"]
        list_b = ["d", "e", "f"]

        # Act
        result = check_two_lists_intersection(list_a, list_b)

        # Assert
        assert result is False

    def test_handles_empty_lists(self):
        """
        Verifica que retorna False quando uma das listas esta vazia.

        Input: Uma lista vazia e uma com elementos
        Output: False
        """
        # Arrange
        list_a = []
        list_b = ["a", "b"]

        # Act
        result = check_two_lists_intersection(list_a, list_b)

        # Assert
        assert result is False

    def test_handles_both_empty_lists(self):
        """
        Verifica que retorna False quando ambas as listas estao vazias.

        Input: Duas listas vazias
        Output: False
        """
        # Arrange
        list_a = []
        list_b = []

        # Act
        result = check_two_lists_intersection(list_a, list_b)

        # Assert
        assert result is False


class TestListRolesId:
    """Testes para conversao de roles em IDs."""

    def test_converts_roles_to_string_ids(self):
        """
        Verifica que roles sao convertidos para lista de IDs em string.

        Input: Lista de roles com IDs numericos
        Output: Lista de strings com os IDs
        """
        # Arrange
        role1 = MagicMock()
        role1.id = 123
        role2 = MagicMock()
        role2.id = 456
        roles = [role1, role2]

        # Act
        result = list_roles_id(roles)

        # Assert
        assert result == ["123", "456"]

    def test_handles_empty_list(self):
        """
        Verifica que lista vazia retorna lista vazia.

        Input: Lista vazia de roles
        Output: Lista vazia
        """
        # Arrange
        roles = []

        # Act
        result = list_roles_id(roles)

        # Assert
        assert result == []


class TestSplitJoinedMessages:
    """Testes para divisao de mensagens salvas como texto separado por ;."""

    def test_splits_by_semicolon(self):
        """
        Verifica que mensagens sao divididas por ponto e virgula.

        Input: String com tres mensagens separadas por ;
        Output: Lista com tres mensagens
        """
        # Arrange
        messages = "Msg1;Msg2;Msg3"

        # Act
        result = split_messages(messages)

        # Assert
        assert len(result) == 3
        assert result[0] == "Msg1"
        assert result[1] == "Msg2"
        assert result[2] == "Msg3"

    def test_handles_single_message(self):
        """
        Verifica que mensagem unica retorna lista com um elemento.

        Input: String com apenas uma mensagem
        Output: Lista com um elemento
        """
        # Arrange
        messages = "Single message"

        # Act
        result = split_messages(messages)

        # Assert
        assert len(result) == 1
        assert result[0] == "Single message"


class TestSplitMessages:
    """Testes para as mensagens de um valor salvo, lista ou separado por ;."""

    @pytest.mark.parametrize(
        "stored",
        [["Msg1", " Msg2"], ("Msg1", "Msg2"), "Msg1; Msg2", "Msg1;;Msg2;"],
        ids=["list", "tuple", "joined", "blanks"],
    )
    def test_every_stored_shape_reads_as_its_messages(self, stored):
        assert split_messages(stored) == ["Msg1", "Msg2"]

    @pytest.mark.parametrize("stored", [None, "", [], [" "]])
    def test_nothing_saved_reads_as_no_message(self, stored):
        assert split_messages(stored) == []


class TestFormatDatetimeOutput:
    """Testes para formatacao de duracao."""

    def test_formats_seconds_only(self):
        """
        Verifica formatacao de apenas segundos.

        Input: Duracao de 45 segundos
        Output: "45s"
        """
        # Arrange
        delta = timedelta(seconds=45)

        # Act
        result = format_datetime_output(delta)

        # Assert
        assert result == "45s"

    def test_formats_minutes_and_seconds(self):
        """
        Verifica formatacao de minutos e segundos.

        Input: Duracao de 5 minutos e 30 segundos
        Output: "5m 30s"
        """
        # Arrange
        delta = timedelta(minutes=5, seconds=30)

        # Act
        result = format_datetime_output(delta)

        # Assert
        assert result == "5m 30s"

    def test_formats_hours_minutes_seconds(self):
        """
        Verifica formatacao de horas, minutos e segundos.

        Input: Duracao de 2 horas, 30 minutos e 15 segundos
        Output: "2h 30m 15s"
        """
        # Arrange
        delta = timedelta(hours=2, minutes=30, seconds=15)

        # Act
        result = format_datetime_output(delta)

        # Assert
        assert result == "2h 30m 15s"

    def test_formats_days_hours_minutes(self):
        """
        Verifica formatacao de dias, horas e minutos.

        Input: Duracao de 1 dia, 5 horas e 30 minutos
        Output: "1d 5h 30m"
        """
        # Arrange
        delta = timedelta(days=1, hours=5, minutes=30)

        # Act
        result = format_datetime_output(delta)

        # Assert
        assert result == "1d 5h 30m"

    def test_formats_zero_duration(self):
        """
        Verifica formatacao de duracao zero.

        Input: Duracao de 0 segundos
        Output: "0s"
        """
        # Arrange
        delta = timedelta(seconds=0)

        # Act
        result = format_datetime_output(delta)

        # Assert
        assert result == "0s"


class TestEnsureList:
    """ensure_list coerces the scalar-collapsed {style, values} envelopes."""

    def test_none_becomes_empty_list(self):
        from app.core.utils import ensure_list

        assert ensure_list(None) == []

    def test_scalar_is_wrapped(self):
        from app.core.utils import ensure_list

        assert ensure_list("201") == ["201"]

    def test_list_is_returned_as_list(self):
        from app.core.utils import ensure_list

        assert ensure_list(["a", "b"]) == ["a", "b"]

    def test_tuple_and_set_become_lists(self):
        from app.core.utils import ensure_list

        assert ensure_list(("a",)) == ["a"]
        assert sorted(ensure_list({"a", "b"})) == ["a", "b"]


class TestGetMessageLinksSchemeless:
    """Conservative schemeless detection: www.* hosts and domain.tld/path."""

    def test_detects_www_host_without_scheme(self):
        assert get_message_links("olha www.spam-site.com ai") == ["www.spam-site.com"]

    def test_detects_bare_domain_with_path(self):
        assert get_message_links("entra em discord.gg/abc123") == ["discord.gg/abc123"]

    def test_bare_domain_without_path_is_ignored(self):
        assert get_message_links("gosto de youtube.com e pronto") == []

    def test_common_dotted_words_are_not_links(self):
        assert get_message_links("uso node.js e li o package.json na v1.2.3") == []

    def test_trailing_punctuation_is_trimmed(self):
        assert get_message_links("corre em bit.ly/promo!") == ["bit.ly/promo"]

    def test_email_is_not_a_link(self):
        assert get_message_links("fala com a gente em contato@site.com") == []

    def test_http_link_is_not_double_extracted(self):
        assert get_message_links("veja https://youtube.com/watch?v=a") == [
            "https://youtube.com/watch?v=a"
        ]


class TestVerifyHmacSha256:
    def test_accepts_the_prefixed_digest_and_refuses_any_other(self):
        signature = "sha256=" + hmac.new(b"secret", b"body", hashlib.sha256).hexdigest()

        assert verify_hmac_sha256("secret", "body", signature, prefix="sha256=")
        assert not verify_hmac_sha256("other", "body", signature, prefix="sha256=")
        assert not verify_hmac_sha256("secret", "body", signature)


class TestLocaleCopyAgreesWithThePlatform:
    """`ml` and `parse_locale` here, and `text` and `normalize_locale` in the form
    platform, are two copies of one rule: the platform imports only itself and
    `app.constants`, and this module, which the logger imports at boot, cannot import
    the platform, whose package pulls in the data layer before the Mongo client
    exists. Guaranteed: both copies give the same answer, so they never drift apart."""

    @pytest.mark.parametrize(
        "locale",
        [
            "en-us",
            "en-US",
            "pt-br",
            "pt-BR",
            "pt",
            "en",
            "fr",
            "es-ES",
            "",
            "xx-yy",
            discord.Locale.brazil_portuguese,
            discord.Locale.american_english,
        ],
    )
    def test_parse_locale_maps_a_locale_the_way_the_platform_does(self, locale):
        from app.core.utils import parse_locale
        from app.forms.form.copy import normalize_locale

        assert parse_locale(locale) == normalize_locale(locale)

    @pytest.mark.parametrize(
        "key",
        [
            "texts.notices.closed",
            "texts.admin_logs.daily_export",
            "texts.no-such-key",
        ],
    )
    @pytest.mark.parametrize("locale", ["en-us", "pt-br", "pt", "fr"])
    def test_ml_reads_a_key_the_way_the_platform_does(self, key, locale):
        from app.core.utils import ml
        from app.forms.form.copy import text

        assert ml(key, locale) == text(key, locale)


class TestWhoConfiguresKeiko:
    """Whoever may set Keiko up in a server: an Administrator, or a member with Manage
    Server (a moderator the owner trusts with the server's settings)."""

    @pytest.mark.parametrize(
        ("permissions", "configures"),
        [
            pytest.param(
                discord.Permissions(administrator=True), True, id="administrator"
            ),
            pytest.param(
                discord.Permissions(manage_guild=True), True, id="manage-server"
            ),
            pytest.param(discord.Permissions.all(), True, id="every-permission"),
            pytest.param(
                discord.Permissions(manage_messages=True), False, id="moderator"
            ),
            pytest.param(discord.Permissions.none(), False, id="member"),
        ],
    )
    def test_administrator_or_manage_server_configures_keiko(
        self, permissions, configures
    ):
        from app.core.utils import is_guild_admin

        member = MagicMock(guild_permissions=permissions)

        assert is_guild_admin(member) is configures

    def test_a_user_outside_a_server_configures_nothing(self):
        from types import SimpleNamespace

        from app.core.utils import is_guild_admin

        assert is_guild_admin(SimpleNamespace(id=1)) is False


def _refused(status=403, code=50007):
    response = SimpleNamespace(status=status, reason="Forbidden")
    return discord.Forbidden(response, {"code": code, "message": "Cannot send"})


class TestDirectMessagesAndUploads:
    """A DM Keiko sends says whether it arrived, and a file someone uploaded is posted
    again only when the channel takes it."""

    async def test_a_dm_says_whether_it_reached_the_user(self):
        sent = []

        async def send(*, embed):
            sent.append(embed)

        async def refuse(*, embed):
            raise _refused()

        embed = discord.Embed(title="t")

        assert await send_dm(SimpleNamespace(send=send), embed) is True
        assert sent == [embed]
        assert await send_dm(SimpleNamespace(send=refuse), embed) is False
        assert await send_dm(None, embed) is False

    async def test_a_dm_carries_the_view_it_is_given(self):
        sent = []

        async def send(**kwargs):
            sent.append(kwargs)

        embed = discord.Embed(title="t")
        view = discord.ui.View()

        assert await send_dm(SimpleNamespace(send=send), embed, view=view) is True
        assert sent == [{"embed": embed, "view": view}]

    async def test_an_upload_is_posted_again_only_within_the_channels_cap(self):
        async def read():
            return b"picture"

        async def unreadable():
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "")

        channel = SimpleNamespace(guild=SimpleNamespace(filesize_limit=10))
        upload = SimpleNamespace(filename="print.png", size=7, read=read)

        file = await postable_file(upload, channel)

        assert file is not None and file.filename == "print.png"
        assert file.fp.read() == b"picture"
        assert (
            await postable_file(
                SimpleNamespace(**{**vars(upload), "size": 11}), channel
            )
            is None
        )
        gone = SimpleNamespace(**{**vars(upload), "read": unreadable})
        assert await postable_file(gone, channel) is None
        assert await postable_file(None, channel) is None


class TestInsideAQuote:
    """A text written after `> ` in a copy stays inside the quote on every line, and
    reads back whole from the quote."""

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("Em qual servidor?", id="one-line"),
            pytest.param("Em qual servidor?\nE qual canal?", id="two-lines"),
            pytest.param("Primeiro\n\nDepois de uma linha vazia", id="a-blank-line"),
        ],
    )
    def test_every_line_stays_inside_the_quote_and_reads_back_whole(self, text):
        written = f"Minha equipe quer saber:\n\n> {inside_quote(text)}\n\nToque abaixo"

        quoted = [line for line in written.splitlines() if line.startswith("> ")]

        assert len(quoted) == len(text.splitlines())
        assert quoted_lines(written) == text
