"""
Testes unitarios para FormStateManager.

Estes testes verificam a logica de navegacao, normalizacao de respostas
e preenchimento de views no fluxo de formulario.
"""

import pytest
from unittest.mock import MagicMock

from app.views.form_state import FormStateManager


class TestFormStateManagerNavigation:
    """Testes de navegacao entre steps."""

    def test_advance_increments_step_index(self):
        """
        Verifica que advance() incrementa o indice do step.

        Input: FormStateManager com 3 steps
        Output: step_index incrementa de -1 para 0, depois 1
        """
        steps = [{"key": "step1"}, {"key": "step2"}, {"key": "step3"}]
        manager = FormStateManager(steps)

        assert manager.step_index == -1
        assert manager.advance() is True
        assert manager.step_index == 0
        assert manager.advance() is True
        assert manager.step_index == 1

    def test_advance_returns_false_when_no_more_steps(self):
        """
        Verifica que advance() retorna False quando nao ha mais steps.

        Input: FormStateManager no ultimo step
        Output: advance() retorna False
        """
        steps = [{"key": "step1"}]
        manager = FormStateManager(steps)

        assert manager.advance() is True
        assert manager.advance() is False

    def test_go_back_decrements_step_index(self):
        """
        Verifica que go_back() decrementa o indice do step.

        Input: FormStateManager no step 2
        Output: step_index volta para 1
        """
        steps = [{"key": "step1", "action": "modal"}, {"key": "step2"}, {"key": "step3"}]
        manager = FormStateManager(steps)
        manager.advance()
        manager.advance()
        manager.advance()

        assert manager.step_index == 2
        assert manager.go_back() is True
        assert manager.step_index == 1

    def test_go_back_returns_false_at_first_step(self):
        """
        Verifica que go_back() retorna False no primeiro step.

        Input: FormStateManager no step 0
        Output: go_back() retorna False
        """
        steps = [{"key": "step1"}]
        manager = FormStateManager(steps)
        manager.advance()

        assert manager.go_back() is False

    def test_go_back_skips_form_action(self):
        """
        Verifica que go_back() nao permite voltar para step com action=form.

        Input: Step atual apos step com action=form
        Output: can_go_back retorna False
        """
        steps = [{"key": "form", "action": "form"}, {"key": "step2"}]
        manager = FormStateManager(steps)
        manager.advance()
        manager.advance()

        assert manager.can_go_back is False

    def test_current_step_returns_correct_step(self):
        """
        Verifica que current_step retorna o step atual.

        Input: FormStateManager no step 1
        Output: current_step retorna o segundo step
        """
        steps = [{"key": "step1"}, {"key": "step2"}]
        manager = FormStateManager(steps)
        manager.advance()
        manager.advance()

        assert manager.current_step["key"] == "step2"


class TestFormStateManagerNormalize:
    """Testes de normalizacao de respostas."""

    def test_normalize_string_splits_by_semicolon(self):
        """
        Verifica que string e normalizada para lista separando por ;

        Input: "value1;value2;value3"
        Output: ["value1", "value2", "value3"]
        """
        manager = FormStateManager([])
        result = manager._normalize("value1;value2;value3", {})

        assert result == ["value1", "value2", "value3"]

    def test_normalize_list_converts_to_strings(self):
        """
        Verifica que lista e normalizada convertendo itens para string.

        Input: [1, 2, 3]
        Output: ["1", "2", "3"]
        """
        manager = FormStateManager([])
        result = manager._normalize([1, 2, 3], {})

        assert result == ["1", "2", "3"]

    def test_normalize_dict_with_fields_preserves_order(self):
        """
        Verifica que dict com fields preserva a ordem dos campos.

        Input: {"field_a": "A", "field_b": "B"} com fields definindo ordem
        Output: ["A", "B"] na ordem dos fields
        """
        manager = FormStateManager([])
        step = {
            "fields": [
                {"key": "field_a"},
                {"key": "field_b"},
            ]
        }
        response = {"field_a": "A", "field_b": "B"}
        result = manager._normalize(response, step)

        assert result == ["A", "B"]

    def test_normalize_dict_with_concat_extracts_values(self):
        """
        Verifica que __concat__ e extraido corretamente.

        Input: {"__concat__": "val1;val2", "key1": "k1"} com fields mistos
        Output: Lista com valores na ordem correta
        """
        manager = FormStateManager([])
        step = {
            "fields": [
                {"key": "key1"},
                {},
                {},
            ]
        }
        response = {"key1": "k1", "__concat__": "val1;val2"}
        result = manager._normalize(response, step)

        assert result == ["k1", "val1", "val2"]

    def test_normalize_none_returns_empty_list(self):
        """
        Verifica que None retorna lista vazia.

        Input: None
        Output: []
        """
        manager = FormStateManager([])
        result = manager._normalize(None, {})

        assert result == []


