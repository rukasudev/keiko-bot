"""
Testes unitarios para Form.

Estes testes verificam a logica de salvamento de respostas e
avaliacao de condicoes no fluxo de formulario.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.views.form import Form
from app.constants import FormConstants as constants


class TestFormUpsertResponse:
    """Testes para _upsert_response - atualiza ou adiciona respostas."""

    def test_upsert_appends_new_response(self):
        """
        Verifica que nova resposta e adicionada quando key nao existe.

        Input: responses vazio, nova resposta
        Output: resposta adicionada a lista
        """
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "en-US")
            form.responses = []

            form._upsert_response({"key": "new_key", "value": "new_value"})

            assert len(form.responses) == 1
            assert form.responses[0]["key"] == "new_key"
            assert form.responses[0]["value"] == "new_value"

    def test_upsert_updates_existing_response(self):
        """
        Verifica que resposta existente e atualizada quando key ja existe.

        Input: responses com key existente, nova resposta com mesma key
        Output: resposta atualizada, nao duplicada
        """
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "en-US")
            form.responses = [
                {"key": "existing_key", "value": "old_value", "_raw_value": "old_raw"}
            ]

            form._upsert_response({"key": "existing_key", "value": "new_value", "_raw_value": "new_raw"})

            assert len(form.responses) == 1
            assert form.responses[0]["key"] == "existing_key"
            assert form.responses[0]["value"] == "new_value"
            assert form.responses[0]["_raw_value"] == "new_raw"

    def test_upsert_preserves_other_responses(self):
        """
        Verifica que outras respostas nao sao afetadas ao atualizar uma.

        Input: responses com multiplas keys, atualiza uma delas
        Output: apenas a key especifica e atualizada
        """
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "en-US")
            form.responses = [
                {"key": "key1", "value": "value1"},
                {"key": "key2", "value": "value2"},
                {"key": "key3", "value": "value3"},
            ]

            form._upsert_response({"key": "key2", "value": "updated_value2"})

            assert len(form.responses) == 3
            assert form.responses[0]["value"] == "value1"
            assert form.responses[1]["value"] == "updated_value2"
            assert form.responses[2]["value"] == "value3"


class TestFormShouldSkipStep:
    """Testes para _should_skip_step - avaliacao de condicoes."""

    def test_skip_step_uses_latest_response_value(self):
        """
        Verifica que condicao usa valor da resposta atualizada.

        Cenario do bug:
        1. Usuario seleciona custom_blur (requer file_upload)
        2. Fecha modal e volta
        3. Seleciona server_blur (nao requer file_upload)
        4. _should_skip_step deve usar server_blur, nao custom_blur

        Input: Resposta atualizada de custom_blur para server_blur
        Output: Step file_upload e pulado (server_blur in not_in)
        """
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "en-US")
            form._step = {
                "action": "file_upload",
                "key": "welcome_custom_image",
                "condition": {
                    "key": "welcome_design",
                    "not_in": ["server_blur"]
                }
            }

            form.responses = [
                {"key": "welcome_design", "value": "Server Image", "_raw_value": "server_blur"}
            ]

            should_skip = form._should_skip_step()

            assert should_skip is True

    def test_does_not_skip_when_condition_not_met(self):
        """
        Verifica que step nao e pulado quando condicao nao e atendida.

        Input: Resposta custom_blur, condition not_in: [server_blur]
        Output: Step NAO e pulado
        """
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "en-US")
            form._step = {
                "action": "file_upload",
                "key": "welcome_custom_image",
                "condition": {
                    "key": "welcome_design",
                    "not_in": ["server_blur"]
                }
            }

            form.responses = [
                {"key": "welcome_design", "value": "Custom with Blur", "_raw_value": "custom_blur"}
            ]

            should_skip = form._should_skip_step()

            assert should_skip is False


class TestFormUpdateResume:
    """Testes para update_resume - atualizacao de respostas apos edicao."""

    @pytest.mark.asyncio
    async def test_update_resume_copies_raw_value(self):
        """
        Verifica que update_resume copia _raw_value alem de value.

        Cenario:
        1. Usuario configura welcome_design = custom_blur
        2. Usuario edita e muda para server_blur
        3. update_resume deve copiar tanto value quanto _raw_value

        Sem o fix, _raw_value ficaria stale e causaria problemas
        em edicoes subsequentes.
        """
        from unittest.mock import AsyncMock

        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "en-US")
            form.responses = [
                {"key": "welcome_design", "value": "Custom with Blur", "_raw_value": "custom_blur"}
            ]

            edited_form = MagicMock()
            edited_form.responses = [
                {"key": "welcome_design", "value": "Server Image", "_raw_value": "server_blur"}
            ]
            form.edited_form_view = edited_form

            with patch.object(form, 'show_resume', new_callable=AsyncMock):
                await form.update_resume(MagicMock())

            assert form.responses[0]["value"] == "Server Image"
            assert form.responses[0]["_raw_value"] == "server_blur"


class TestFormDesignSelectReselection:
    """Testes para cenario de re-selecao de design."""

    def test_reselecting_design_updates_response_not_appends(self):
        """
        Verifica que re-selecionar design atualiza resposta existente.

        Cenario:
        1. Usuario seleciona custom_blur
        2. Fecha modal e volta para design_select
        3. Seleciona server_blur
        4. responses deve ter apenas UMA entrada para welcome_design

        Este teste garante que nao ha duplicacao de respostas.
        """
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[
            {"key": "form", "action": "form", "title": {"en-US": "Test"}, "description": {"en-US": "Test"}},
            {"key": "welcome_design", "action": "design_select", "title": {"en-US": "Design"}, "description": {"en-US": "Choose"},
             "designs": [
                 {"key": "server_blur", "label": {"en-US": "Server Image"}},
                 {"key": "custom_blur", "label": {"en-US": "Custom with Blur"}},
             ]},
        ]):
            form = Form("test_command", "en-US")
            form._step = form.state.steps_list[1]
            form.locale = "en-US"

            mock_view = MagicMock()
            mock_view.get_response.return_value = "custom_blur"
            form.view = mock_view

            form._save_step_response()
            assert len(form.responses) == 1
            assert form.responses[0]["_raw_value"] == "custom_blur"

            mock_view.get_response.return_value = "server_blur"
            form._save_step_response()

            assert len(form.responses) == 1
            assert form.responses[0]["_raw_value"] == "server_blur"
            assert form.responses[0]["value"] == "Server Image"
