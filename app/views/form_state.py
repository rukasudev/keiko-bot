from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord
from discord import SelectDefaultValue, SelectDefaultValueType

from app.services import analytics
from app.services.analytics import bucket_duration
from app.services.trace import new_trace_id


class FormSession:
    """One attempt at configuring a feature, from the first step to the last.

    The view already lives for the whole attempt, so it is the session: no
    session store, no extra timeout, nothing to clean up. Sub-forms inherit the
    id so a composition does not look like a separate attempt.
    """

    def __init__(self, session_id: str = None) -> None:
        self.id = session_id or new_trace_id()
        self.started_at = datetime.now(timezone.utc)
        self.steps_viewed = 0
        self.validation_failures = 0
        self.back_count = 0
        self.required_misses = 0
        self.last_step_key = None
        self.last_step: Dict[str, Any] = {}
        self.last_step_at: datetime = None
        self.step_failures: Dict[str, int] = {}
        self.guild_id = None
        self.user_id = None

    @property
    def duration_ms(self) -> int:
        delta = datetime.now(timezone.utc) - self.started_at
        return int(delta.total_seconds() * 1000)

    def record_step(self, step: Dict[str, Any]) -> Optional[int]:
        """Moves to `step` and returns how long the previous one was on screen.

        The interval is between two interactions, not attention: a long gap can
        be doubt, distraction, or the admin reading another tab.
        """
        now = datetime.now(timezone.utc)
        elapsed = None
        if self.last_step_at:
            elapsed = int((now - self.last_step_at).total_seconds() * 1000)

        self.steps_viewed += 1
        self.last_step = step or {}
        self.last_step_key = self.last_step.get("key")
        self.last_step_at = now
        return elapsed

    def close_step(self) -> Optional[int]:
        """Time on the final step, which no later step will ever report."""
        if not self.last_step_at:
            return None
        delta = datetime.now(timezone.utc) - self.last_step_at
        return int(delta.total_seconds() * 1000)

    def record_validation_failure(self, step_key: str) -> int:
        self.validation_failures += 1
        attempts = self.step_failures.get(step_key, 0) + 1
        self.step_failures[step_key] = attempts
        return attempts

    def friction(self) -> Dict[str, Any]:
        """The numbers every terminal setup event reports the same way."""
        return {
            "steps_viewed": self.steps_viewed,
            "validation_failures": self.validation_failures,
            "back_count": self.back_count,
            "duration_ms": self.duration_ms,
            "duration_bucket": bucket_duration(self.duration_ms),
        }


class SessionAwareView:
    """The one place a configuration view reports what the user is doing.

    Mixed into Form and Manager so every command inherits instrumentation from
    the engine instead of each handler calling an analytics API.
    """

    source = None

    @property
    def session(self) -> "FormSession":
        session = self.__dict__.get("_session")
        if session is None:
            session = FormSession()
            self.__dict__["_session"] = session
        return session

    @session.setter
    def session(self, value: "FormSession") -> None:
        self.__dict__["_session"] = value

    @property
    def view(self):
        return self.__dict__.get("_view")

    @view.setter
    def view(self, value) -> None:
        """Every screen the engine renders points back at the session that owns
        it, so a shared button (Cancel, Back) can report without knowing which
        kind of view it happens to live in."""
        self.__dict__["_view"] = value
        if value is not None:
            try:
                value.owner_form = self
            except AttributeError:
                pass

    @property
    def feature(self):
        return getattr(self, "command_key", None) or getattr(
            self, "_inherited_feature", None
        )

    def inherit_context(self, parent) -> None:
        """A sub-view is the same configuration attempt as the view that opened it."""
        if not parent:
            return
        session = getattr(parent, "session", None)
        if session:
            self.session = session
        self.source = getattr(parent, "source", None)
        self._inherited_feature = getattr(parent, "feature", None) or getattr(
            parent, "command_key", None
        )

    def emit_event(self, event: str, interaction, **props) -> None:
        analytics.emit(
            event,
            guild_id=getattr(interaction, "guild_id", None) or self.session.guild_id,
            user_id=(
                getattr(getattr(interaction, "user", None), "id", None)
                or self.session.user_id
            ),
            feature=self.feature,
            source=self.source,
            session_id=self.session.id,
            **props,
        )

    def open_journey(self, interaction, name: str) -> None:
        """Start the single log message that follows this session to its end."""
        from app.services import journey
        from app.services.trace import current_trace

        self.session.guild_id = getattr(interaction, "guild_id", None)
        self.session.user_id = getattr(getattr(interaction, "user", None), "id", None)

        trace = current_trace()
        story = journey.open_journey(
            self.session.id, name,
            guild_id=self.session.guild_id,
            user_id=self.session.user_id,
            feature=self.feature,
            source=self.source,
            inherit=trace,
        )
        story.is_journey = True
        if trace:
            trace.supersede(story)

    def close_journey(self, outcome: str) -> None:
        from app.services import journey

        journey.finalize(self.session.id, outcome)

    async def report_abandoned(self) -> None:
        """The view expired with the configuration unfinished.

        Nothing is said to the user — the buttons already stop responding when
        a view times out; this only closes the session's story. Guarded on the
        journey still being open, so a sub-form timing out after the parent
        finished cannot report a second abandonment.
        """
        from app.services import journey

        if not journey.get(self.session.id):
            return

        self.emit_event(
            "setup.abandoned", None,
            step_key=self.session.last_step_key,
            **self.session.friction(),
        )
        self.close_journey("abandoned")


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
        elif hasattr(view, 'month_select') and self._previous_response:
            selected = set(self._previous_response)
            for option in view.month_select.options:
                option.default = option.value in selected

        return True

    def fill_user_select(self, view: discord.ui.View) -> bool:
        """Preenche UserSelectView com resposta anterior. Retorna True se preencheu."""
        if not self._previous_response:
            return False
        for value in self._previous_response:
            view.response[value] = value

        if hasattr(view, 'user_select'):
            view.user_select.default_values = [
                SelectDefaultValue(id=int(v), type=SelectDefaultValueType.user)
                for v in self._previous_response if str(v).isdigit()
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
