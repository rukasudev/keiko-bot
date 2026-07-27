"""
Testes unitarios para Form.

Estes testes verificam a logica de salvamento de respostas e
avaliacao de condicoes no fluxo de formulario.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.views.form import Form
from app.constants import FormConstants as constants
from app.services.compositions import merge_composition_item_by_nested_value


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


class TestFormOptions:
    def test_get_options_localizes_structured_labels(self):
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "pt-br")
            form._step = {
                "key": "mention_everyone",
                "action": constants.OPTIONS_ACTION_KEY,
                "options": [
                    {"label": {"en-us": "Yes", "pt-br": "Sim"}, "value": True},
                    {"label": {"en-us": "No", "pt-br": "Não"}, "value": False},
                ],
            }

            assert form._get_options() == {"Sim": True, "Não": False}


class TestFormSummaryCard:
    """Testes para salvamento e recuperacao da summary card de aniversarios."""

    def test_summary_card_response_is_saved_as_form_responses(self):
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "pt-br")
            form._step = {"key": "customizations", "action": constants.SUMMARY_CARD_ACTION_KEY}
            form.view = MagicMock()
            form.view.get_response.return_value = {
                "use_custom_message": "custom",
                "custom_message_title": "Parabens, {user}!",
                "custom_message_content": "Feliz aniversario!",
                "use_custom_image": "custom",
                "custom_image": "https://example.com/image.png",
            }

            form._save_step_response()

            responses = {item["key"]: item for item in form.responses}
            assert responses["use_custom_message"]["value"] == "custom"
            assert responses["custom_message_title"]["value"] == "Parabens, {user}!"
            assert responses["custom_message_content"]["value"] == "Feliz aniversario!"
            assert responses["use_custom_image"]["value"] == "custom"
            assert responses["custom_image"]["value"] == "https://example.com/image.png"

    def test_summary_card_sections_hide_internal_fields_in_resume(self):
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "pt-br")
            form._step = {
                "key": "customizations",
                "action": constants.SUMMARY_CARD_ACTION_KEY,
                "sections": [
                    {
                        "label": {"en-us": "Message", "pt-br": "Mensagem"},
                        "state": {
                            "mode": "use_custom_message",
                            "title": "custom_message_title",
                            "content": "custom_message_content",
                        },
                    },
                    {
                        "label": {"en-us": "Image", "pt-br": "Imagem"},
                        "state": {
                            "mode": "use_custom_image",
                            "url": "custom_image",
                        },
                    },
                ],
            }
            form.view = MagicMock()
            form.view.get_response.return_value = {
                "use_custom_message": "default",
                "custom_message_title": None,
                "custom_message_content": None,
                "use_custom_image": "default",
                "custom_image": None,
            }

            form._save_step_response()

            responses = {item["key"]: item for item in form.responses}
            assert responses["use_custom_message"]["title"] == "Mensagem"
            assert responses["use_custom_message"]["hidden"] is True
            assert responses["custom_message_title"]["hidden"] is True
            assert responses["custom_message_content"]["hidden"] is True
            assert responses["use_custom_image"]["title"] == "Imagem"
            assert responses["use_custom_image"]["hidden"] is True
            assert responses["custom_image"]["hidden"] is True

    def test_summary_card_prior_state_reads_nested_response_values(self):
        from app.views.summary_card import prior_state_from_form

        keys = [
            "use_custom_message",
            "custom_message_title",
            "custom_message_content",
            "use_custom_image",
            "custom_image",
        ]
        responses = [
            {"key": "use_custom_message", "value": {"value": "custom"}},
            {"key": "custom_message_title", "value": {"value": "Titulo salvo"}},
            {"key": "custom_message_content", "value": {"value": "Conteudo salvo"}},
            {"key": "use_custom_image", "value": {"value": "custom"}},
            {"key": "custom_image", "value": {"value": "https://example.com/saved.png"}},
        ]

        state = prior_state_from_form(responses, cogs=None, keys=keys)

        assert state["use_custom_message"] == "custom"
        assert state["custom_message_title"] == "Titulo salvo"
        assert state["custom_message_content"] == "Conteudo salvo"
        assert state["use_custom_image"] == "custom"
        assert state["custom_image"] == "https://example.com/saved.png"

    def test_configuration_card_response_is_saved_as_form_responses(self):
        with patch('app.views.form.parse_form_yaml_to_dict', return_value=[{"key": "test", "action": "form"}]):
            form = Form("test_command", "pt-br")
            form._step = {
                "key": "config",
                "action": constants.CONFIGURATION_CARD_ACTION_KEY,
                "sections": [
                    {
                        "label": {"en-us": "Channel", "pt-br": "Canal"},
                        "style": "channel",
                        "state": {"value": "channel"},
                    },
                    {
                        "label": {"en-us": "Mention", "pt-br": "Menção"},
                        "style": "boolean",
                        "state": {"value": "mention_everyone"},
                    },
                ],
            }
            form.view = MagicMock()
            form.view.get_response.return_value = {
                "channel": "123",
                "mention_everyone": True,
            }

            form._save_step_response()

            responses = {item["key"]: item for item in form.responses}
            assert responses["channel"]["title"] == "Canal"
            assert responses["channel"]["style"] == "channel"
            assert responses["mention_everyone"]["title"] == "Menção"
            assert responses["mention_everyone"]["style"] == "boolean"

    def test_configuration_card_fields_are_listed_in_first_form_embed(self):
        steps = [
            {
                "key": "form",
                "action": constants.FORM_ACTION_KEY,
                "title": {"en-us": "Setup", "pt-br": "Setup"},
                "description": {"en-us": "Start", "pt-br": "Inicio"},
            },
            {
                "key": "config",
                "action": constants.CONFIGURATION_CARD_ACTION_KEY,
                "title": {"en-us": "Config", "pt-br": "Config"},
                "description": {"en-us": "Configure", "pt-br": "Configure"},
                "fields": [
                    {
                        "key": "channel",
                        "label": {"en-us": "Channel", "pt-br": "Canal"},
                        "description": {"en-us": "Pick a channel", "pt-br": "Escolha um canal"},
                    },
                    {
                        "key": "timezone",
                        "label": {"en-us": "Timezone", "pt-br": "Timezone"},
                        "description": {"en-us": "Pick a timezone", "pt-br": "Escolha um timezone"},
                    },
                ],
            },
            {
                "key": "items",
                "action": constants.COMPOSITION_ACTION_KEY,
                "title": {"en-us": "Items", "pt-br": "Itens"},
                "description": {"en-us": "Items", "pt-br": "Itens"},
                "condition": {"key": "register_now", "not_in": [False]},
                "steps": [
                    {
                        "key": "member",
                        "action": constants.USER_SELECT_ACTION_KEY,
                        "title": {"en-us": "Member", "pt-br": "Membro"},
                        "description": {"en-us": "Pick member", "pt-br": "Escolha membro"},
                    }
                ],
            },
        ]

        with patch('app.views.form.parse_form_yaml_to_dict', return_value=steps):
            form = Form("test_command", "pt-br")

        assert form.get_form_titles_and_descriptions() == {
            "Canal": "Escolha um canal",
            "Timezone": "Escolha um timezone",
        }

    def test_configuration_card_builder_uses_step_description(self):
        from types import SimpleNamespace
        from app.views.summary_card import build_configuration_card_from_step

        form = SimpleNamespace(
            locale="pt-br",
            responses=[],
            cogs={},
            state=SimpleNamespace(can_go_back=False),
            _go_back=None,
            _callback=None,
        )
        step = {
            "description": {"en-us": "Configure settings", "pt-br": "Configure as opções"},
            "header": {
                "title": {"en-us": "Settings", "pt-br": "Configurações"},
                "title-emoji": "🎂",
                "lines": [],
            },
            "sections": [],
        }

        view = build_configuration_card_from_step(step, form, SimpleNamespace())

        assert view.config.header.description == "Configure as opções"


class TestBirthdayCompositionMerge:
    """Testes para unicidade da lista de aniversarios por usuario."""

    def test_add_same_user_replaces_existing_item(self):
        items = [
            {
                "user": {"value": "123", "title": "Membro"},
                "date": {"value": "05-15", "title": "Aniversario"},
            }
        ]
        new_item = {
            "user": {"value": "123", "title": "Membro"},
            "date": {"value": "06-20", "title": "Aniversario"},
        }

        merge_composition_item_by_nested_value(items, new_item, "user")

        assert len(items) == 1
        assert items[0]["user"]["value"] == "123"
        assert items[0]["date"]["value"] == "06-20"

    def test_edit_user_to_existing_user_overwrites_existing_and_removes_old_slot(self):
        items = [
            {
                "user": {"value": "rukasu", "title": "Membro"},
                "date": {"value": "05-15", "title": "Aniversario"},
            },
            {
                "user": {"value": "keiko", "title": "Membro"},
                "date": {"value": "07-10", "title": "Aniversario"},
            },
        ]
        edited_item = {
            "user": {"value": "rukasu", "title": "Membro"},
            "date": {"value": "08-25", "title": "Aniversario"},
        }

        merge_composition_item_by_nested_value(items, edited_item, "user", edited_index=1)

        assert len(items) == 1
        assert items[0]["user"]["value"] == "rukasu"
        assert items[0]["date"]["value"] == "08-25"

    def test_edit_user_without_duplicate_replaces_same_index(self):
        items = [
            {
                "user": {"value": "rukasu", "title": "Membro"},
                "date": {"value": "05-15", "title": "Aniversario"},
            },
            {
                "user": {"value": "keiko", "title": "Membro"},
                "date": {"value": "07-10", "title": "Aniversario"},
            },
        ]
        edited_item = {
            "user": {"value": "luna", "title": "Membro"},
            "date": {"value": "08-25", "title": "Aniversario"},
        }

        merge_composition_item_by_nested_value(items, edited_item, "user", edited_index=1)

        assert len(items) == 2
        assert items[0]["user"]["value"] == "rukasu"
        assert items[1]["user"]["value"] == "luna"
        assert items[1]["date"]["value"] == "08-25"
