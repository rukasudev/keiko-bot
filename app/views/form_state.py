from typing import Any, Dict, List

import discord
from discord import SelectDefaultValue, SelectDefaultValueType


class FormStateManager:
    """Gerencia o estado de navegação e respostas do form flow."""

    def __init__(self, steps: List[Dict[str, Any]]):
        self.steps_list = steps
        self.step_index = -1
        self.responses_by_step: Dict[int, List[str]] = {}
        self.responses_by_step_raw: Dict[int, Any] = {}
        self._previous_response: List[str] = None
        self._previous_response_raw: Any = None

    @property
    def current_step(self) -> Dict[str, Any]:
        return self.steps_list[self.step_index]

    @property
    def can_go_back(self) -> bool:
        if self.step_index <= 0:
            return False
        prev_step = self.steps_list[self.step_index - 1]
        return prev_step.get("action") != "form"

    def advance(self) -> bool:
        """Avança para o próximo step. Retorna False se não houver mais steps."""
        self.step_index += 1
        return self.step_index < len(self.steps_list)

    def go_back(self) -> bool:
        """Volta para o step anterior. Retorna False se já estiver no primeiro."""
        if not self.can_go_back:
            return False
        self.step_index -= 1
        self._previous_response = self.responses_by_step.get(self.step_index)
        self._previous_response_raw = self.responses_by_step_raw.get(self.step_index)
        return True

    def save_response(self, response: Any, step: Dict[str, Any]) -> None:
        """Salva a resposta normalizada como lista ordenada."""
        self.responses_by_step[self.step_index] = self._normalize(response, step)
        self.responses_by_step_raw[self.step_index] = response

    def clear_previous_response(self) -> None:
        self._previous_response = None
        self._previous_response_raw = None

    def _normalize(self, response: Any, step: Dict) -> List[str]:
        """Converte qualquer response para lista ordenada de valores."""
        if response is None:
            return []
        if isinstance(response, str):
            return response.split(";")
        if isinstance(response, list):
            return [str(v) for v in response]
        if not isinstance(response, dict):
            return []

        fields = step.get("fields", [])
        if not fields:
            return list(response.values())

        concat = iter(response.get("__concat__", "").split(";")) if response.get("__concat__") else iter([])

        return [
            response.get(f["key"], "") if f.get("key") else next(concat, "")
            for f in fields
        ]

    def fill_modal(self, view: discord.ui.Modal) -> bool:
        """Preenche modal com resposta anterior. Retorna True se preencheu."""
        if not self._previous_response:
            return False
        for i, value in enumerate(self._previous_response):
            if i < len(view.children):
                view.children[i].default = value
        return True

    def fill_options(self, view: discord.ui.View) -> bool:
        """Preenche OptionsView com resposta anterior. Retorna True se preencheu."""
        if not self._previous_response:
            return False
        for item in view.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if getattr(item, "custom_id", None) in ("prev_page", "next_page"):
                continue
            if item.label in self._previous_response or item.custom_id in self._previous_response:
                item.style = discord.ButtonStyle.primary
                view.response[item.custom_id] = item.label
        return True

    def fill_select(self, view: discord.ui.View) -> bool:
        """Preenche SelectView com resposta anterior. Retorna True se preencheu."""
        if not self._previous_response:
            return False
        for value in self._previous_response:
            view.response[value] = value

        if hasattr(view, 'channel_select') and self._previous_response:
            view.channel_select.default_values = [
                SelectDefaultValue(id=int(v), type=SelectDefaultValueType.channel)
                for v in self._previous_response if v
            ]
        elif hasattr(view, 'role_select') and self._previous_response:
            view.role_select.default_values = [
                SelectDefaultValue(id=int(v), type=SelectDefaultValueType.role)
                for v in self._previous_response if v
            ]

        return True

    def fill_multi_select(self, view: discord.ui.View) -> bool:
        """Preenche MultiSelectView com resposta anterior. Retorna True se preencheu."""
        if not self._previous_response_raw or not isinstance(self._previous_response_raw, dict):
            return False

        for key, data in self._previous_response_raw.items():
            if not isinstance(data, dict) or key not in view.selects:
                continue

            values = data.get("values", [])
            if isinstance(values, str):
                values = [values]

            style = data.get("style") or view.select_styles.get(key, "")

            for v in values:
                view.responses[key][v] = v

            select = view.selects[key]
            if style == "channel":
                select.default_values = [
                    SelectDefaultValue(id=int(v), type=SelectDefaultValueType.channel)
                    for v in values if v
                ]
            elif style == "role":
                select.default_values = [
                    SelectDefaultValue(id=int(v), type=SelectDefaultValueType.role)
                    for v in values if v
                ]

        return True

    def fill_design_select(self, view: discord.ui.View) -> bool:
        """Fills the DesignSelectView with the previous response. Returns True if filled."""
        if not self._previous_response:
            return False
        for value in self._previous_response:
            view.response[value] = value
        return True

    def fill_file_upload(self, view: discord.ui.View) -> bool:
        """Fills the FileUploadView with the previous response. Returns True if filled."""
        if not self._previous_response:
            return False
        if hasattr(view, '_response'):
            view._response = self._previous_response[0]
        return True