class TestFormStateManagerFillModal:
    """Testes de preenchimento de modals."""

    def test_fill_modal_sets_defaults(self):
        """
        Verifica que fill_modal preenche os defaults dos children.

        Input: Modal com 3 children, previous_response com 3 valores
        Output: Cada child recebe seu valor correspondente
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()
        manager._previous_response = ["value1", "value2", "value3"]

        modal = MagicMock()
        modal.children = [MagicMock(), MagicMock(), MagicMock()]

        result = manager.fill_modal(modal)

        assert result is True
        assert modal.children[0].default == "value1"
        assert modal.children[1].default == "value2"
        assert modal.children[2].default == "value3"

    def test_fill_modal_returns_false_without_previous_response(self):
        """
        Verifica que fill_modal retorna False sem previous_response.

        Input: Modal sem previous_response
        Output: False, nenhum child modificado
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()

        modal = MagicMock()
        modal.children = [MagicMock()]

        result = manager.fill_modal(modal)

        assert result is False

    def test_fill_modal_handles_fewer_children_than_values(self):
        """
        Verifica que fill_modal lida com menos children que valores.

        Input: 2 children, 3 valores
        Output: Apenas os 2 primeiros valores sao usados
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()
        manager._previous_response = ["value1", "value2", "value3"]

        modal = MagicMock()
        modal.children = [MagicMock(), MagicMock()]

        result = manager.fill_modal(modal)

        assert result is True
        assert modal.children[0].default == "value1"
        assert modal.children[1].default == "value2"


class TestFormStateManagerFillSelect:
    """Testes de preenchimento de select views."""

    def test_fill_select_sets_response_dict(self):
        """
        Verifica que fill_select popula o response dict da view.

        Input: View com response dict, previous_response com valores
        Output: response dict preenchido
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()
        manager._previous_response = ["123", "456"]

        view = MagicMock()
        view.response = {}

        result = manager.fill_select(view)

        assert result is True
        assert view.response == {"123": "123", "456": "456"}

    def test_fill_select_sets_channel_default_values(self):
        """
        Verifica que fill_select configura default_values para ChannelSelect.

        Input: View com channel_select, previous_response com IDs
        Output: channel_select.default_values configurado
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()
        manager._previous_response = ["123"]

        view = MagicMock()
        view.response = {}
        view.channel_select = MagicMock()

        result = manager.fill_select(view)

        assert result is True
        assert len(view.channel_select.default_values) == 1
        assert view.channel_select.default_values[0].id == 123

    def test_fill_select_sets_role_default_values(self):
        """
        Verifica que fill_select configura default_values para RoleSelect.

        Input: View com role_select, previous_response com IDs
        Output: role_select.default_values configurado
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()
        manager._previous_response = ["456"]

        view = MagicMock()
        view.response = {}
        view.role_select = MagicMock()
        del view.channel_select

        result = manager.fill_select(view)

        assert result is True
        assert len(view.role_select.default_values) == 1
        assert view.role_select.default_values[0].id == 456


class TestFormStateManagerSaveResponse:
    """Testes de salvamento de respostas."""

    def test_save_response_stores_normalized_and_raw(self):
        """
        Verifica que save_response salva versao normalizada e raw.

        Input: Resposta string
        Output: responses_by_step e responses_by_step_raw preenchidos
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()

        manager.save_response("val1;val2", {"key": "test"})

        assert manager.responses_by_step[0] == ["val1", "val2"]
        assert manager.responses_by_step_raw[0] == "val1;val2"

    def test_go_back_restores_previous_response(self):
        """
        Verifica que go_back restaura previous_response do step anterior.

        Input: Dois steps com respostas salvas
        Output: Ao voltar, previous_response e restaurado
        """
        steps = [{"key": "step1", "action": "modal"}, {"key": "step2"}]
        manager = FormStateManager(steps)
        manager.advance()
        manager.save_response("response1", steps[0])
        manager.advance()

        manager.go_back()

        assert manager._previous_response == ["response1"]


class TestFormStateManagerDesignSelect:
    """Testes de preenchimento de design select."""

    def test_fill_design_select_sets_response(self):
        """
        Verifica que fill_design_select preenche o response da view.

        Input: View com response dict, previous_response com design key
        Output: response dict preenchido
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()
        manager._previous_response = ["custom_blur"]

        view = MagicMock()
        view.response = {}

        result = manager.fill_design_select(view)

        assert result is True
        assert view.response == {"custom_blur": "custom_blur"}


class TestFormStateManagerFileUpload:
    """Testes de preenchimento de file upload."""

    def test_fill_file_upload_sets_response(self):
        """
        Verifica que fill_file_upload preenche _response da view.

        Input: View com _response, previous_response com URL
        Output: _response preenchido com URL
        """
        manager = FormStateManager([{"key": "test"}])
        manager.advance()
        manager._previous_response = ["https://example.com/image.png"]

        view = MagicMock()
        view._response = None

        result = manager.fill_file_upload(view)

        assert result is True
        assert view._response == "https://example.com/image.png"
