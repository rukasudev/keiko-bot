"""
Testes unitarios para utils.py.

Estes testes NAO fazem I/O - apenas testam logica pura.
"""

import pytest
from unittest.mock import MagicMock, patch
from app.services.utils import (
    get_message_links,
    check_two_lists_intersection,
    list_roles_id,
    parse_welcome_messages,
    format_datetime_output,
    split_welcome_messages,
    get_styled_composition_values,
)
import app.services.reminders_birthdays  # noqa: F401
from app.data.birthdays import build_default_item
from app.services.reminders_birthdays import can_self_edit_birthday, get_self_edit_count
from datetime import date, timedelta


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


class TestBirthdayCompositionSummary:
    """Testes para renderizacao do resumo de aniversarios."""

    def test_formats_birthday_summary_without_auxiliary_month_or_image_url(self):
        values = [{
            "user": {"value": "123", "title": "Membro", "style": "user"},
            "month": {"value": "05", "title": "Mes do Aniversario"},
            "date": {"value": "05-15", "title": "Aniversario", "style": "birthday_date"},
            "use_custom_message": {"value": "custom", "title": "Mensagem"},
            "custom_message_title": {"value": "Parabens, {user}!", "title": "Titulo"},
            "custom_message_content": {"value": "Feliz aniversario!", "title": "Conteudo"},
            "use_custom_image": {"value": "custom", "title": "Imagem Personalizada"},
            "custom_image": {"value": "https://example.com/image.png", "title": "Imagem Personalizada"},
        }]

        result = get_styled_composition_values("Lista de Aniversarios", values, "pt-br")

        assert "Aniversario: **15 de maio**" in result
        assert "Mes do Aniversario" not in result
        assert "Mensagem: **Personalizado**" in result
        assert "> **Parabens, {user}!**" in result
        assert "> Feliz aniversario!" in result
        assert "Titulo:" not in result
        assert "Conteudo:" not in result
        assert "Imagem Personalizada: **Sim**" in result
        assert "https://example.com/image.png" not in result

    def test_hides_default_birthday_customization_fields(self):
        values = [{
            "user": {"value": "123", "title": "Member", "style": "user"},
            "date": {"value": "05-15", "title": "Birthday", "style": "birthday_date"},
            "use_custom_message": {"value": "default", "title": "Message"},
            "custom_message_title": {"value": None, "title": "Title"},
            "custom_message_content": {"value": None, "title": "Content"},
            "use_custom_image": {"value": "default", "title": "Custom Birthday Image"},
            "custom_image": {"value": None, "title": "Custom Birthday Image"},
        }]

        result = get_styled_composition_values("Birthday List", values, "en-us")

        assert "Birthday: **May 15**" in result
        assert "Message:" not in result
        assert "Title:" not in result
        assert "Content:" not in result
        assert "Custom Birthday Image:" not in result


class TestBirthdayItemDefaults:
    """Testes para o formato padrao salvo por cadastro inline/pessoal."""

    def test_build_default_item_uses_manager_compatible_styles(self):
        item = build_default_item("123", "05-15", "reminder-1")

        assert item["user_id"] == "123"
        assert item["date"] == "05-15"
        assert item["month"] == 5
        assert item["day"] == 15
        assert item["message"]["mode"] == "default"
        assert item["image"]["mode"] == "default"
        assert item["self_edit_count"] == 0

    def test_self_edit_limit_allows_only_first_overwrite(self):
        assert can_self_edit_birthday({"self_edit_count": 0}) is True
        assert can_self_edit_birthday({"self_edit_count": 1}) is False
        assert get_self_edit_count({"self_edit_count": "invalid"}) == 0

    def test_birthday_stats_from_items(self):
        from app.services.reminders_birthdays import get_birthday_stats

        items = [
            {"guild_id": "1", "user_id": "1", "date": "05-15", "month": 5},
            {"guild_id": "1", "user_id": "2", "date": "05-15", "month": 5},
            {"guild_id": "1", "user_id": "3", "date": "06-01", "month": 6},
        ]

        with patch("app.services.reminders_birthdays.birthdays_data.find_birthday_items_by_guild", return_value=items):
            stats = get_birthday_stats("1", today=date(2026, 5, 2))

        assert stats["total"] == 3
        assert stats["current_month"] == 2
        assert stats["max_month"] == (5, 2)
        assert stats["min_month"] == (6, 1)
        assert stats["max_date"] == ("05-15", 2)

    def test_reuses_existing_reminder_for_date(self):
        from app.services.reminders_birthdays import ensure_reminder_for_date

        with patch("app.services.reminders_birthdays.birthdays_data.find_reminder_id_by_date", return_value="rem-1"):
            with patch("app.services.reminders_birthdays.create_reminder_for_date") as create:
                assert ensure_reminder_for_date("05-15") == "rem-1"

        create.assert_not_called()

    def test_formats_boolean_style_as_scalar(self):
        from app.services.utils import format_values_by_style

        assert format_values_by_style(True, "boolean", "pt-br") == "Sim"
        assert format_values_by_style(False, "boolean", "pt-br") == "Não"

    def test_save_setup_form_writes_config_and_items(self):
        from app.services.reminders_birthdays import save_setup_form

        responses = [
            {"key": "channel", "value": "111", "style": "channel"},
            {"key": "mention_everyone", "value": "True"},
            {
                "key": "reminders_birthday",
                "value": [
                    {
                        "user": {"value": "222", "title": "Member", "style": "user"},
                        "date": {"value": "05-15", "title": "Birthday", "style": "birthday_date"},
                        "use_custom_message": {"value": "custom", "title": "Message"},
                        "custom_message_title": {"value": "Parabens", "title": "Title"},
                        "custom_message_content": {"value": "Feliz aniversario, {user}", "title": "Content"},
                        "use_custom_image": {"value": "default", "title": "Image"},
                    }
                ],
            },
        ]

        with patch("app.services.reminders_birthdays.setup_birthdays") as setup:
            with patch("app.services.reminders_birthdays.upsert_birthday", return_value={"user_id": "222"}) as upsert:
                saved = save_setup_form("guild-1", responses)

        setup.assert_called_once_with("guild-1", "111", True)
        upsert.assert_called_once_with(
            "guild-1",
            "222",
            "05-15",
            message={
                "mode": "custom",
                "title": "Parabens",
                "content": "Feliz aniversario, {user}",
            },
            image={"mode": "default", "url": None},
        )
        assert saved == [{"user_id": "222"}]

class TestSplitWelcomeMessages:
    """Testes para divisao de mensagens de boas-vindas."""

    def test_splits_by_semicolon(self):
        """
        Verifica que mensagens sao divididas por ponto e virgula.

        Input: String com tres mensagens separadas por ;
        Output: Lista com tres mensagens
        """
        # Arrange
        messages = "Msg1;Msg2;Msg3"

        # Act
        result = split_welcome_messages(messages)

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
        result = split_welcome_messages(messages)

        # Assert
        assert len(result) == 1
        assert result[0] == "Single message"


class TestParseWelcomeMessages:
    """Testes para formatacao de mensagens de boas-vindas."""

    @pytest.fixture
    def member(self):
        """Member mock para testes."""
        m = MagicMock()
        m._user = MagicMock()
        m._user.id = 123
        m.guild.name = "Test Server"
        m.guild.member_count = 100
        return m

    def test_replaces_user_placeholder_with_mention(self, member):
        """
        Verifica que o placeholder {user} e substituido pela mencao do membro.

        Input: Mensagem com {user} e um member com ID 123
        Output: Mensagem com "<@!123>" no lugar de {user}
        """
        # Arrange
        message = "Hello {user}!"

        # Act
        result = parse_welcome_messages(message, member)

        # Assert
        assert "<@!123>" in result

    def test_replaces_server_placeholder_with_guild_name(self, member):
        """
        Verifica que o placeholder {server} e substituido pelo nome do servidor.

        Input: Mensagem com {server} e um member de guild "Test Server"
        Output: Mensagem com "Test Server" no lugar de {server}
        """
        # Arrange
        message = "Welcome to {server}!"

        # Act
        result = parse_welcome_messages(message, member)

        # Assert
        assert "Test Server" in result

    def test_replaces_member_count_placeholder(self, member):
        """
        Verifica que o placeholder {member_count} e substituido pelo total de membros.

        Input: Mensagem com {member_count} e guild com 100 membros
        Output: Mensagem com "100" no lugar de {member_count}
        """
        # Arrange
        message = "We are {member_count} members!"

        # Act
        result = parse_welcome_messages(message, member)

        # Assert
        assert "100" in result

    def test_replaces_multiple_placeholders_in_same_message(self, member):
        """
        Verifica que multiplos placeholders sao substituidos na mesma mensagem.

        Input: Mensagem com {user}, {server} e {member_count}
        Output: Todos os placeholders substituidos corretamente
        """
        # Arrange
        message = "{user} joined {server}! Now we have {member_count} members!"

        # Act
        result = parse_welcome_messages(message, member)

        # Assert
        assert "<@!123>" in result
        assert "Test Server" in result
        assert "100" in result

    def test_adds_mention_when_no_user_placeholder(self, member):
        """
        Verifica que adiciona mencao automaticamente quando nao ha {user}.

        Input: Mensagem sem placeholder {user}
        Output: Mensagem com mencao adicionada ao final
        """
        # Arrange
        message = "Welcome to the server!"

        # Act
        result = parse_welcome_messages(message, member)

        # Assert
        assert "<@!123>" in result

    def test_selects_random_message_when_multiple_provided(self, member):
        """
        Verifica que escolhe uma mensagem aleatoria quando multiplas sao
        fornecidas separadas por ponto e virgula.

        Input: Tres mensagens separadas por ";"
        Output: Uma das mensagens e escolhida (com variacao)
        """
        # Arrange
        messages = "Msg1 {user};Msg2 {user};Msg3 {user}"

        # Act
        results = set()
        for _ in range(50):
            results.add(parse_welcome_messages(messages, member))

        # Assert
        assert len(results) > 1  # Deve ter variacao


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
