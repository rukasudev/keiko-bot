# Keiko Form Platform — Architecture Review

Date: 2026-09-13. Scope: the Form + YAML + State subsystem (`app/views/form.py`,
`app/views/form_state.py`, `app/views/manager.py`, `app/views/summary_card.py`,
`app/views/composition.py`, `app/views/edit.py`, `app/views/remove.py`,
`app/components/*`, `app/services/moderations.py`, `app/languages/form/*.yml`) and
everything that plugs into it. Review only: no code was changed.

> **Baseline note (2026-09-13, same day).** Part I below was traced on the worktree
> branch at `6cf2016`. `origin/main` (`65f7ec1`) is 175 files ahead and already changes
> several of Part I's "current state" claims — notably the event-loop fix, `on_timeout`
> reporting, session ids, traces and journeys, and the Components V2 manager panel.
> **Part II (end of this document) re-evaluates every affected claim against main and
> answers the seven follow-up questions**; read II.0 before acting on §13.

Evidence labels used throughout: **Fact** (demonstrated by code or tests),
**Strong inference** (several pieces of evidence point the same way), **Hypothesis**
(plausible, not confirmed). Line references are to the current branch
(`rukasudev/adding-metrics`, HEAD `6cf2016`).

---

## 1. Executive summary

The **product model** behind Keiko forms is right and worth keeping: a feature is a YAML
definition composed from generic capabilities, the bot runs it as a guided
configuration conversation, and the result is one committed configuration document.
Seven features ship on it today, and the recent test harness proves the model can be
driven offline end to end.

The **runtime model** is the problem. The engine is a `discord.ui.View` subclass
(`Form`, 1,173 lines) that is at once the definition interpreter, the session state,
the renderer, the interaction dispatcher, the persistence orchestrator and the host
for feature-specific hooks. State lives in mutable attributes on that object and in
mutable dicts inside child views; every callback mutates it directly; rendering is a
side effect of mutation; there is no session identity, no revision, no lifecycle
status, and no boundary between "decide what happens" and "do it against Discord and
Mongo". This is why one form's change can break another (shared helpers are the
only place to put behaviour), why back navigation and double clicks corrupt state,
and why partial failures leave the guild in mixed states.

Two of the reported reliability problems have causes **outside** the engine that
are cheap to fix and should be fixed first: synchronous I/O and a literal
`time.sleep(15)` run on the bot's event loop (every interaction in every guild misses
Discord's 3-second acknowledgement window while it sleeps), and in-memory views that
time out after 30 minutes or vanish on restart with no `on_timeout` handling, leaving
clickable buttons attached to nothing.

Recommendation: **Option B — extract a deterministic form engine behind Discord
adapters, keep YAML as the definition language, migrate form by form.** Do the
Option A hardening (latching, timeout finalisation, unblocking the loop, schema
validation) as Stage 0/1 because it is cheap and de-risks everything after. Do not
build a persisted, restart-resumable workflow platform (Option C) now; the product
does not need sessions that outlive a process, and the cost would not buy a class of
bug you actually have.

---

## 2. Product / system premises

What a Keiko Form actually is (derived from the seven YAMLs and their services):

- **A guided configuration session.** A single admin, in one ephemeral Discord
  thread of messages, answers a short ordered list of questions and confirms. The
  output is one configuration document per (guild, feature) plus zero or more domain
  actions (Twitch/YouTube subscriptions, reminder scheduling, file re-hosting).
- **Short-lived and single-actor.** Views time out at 30 minutes
  (`app/views/form.py:71`); messages are ephemeral (`app/services/moderations.py:121`);
  nothing survives a restart. The only user who can click is the one who opened it.
- **Two modes of the same session.** *Setup* (run every step, then `_finish`) and
  *Manage* (lifecycle actions on saved config: pause, unpause, disable, history, plus
  partial re-runs of the wizard for *edit one step*, *add an item*, *remove an item*).
  Today both are implemented by re-instantiating `Form` with filtered steps
  (`app/views/edit.py:21,121`, `app/components/buttons.py:318-324`).
- **Compositions** (a list of items, each configured by a sub-wizard) are the one
  structural feature beyond a flat list of steps; three of seven forms use them.

So the domain object is not "a UI" and not "a workflow engine". It is a
**state machine over a definition**: `(definition, session, event) → (session', effects)`.
Everything else (embeds, LayoutViews, modals, Mongo documents, Twitch calls) is a
projection of, or a consequence of, that transition.

---

## 3. Premises worth challenging

| Premise embedded in today's code | Keep? | Why |
|---|---|---|
| YAML is the primary representation of a form | **Keep for definition**, stop for behaviour | Definition-as-data is the reason seven features fit in ~1.1k lines of YAML. But YAML already leaks behaviour: reflective `from: interaction, attr: guild.name` (`app/views/summary_card.py:237-244`), `reset-on-change` with named validators (`:326-345`), `condition.not_in` with three spellings of `false` (`reminders_birthday.yml:287-292`). |
| A Form *is* a Discord view | **Replace** | `Form(discord.ui.View)` (`form.py:47`) binds session lifetime to a Discord component lifetime, forces every state change through an interaction callback, and makes the core untestable without a Discord surface. |
| Handlers mutate state directly | **Replace** | `self.responses.pop()` (`form.py:1073`), `self.view.response[...] = ...` (`form_state.py:93-94`), `state[mode_state_key] = "default"` (`summary_card.py:468`), `del self.cogs[option]["values"][int(index)]` (`remove.py:117`). No single mutation point, no invariants. |
| Rendering and workflow execution are the same call | **Replace** | `show_*` methods both decide the next step and emit Discord API calls (`form.py:545-557`, `1094-1114`). |
| Feature-specific behaviour may live in the engine when a hook is missing | **Replace** | `pre_finish_step` (`form.py:905-941`), `_start_preview_pregeneration` (`:159-168`), month/date hydration (`:506-512`, `:966-974`), `COMPOSITION_COMMANDS_LIST` / `COMPOSITION_MAX_LENGTH` / `COMMAND_KEY_TO_COMPOSITION_KEY` in `app/constants.py:110-126` consumed by `form.py:694-699` and `manager.py:308-311`. |
| The stored document shape is derived from the answer list with a `style` tag | **Reconsider** | `{key: {style, values}}` (`form.py:411-422`) mixes presentation with storage and produces four "unwrap value/values" helpers (`docs/form-configuration.md` §7). |
| Edit = re-run the filtered wizard | **Keep the idea, make it explicit** | It is a good product idea (one code path). It should be a *session mode* with its own commit semantics, not a `Form` whose `command_key=""` and whose parent reaches into `edited_form_view.composition_index` (`reminders_birthdays.py:184-185`). |
| Compositions are nested `Form`s | **Reconsider** | `Form("", locale, steps, cogs=...)` (`composition.py:95`) plus attribute injection (`all_cogs`, `composition_responses`, `prefilled_step_keys`) is the most fragile construction in the code base. |
| Discord is embedded in the core | **Replace** | `SelectDefaultValue` in `form_state.py:105-117`; `discord.ButtonStyle` in `form.py:574-579`; `interaction.message.flags.components_v2` in `form.py:1126`. |
| Sessions do not need identity | **Replace** | No session id, no revision, no status. Every reliability problem below traces back to this. |

---

## 4. Architecture from first principles

### 4.1 Vocabulary (only the concepts the product needs)

| Concept | Represents | Why it exists | Owner | Config or runtime | Platform or feature |
|---|---|---|---|---|---|
| **FormDefinition** | The compiled, immutable, versioned description of one form: ordered steps, step kinds, field specs, conditions, references to extensions, copy in both locales. | Everything the engine needs to run a form without reading YAML at runtime. | Platform (compiler) | Config | Platform |
| **Step** | One question or screen in a definition. Has a `kind` (text, single-choice, channels, roles, users, multi-select, card, composition, info, review), a `key`, and a kind-specific spec. | Unit of navigation and of answers. | Platform | Config | Platform |
| **FormSession** | The runtime state of one run: identity (`session_id`), `definition_ref` (key + version), `mode` (setup, edit(subset), add_item, remove_item), `status`, `cursor` (current step key), `answers` (`Dict[step_key, Answer]`), `revision`, `origin` (guild, user, locale), `opened_at`, `expires_at`, `last_event_id`. | The single authoritative state. | Platform (engine) | Runtime | Platform |
| **Answer** | The committed value of one step: `raw` (machine value), `parts` (for multi-part steps), and the step key it answers. Display labels are derived at render time, not stored. | Removes the `value` / `_raw_value` / `style` / `hidden` ambiguity. | Platform | Runtime | Platform |
| **Event** | Something that happened: `Started`, `Answered(step_key, payload)`, `Back`, `Cancel`, `DiscardConfirmed`, `KeepEditing`, `ReviewConfirmed`, `ItemAdded`, `ItemRemoved`, `Expired`. Carries `event_id` (the Discord interaction id) and the `expected_revision` the UI was rendered from. | The only input that can change a session. | Adapter creates, engine consumes | Runtime | Platform |
| **Decision / Transition** | `engine.decide(definition, session, event) -> (session', [Effect])`. Pure. Rejects events whose `expected_revision` is stale or whose `event_id` was already applied. | Makes state changes testable and idempotent. | Platform | Runtime | Platform |
| **Effect** | A description of a side effect the adapter must execute: `Render(screen)`, `OpenModal(spec)`, `ShowError(key)`, `Commit(session)`, `RunDomainAction(name, payload)`, `Finalize(message)`. Data, not calls. | Separates deciding from doing; lets failures be reported against a known step. | Engine emits, adapter executes | Runtime | Platform |
| **Screen** | An abstract render model: title, description, footer, fields to show, components (`Choice`, `ChannelPicker`, `RolePicker`, `UserPicker`, `TextInputs`, `Card(sections)`, `Buttons`), and which of them are enabled. No Discord types. | Rendering derived from state; testable without Discord. | Engine (via step kind) | Runtime (derived) | Platform |
| **Step kind** | A registered pair `render(step, session) -> Screen` and `parse(step, payload) -> Answer | ValidationError`. | The extension point for new UI shapes. | Platform registry | Config (kinds) | Platform |
| **Validator** | Pure function `(value, context) -> ok | error_key`; declares what it needs (`needs: [guild_config, external:twitch]`) so the adapter can pre-fetch. | Feature rules without engine changes. | Platform registry, feature implementations | Config reference | Feature |
| **Transform** | `serialize(parts) -> stored`, `hydrate(stored) -> parts`. Exists today (`app/services/transforms.py`). | Multi-part answers. | Platform registry | Config reference | Platform |
| **Formatter** | `(value, style, locale) -> str`. Exists today (`format_values_by_style`). | Summaries and manager panel. | Platform registry | Config reference | Platform |
| **FeatureModule** | The one object a feature contributes: `to_document(answers)`, `from_document(doc) -> answers`, `commit(session, ctx)` (persistence + domain actions), `on_disable`, `on_item_added`, `on_item_removed`, `summary(doc, locale)`. Replaces `persistence_callback`, `lifecycle_callbacks`, `settings_provider`, `pre_finish_step` and the constants lists. | The only place feature logic lives. | Feature | Runtime | Feature |
| **SessionStore** | In-memory `Dict[session_id, FormSession]` with TTL; optional persistence later. | One place that owns sessions; enables expiry, per-session serialisation, observability. | Platform | Runtime | Platform |
| **DiscordAdapter** | Maps `Interaction -> Event`, executes `Effect`s (send / edit / replace / modal / followup), owns message identity, tokens, ephemeral rules, embed-vs-LayoutView transitions, timeouts. | Everything Discord-specific in one place. | Platform (adapter) | Runtime | Platform |

Deliberately **not** introduced: generic workflow graphs, transition guards as a
separate concept (conditions on steps are enough), a persisted event log, plugin
loading. None of the seven forms needs them.

### 4.2 Data and control flow

```
YAML file ──► schema validation ──► semantic validation ──► FormDefinition (frozen, versioned)
                                                                    │
  /command or button ──► DiscordAdapter.start(command_key, mode) ───┤
                                                                    ▼
                                            SessionStore.create(FormSession)
                                                                    │
  Interaction ──► DiscordAdapter.to_event(interaction, session) ──► Event
                                                                    │
                                                                    ▼
                        Engine.decide(definition, session, event) ─► (session', effects)
                                                                    │
                       SessionStore.commit(session')  ◄─────────────┤   (revision += 1)
                                                                    │
                       DiscordAdapter.execute(effects) ◄────────────┘
                          ├─ Render(screen)     → edit / replace message
                          ├─ OpenModal(spec)    → response.send_modal
                          ├─ ShowError(key)     → ephemeral followup
                          ├─ Commit(session)    → FeatureModule.commit → Mongo / Twitch / reminders
                          └─ Finalize(kind)     → strip components, final embed
```

### 4.3 Separate pure decisions from side effects — is it worth it here?

Yes, and specifically for four reasons visible in the current code:

1. **Testability.** Today a "does back navigation restore the multi-select" test must
   drive a fake Discord surface (`tests/behavioral/harness/`, seven modules). With a
   pure engine it is `assert decide(defn, s, Back()).session.answers == {...}`.
2. **Idempotency and dedup.** A pure `decide` can reject `event_id` replays and stale
   `expected_revision` before anything touches Discord. Today the first place a double
   click is noticed is a `40060` from Discord or a duplicated Mongo document.
3. **Partial-failure semantics.** Effects are ordered data; the adapter can log which
   effect failed against which session revision and decide to retry, compensate or
   finalise with an error screen. Today `_finish` (`form.py:846-903`) runs subscribe →
   moderations → cog insert → event insert → edit message with no boundary.
4. **Feature isolation.** Features contribute effects (`RunDomainAction`) and
   commit logic, never engine code paths. `if self.command_key == ...` becomes
   impossible by construction, not by convention.

Cost: one more layer, and the adapter has to be careful about Discord's
"respond once within 3 seconds" contract because it can no longer respond from inside
a `show_*` method. The existing harness already models exactly that contract
(`tests/behavioral/harness/fake_interaction.py:19-36`), so the cost is understood.

### 4.4 Source of truth

- **Authoritative:** the `FormSession` in the `SessionStore` (in-memory, single
  process). Its `revision` is the version.
- **Derived, rebuildable:** every Discord message and component (from
  `render(step, session)`); the manager panel (from the saved document via
  `FeatureModule.summary`); labels and formatted values (from answers + locale).
- **Persistent, separate truth:** the saved configuration document per (guild, feature)
  in Mongo, plus the cached copy in Redis (30-day TTL, `app/services/cache.py:29-43`).
  A session is a *proposal* until `Commit` succeeds; after commit, the document is the
  truth and the session is closed.
- **Never a source of truth:** component `custom_id`s, message ids, YAML at runtime,
  closure variables, the Redis cache (invalidated on write, `app/services/cogs.py:11,42,51`).

Derived state rebuild: any render is `screen = kind.render(step, session)`; if a
message is missing or an edit fails, the adapter re-sends the same screen. A manager
panel rebuild is `summary(load_document(guild, feature))`.

### 4.5 Lifecycle

Lifecycle status is separate from the cursor (which step). Keep them separate: the
cursor answers "where in the definition", the status answers "may this session still
accept events".

```
            start
              │
              ▼
          ┌────────┐  Answered/Back/Cancel(keep)   ┌───────────┐
          │ ACTIVE │◄─────────────────────────────►│ AWAITING  │  (modal open / picker open)
          └───┬────┘                               └───────────┘
              │ ReviewConfirmed
              ▼
        ┌────────────┐  commit ok      ┌───────────┐
        │ COMMITTING │────────────────►│ COMPLETED │
        └─────┬──────┘                 └───────────┘
              │ commit failed
              ▼
        ┌────────┐      DiscardConfirmed          ┌───────────┐
        │ FAILED │   ACTIVE ─────────────────────►│ CANCELLED │
        └────────┘      Expired / restart         └───────────┘
                     ACTIVE ─────────────────────► EXPIRED
```

Per transition:

| Transition | Trigger | Preconditions | Revision | Effects | Persistence | Discord | Failure | Idempotency |
|---|---|---|---|---|---|---|---|---|
| `∅ → ACTIVE` | command / button | admin, guild, definition valid | 0 | Render(first screen) | none | send ephemeral | show error embed | one session per interaction id |
| `ACTIVE → ACTIVE` (answer) | component / modal submit | `expected_revision == revision`; `event_id` unseen; parse ok | +1 | Render(next) or ShowError | none | edit / replace | error screen keeps old revision | replay of same `event_id` → no-op, re-render current |
| `ACTIVE → ACTIVE` (back) | Back | previous step exists | +1 | Render(prev with prior answer) | none | edit | — | same |
| `ACTIVE → CANCELLED` | DiscardConfirmed | status ACTIVE | +1 | Finalize(discarded) | none | strip components | — | same |
| `ACTIVE → COMMITTING` | ReviewConfirmed | all required answered | +1 | Commit(session) | FeatureModule.commit | defer | see FAILED | second confirm rejected (status ≠ ACTIVE) |
| `COMMITTING → COMPLETED` | commit returned | — | +1 | Finalize(enabled/edited/added…) + audit event | done | edit final embed | edit fails → followup | — |
| `COMMITTING → FAILED` | commit raised | — | +1 | Finalize(error) | none guaranteed; module reports what it wrote | error embed | logged with session id | retry = new session |
| `ACTIVE → EXPIRED` | timeout / restart | — | +1 | Finalize(expired) | none | strip components (if reachable) | best effort | — |

A `COMPLETED`, `CANCELLED`, `FAILED` or `EXPIRED` session rejects every
state-changing event and answers with a "this form is closed" render.

### 4.6 Hostile-environment design

Assume: double clicks, clicks on stale messages, restarts mid-form, Discord edit
failures, Mongo write failures, slow handlers. Minimal mechanisms that are justified
by the failures actually observed in this code base:

| Guarantee | Mechanism | Justified by |
|---|---|---|
| The same logical event is applied once | `event_id` = Discord interaction id, remembered per session (last N) | double-click on card Done / review Confirm (§13) |
| A stale UI cannot mutate a newer session | `expected_revision` carried in the component `custom_id` (`k:<session>:<rev>:<action>`), rejected if `< revision` | re-rendered LayoutViews with identical `custom_id`s (§13) |
| Two events on one session never interleave | per-session `asyncio.Lock` in the adapter | discord.py dispatches each interaction as its own task |
| A closed session never changes | status check in `decide` | Confirm after `_finish` began |
| No blocking of the event loop | all sync I/O via `asyncio.to_thread` or async clients | `time.sleep(15)` on the loop (§13, confirmed) |
| Expiry is visible | `on_timeout` → `Expired` event → Finalize | 30-minute silent death (§13, confirmed) |
| Concurrent manager panels do not clobber each other | document `revision` field + `$inc`, or array ops (`$push`/`$pull`) for item lists | full-document `$set` from an in-memory snapshot (§13) |

Not justified today: distributed locks, an event log, exactly-once semantics with
Discord, optimistic concurrency on Discord messages, multi-worker coordination (single
`Bot` process, `app/bot.py:15`, one service in `docker-compose.yml`).

### 4.7 Extension model

Extensions may:
- register a **step kind** (`render` + `parse`), a **validator**, a **transform**, a
  **formatter**, a **card section type**;
- provide a **FeatureModule** for a command key (document mapping, commit, lifecycle
  reactions, summary);
- declare data they need pre-fetched (`needs`), so the adapter loads it before `decide`.

Extensions may not:
- mutate a `FormSession` (they receive answers, they return values or effects);
- call Discord (they return `Screen` fragments or effects; the adapter renders);
- read another feature's document;
- branch on the command key inside platform code (the platform never passes it to
  step kinds or validators; it passes the step spec and the session context).

Property to hold: adding a new form = a YAML file + a `FeatureModule` (only if
persistence is not the generic document) + tests. No engine edit.

### 4.8 YAML contract

YAML **should** express: form metadata and version; ordered steps with `kind`, `key`,
copy (both locales), and kind-specific spec (options, selects, fields, sections,
designs); `required`, `unique`, `max_length`, `condition` (equality/inclusion on an
earlier answer only); references by name to validators, transforms, formatters,
section types; defaults; composition (`items: {min, max, unique_by, steps}`).

YAML **should not** express: attribute paths into runtime objects
(`from: interaction, attr: guild.name` — replace with a closed set of named context
values, e.g. `context: server_name`); reset rules that call validators (make it a
step-kind option: `depends_on: month` + the step's own validator); persistence
behaviour; any Python name that is not in a registry; any callable.

Pipeline: `YAML → schema (typed model, unknown fields rejected) → semantic checks
(keys unique, conditions reference earlier keys, registries resolve, both locales
present, composition limits present) → FormDefinition (frozen) → registry keyed by
(command_key, version)`. Run at import (fail startup) and in CI (a test that loads
every file). `tests/behavioral/test_form_yaml_contracts.py:82-179` already does the
semantic half against raw dicts; it is the seed of this pipeline.

### 4.9 Public platform contract

Public (a feature developer needs to know these): the YAML schema; `FeatureModule`
protocol; the registries (`step kinds`, `validators`, `transforms`, `formatters`,
`section types`) and how to add one; the `Answer` shape their module receives; the
test helpers (`run(definition, events) -> session, effects`; the existing behavioral
driver for adapter-level checks).

Private (never touched by feature code): `FormSession` internals, `Engine.decide`,
`SessionStore`, `DiscordAdapter`, `custom_id` scheme, message replacement strategy,
LayoutView/embed transitions, revision and dedup bookkeeping, timeout handling.

### 4.10 Discord as an adapter

Could most of the engine be tested without importing Discord? In the target design
yes: `Engine`, `FormSession`, step-kind `parse`, validators, transforms, formatters and
`FeatureModule.to_document`/`from_document` import nothing from `discord`. `render`
returns `Screen`; only the adapter imports `discord`. The coupling that remains
(ChannelSelect / RoleSelect / UserSelect semantics, modal limits of 5 inputs,
Components V2 limits of 40 components / 4000 chars) is expressed as **constraints in
the Screen model** and asserted by adapter tests (`tests/behavioral/contracts/test_components_v2_limits.py`
already does this for cards).

### 4.11 Ideal developer experience

1. Write `app/languages/form/<key>.yml` (validated on save by the CI test).
2. Reuse step kinds and validators; if a new validator is needed, add one pure
   function to the registry.
3. If the generic document is not enough, implement `FeatureModule` for the key
   (`to_document`, `commit`, `summary`).
4. Write engine tests: `run(defn, [Started, Answered(...), ..., ReviewConfirmed])` and
   assert the final answers and the `Commit` effect payload.
5. Optionally one adapter scenario with the existing harness.
6. Deploy.

Compared with today (§7 of `docs/form-configuration.md` lists it): add a constant,
maybe extend `COMPOSITION_*` dicts, register in `ExecuteCommandButton.COMMAND_SERVICES`,
write a service module with the `manager()` shape, know which of
`persistence_callback` / `settings_provider` / `lifecycle_callbacks` / `pre_finish_step`
to use, know that `Form.cogs` may be a list or a dict depending on the caller, know
that `responses` may contain hidden entries, and know that `Form._callback` must be
called with an interaction whose response is not yet done.

---

## 5. Architectural invariants

These are the laws the platform should enforce structurally. For each: how the target
enforces it and whether the current code can.

| # | Invariant | Target enforcement | Current state |
|---|---|---|---|
| I1 | A form session has exactly one authoritative state object, with an identity and a monotonically increasing revision. | `FormSession` in `SessionStore`; every `decide` returns a new revision. | **Violated.** Answers live in `Form.responses` *and* `FormStateManager.responses_by_step` *and* `responses_by_step_raw` (`form_state.py:13-14`, `form.py:68`), plus `view.response` dicts in each child view, plus `SummaryCardView.state`. No id, no revision. |
| I2 | Only a transition (`decide`) may change session state. | Session is immutable outside the engine (frozen dataclass; store replaces). | **Violated.** 14 distinct mutation sites across `form.py`, `form_state.py`, `buttons.py`, `summary_card.py`, `composition.py`, `remove.py`. |
| I3 | A closed session (completed / cancelled / failed / expired) rejects state-changing events. | Status check first in `decide`. | **Violated.** No status; `_finish` can run twice; `ConfirmActionView.confirm` stops itself but the source view keeps accepting clicks until `stop()` is called in `discard` (`confirm_action.py:62-70`). |
| I4 | The same interaction cannot produce the same transition twice. | `event_id` dedup per session. | **Violated.** No dedup; discord.py schedules each interaction as its own task. |
| I5 | A UI rendered from revision *r* cannot mutate a session at revision *r' > r*. | `expected_revision` in `custom_id`, checked in `decide`. | **Violated.** `custom_id`s are either random (discord.py default) or deterministic and reused across re-renders (`card_done`, `card_customize_N`, `picker_back`, `design_<key>`, `prev_page`). |
| I6 | Rendering is a pure function of (definition, session). | `Screen = kind.render(step, session)`. | **Violated.** Rendering reads `self.step_embed` mutated in place (`form.py:682-684`, `1036-1040`), `_using_layout_view` (`:70`), and `interaction.message.flags` (`:1126`). |
| I7 | Feature code cannot mutate engine state. | Features receive copies; return values / effects. | **Violated.** `edit_birthday_save` reads `manager_view.edited_form_view.composition_index` (`reminders_birthdays.py:184-185`); `add_birthdays_manager_item` mutates `manager_view.cogs[...]["values"]` (`:520-525`); `pre_finish_step` appends to `self.responses` (`form.py:941`). |
| I8 | An invalid definition can never become an active form. | Compile at startup; unknown fields rejected; CI test. | **Partially met.** YAML contract tests exist (`tests/behavioral/test_form_yaml_contracts.py`) but the loader itself validates nothing (`utils.py:75-82`); unknown actions silently no-op (`form.py:1065-1066`); unknown section types are warned and skipped (`summary_card.py:864-870`). |
| I9 | Every session references one definition version. | `definition_ref = (key, version)` in the session. | **Violated.** No version field; `parse_form_yaml_to_dict` is process-cached (`utils.py:75`), so the definition is "whatever was on disk at first load". |
| I10 | Core behaviour does not depend on a particular product flow. | Platform never receives a command key. | **Violated.** `form.py:161`, `:694-699`, `:701-702`, `:916-941`, `:966`, `:506`; `manager.py:250-253`, `:308-311`, `:446-449`; `constants.py:110-126`. |
| I11 | Side effects are executed in a declared order with per-effect failure reporting. | `Effect` list executed by adapter with logging. | **Violated.** Ordered inline calls with no boundary (`form.py:851-903`, `manager.py:239-293`). |
| I12 | The event loop is never blocked by I/O. | All sync clients wrapped in `to_thread`; no `time.sleep`. | **Violated.** Sync `requests`, `pymongo`, `redis` everywhere; `time.sleep(15)` in `notifications_twitch.py:189` runs on `bot.loop` (`webhooks/twitch.py:25`). |

Where structural enforcement is not possible: I12 cannot be enforced by types in
Python; it needs a lint rule (ban `requests.` and `time.sleep` outside
`app/integrations/*` and require `to_thread` there) plus an event-loop-lag metric.

---

## 6. Architecture North Star

### 6.1 Components and boundaries

```
┌──────────────────────────────── platform (no discord import) ────────────────────────────────┐
│                                                                                              │
│  definitions/                     engine/                          extensions/               │
│  ├─ schema.py   (typed YAML)      ├─ session.py  (FormSession)     ├─ step_kinds/            │
│  ├─ compiler.py (→ Definition)    ├─ events.py   (Event types)     ├─ validators.py          │
│  └─ registry.py (key,version)     ├─ effects.py  (Effect types)    ├─ transforms.py          │
│                                   ├─ screen.py   (Screen model)    ├─ formatters.py          │
│                                   ├─ engine.py   (decide)          └─ sections.py            │
│                                   └─ store.py    (SessionStore)                              │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                     ▲                                  ▲
                     │ Definition, Event                 │ Screen, Effect
                     │                                  │
┌────────────────────┴──────────────────────────────────┴──────────────────────────────────────┐
│  adapters/discord/                                                                            │
│  ├─ interactions.py  (Interaction → Event; custom_id codec; per-session lock; dedup)          │
│  ├─ renderer.py      (Screen → embed+View | LayoutView; CV2 limits)                           │
│  ├─ executor.py      (Effect → response/followup/edit/replace/modal; timeout → Expired)       │
│  └─ entrypoints.py   (send_form / send_manager for cogs and buttons)                          │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                     ▲
                     │ FeatureModule protocol
┌────────────────────┴──────────────────────────────────────────────────────────────────────────┐
│  features/ (one module per command key)                                                       │
│  block_links, default_roles, welcome_messages, notifications_twitch, notifications_youtube,   │
│  stream_elements, reminders_birthday                                                          │
│  each: to_document / from_document / commit / on_disable / on_item_* / summary                │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

Dependency direction: `features → platform types`; `adapters → platform + features`;
`platform → nothing internal`. Cogs call `adapters.discord.entrypoints` only.

### 6.2 State ownership

| State | Owner | Lifetime |
|---|---|---|
| `FormDefinition` | `definitions.registry` | process |
| `FormSession` | `engine.store` | ≤ expiry (30 min) |
| Discord message ids for a session | `adapters.discord` (in session metadata, adapter-private) | session |
| Saved configuration document | `features/<key>` via `app/data` | durable |
| Redis cache of the document | `app/services/cache` | 30 days, invalidated on write |
| Pre-fetched context (guild roles, external lookups) | adapter, per event | one `decide` |

### 6.3 Side-effect model

`Commit` is the only effect with durable consequences. `FeatureModule.commit` returns
a `CommitResult` listing what was written (document ids, external subscriptions
created) so that a later `Finalize` and the audit event can report exactly what
happened, and a failure midway can log what *was* written. Domain actions that must
not be duplicated (Twitch subscribe) are made idempotent inside the feature (already
the case for 409 handling, `notifications_twitch.py:228-234`).

### 6.4 Error model

- **Validation error** → `ShowError(key)` effect; session unchanged (revision same).
- **Stale / duplicate event** → `Rejected(reason)`; adapter re-renders the current
  screen with a short notice; logged at info.
- **Adapter failure** (Discord edit/send raises) → logged with session id + effect;
  the adapter retries once via followup; session state already committed.
- **Commit failure** → session `FAILED`; `Finalize(error)`; audit log entry with
  `CommitResult` so far.
- **Expiry** → `EXPIRED`; components stripped if the message is still reachable.

---

## 7. Ideal developer experience — before vs after (summary)

See §4.11 for the flow. The measurable reductions: files touched for a new simple
form go from 5–6 (YAML, constants, service module, `COMMAND_SERVICES`, cog, language
files) to 3 (YAML, cog, language files); hidden conventions a developer must know go
from ~10 (listed in §4.11) to 2 (YAML schema, `FeatureModule` protocol); Discord
knowledge needed for a feature drops to zero.

---

## 8. Current architecture (as it actually runs)

Traced execution path, first-time setup:

```
cog (app/cogs/.../*.py)            @keiko_command + @keiko_admin_only (app/decorators.py:14-46)
  → service.manager(interaction, guild_id)          app/services/<key>.py
    → cache.get_cog_data_or_populate(...)            app/services/cache.py:29-43
    → send_command_form_message(interaction, key, persistence_callback?)   moderations.py:104-121
      → Form(command_key, locale)                    form.py:57-73  (View, timeout=1800, Confirm+Cancel)
        → parse_form_yaml_to_dict(key)  (cached)     utils.py:75-82
        → FormStateManager(list(steps))              form_state.py:10-16
      → interaction.response.send_message(embed=intro, view=form, ephemeral=True)
  user clicks Confirm on intro
  → ConfirmButton.callback == Form._callback         buttons.py:14-21 → form.py:1165-1173
    → @_update_form_step                              form.py:96-122
        _handle_after_step → self.view.get_response() → _save_step_response   form.py:199-308
        state.advance(); skip `form`; while _should_skip_step(): advance
        self.step_embed = parse_form_dict_to_embed(step)                       embed.py:9-34
    → get_action_by_type(action)                      form.py:1044-1066
      → show_<kind>: build child view, self.view = ...; _send_view / _send_layout_view / send_modal
        _send_view: fill_* from FormStateManager, else parse_cogs_* from self.cogs; add Back button;
                    followup.edit_message(...) or delete+followup.send for LayoutView transitions
  child view collects input into view.response / view.responses / view.state
  child Confirm → Form._callback again (loop)
  last step `resume` → show_resume: Edit / Add / Remove / Preview / Confirm(_finish) / Cancel   form.py:687-707
  Confirm → Form._finish                              form.py:846-903
    defer → pre_finish_step (feature branches) → _parse_responses_to_cog
    → persistence_callback(...) | update_moderations_by_guild + insert_cog_by_guild
    → insert_cog_event → edit_original_response(final embed, view=self cleared)
```

Manage mode: `send_command_manager_message` (`moderations.py:124-172`) builds a
`Manager` view (`manager.py:56-82`); Edit → `EditCommand` (`edit.py`) → new
`Form(cogs=doc)` filtered to one step → `Manager.update_command` (`manager.py:98-156`);
Add → `AddItemButton` builds `Form` filtered to the composition step
(`buttons.py:311-324`) → `Manager.add_item_callback` (`manager.py:316-375`);
Remove → `RemoveItem` (`remove.py`) mutates the in-memory doc then
`Manager.remove_item_callback` (`manager.py:442-491`).

Compositions: `show_composition` → `FormComposition` (`composition.py`) → nested
`Form("", locale, sub_steps, cogs=item_or_list)` per item (`:95`), attribute-injected
`all_cogs`, `composition_responses`, `prefilled_step_keys`; on finish the item is merged
into `FormComposition.responses`, which may alias the manager's document
(`composition.py:41,49`).

### 8.1 Five real forms traced (deviations are the evidence of drift)

| Form | Shape | Deviations from the generic path |
|---|---|---|
| **block_links** (simple) | multi_select → options → modal → resume | None in the engine. Generic persistence. Cog read via cache with `manager=True`. |
| **welcome_messages** (custom UI) | channels(select) → design_select (LayoutView) → file_upload (conditional) → button → modal(5 fields, keyed + concat) → resume(preview) | `_start_preview_pregeneration` hardwired to this key (`form.py:161`); `PreviewButton` callback hardwired to `send_welcome_message_preview` (`:702`); modal responses mix keyed fields and `__concat__` (`modals.py:94-117`); `ModalValidations` shares the class with unrelated validators. |
| **notifications_twitch** (composition + external) | composition[channels(select) → modal(validated) → button → modal(3 inputs, enumerate)] → resume | `pre_finish_step` twitch branch (`form.py:919-936`); `_handle_subscription` reads `self.view.form_view.cogs` (`:949`); manager `disable` / `remove_item` have `if command_key` branches (`manager.py:250-253`, `:446-449`); max items in `constants.py:122-126`; validator does a sync HTTP call inside modal submit (`modals.py:737`). |
| **reminders_birthday** (highly customised) | configuration_card → options(hidden, auto_confirm) → composition[user_select → configuration_card(transform)] (conditional) → resume | `persistence_callback`, `settings_provider`, four `lifecycle_callbacks` (`reminders_birthdays.py:36-63`); engine hydration special-cases `date`/`month` (`form.py:506-512`, `:966-974`); feature reads `edited_form_view.composition_index` (`reminders_birthdays.py:184`); separate persistence model (`app/data/birthdays.py`) but the manager still receives a synthetic cog document (`birthday_manager_cog_data`) so `Manager` can count items; user-facing English titles in `to_summary_composition` (`birthdays.py:151-183`). |
| **stream_elements_commands** (simplest, plus a hidden effect) | modal(validated) → resume | `pre_finish_step` appends a synthetic response with hardcoded English title `"Channel ID"` (`form.py:938-941`), performing an HTTP call after the user confirmed. |

The regression docstring in
`tests/behavioral/regressions/test_manager_summary_regressions.py:15-26` records the
canonical incident: a shared helper (`parse_settings_with_database_values`) was
changed for birthdays and `default_roles` silently lost its manager summary. That is
the "changing one form breaks another" failure in one sentence: **the only place to
put behaviour is a helper every form shares.**

---

## 9. Current runtime lifecycle (YAML → interaction → state → persistence → rendering)

| Stage | Owner today | Where state lives | Notes |
|---|---|---|---|
| YAML parse | `parse_form_yaml_to_dict` (`utils.py:75-82`) | `functools.cache` (process) | No schema, no version, `steps` only. |
| Runtime representation | the same raw dicts | shared by every `Form` instance | `filter_steps` rebinds the list (`form.py:436`); dicts themselves are shared; `summary_card` deep-copies before mutating (`:690`). |
| State init | `Form.__init__` + `FormStateManager` | View attributes | `cogs` may be `None`, a dict (edit) or a list (composition item context). |
| Rendering | `parse_form_dict_to_embed` + `show_*` + child views | `self.step_embed`, child view items | Embed mutated in place for options/roles (`form.py:682-684`, `1036-1040`). |
| Interaction routing | discord.py `ViewStore` by message id + `custom_id` | discord.py | Deterministic ids on LayoutViews; localized text as id on Pause/Unpause/Disable (`buttons.py:139,175,187`). |
| Dispatch | `_update_form_step` + `get_action_by_type` | — | Unknown action → silent no-op. |
| Validation | `CustomModal.on_submit` (`modals.py:119-131`), `SelectConfirmButton` (`select_views.py:24-33`), `OptionsView._confirm_callback` (`options.py:93-101`), card `on_done` (`summary_card.py:881-890`) | per component | Four different places, three different error channels (ephemeral embed, ephemeral text, `channel.send`). |
| State mutation | `_save_step_response` and friends | `Form.responses` (+ state manager) | Action-type branching, `_upsert_response` by key. |
| Persistence | `_finish` / `Manager.*` / `persistence_callback` / lifecycle callbacks | Mongo via `app/data`, cache invalidation | Full-document writes. |
| Message update | inline in each `show_*` / callback | Discord | Three strategies: edit, followup edit, delete+resend. |
| Next interaction | new discord.py task | — | No serialisation with the previous one. |

---

## 10. Current state ownership map

| State | Owner | Source of truth? | Mutators | Persistence | Derived? |
|---|---|---|---|---|---|
| Step list | `FormStateManager.steps_list` | YAML (cached) | `filter_steps` (rebinds) | no | from YAML |
| Step index | `FormStateManager.step_index` | itself | `advance`, `go_back`, `_design_select_callback` (`form.py:832`) | no | no |
| Current step dict | `Form._step` | duplicate of `steps_list[step_index]` | `_update_form_step`, `_go_back`, `_design_select_callback` | no | should be |
| Answers (display) | `Form.responses` list of `{key,title,value,style,hidden,_raw_value}` | **ambiguous** with below | `_upsert_response`, `pop()`, `update_resume`, `pre_finish_step`, `_ensure_composition_response`, `FormComposition._apply_prefilled_fields` | no | mixed: value+label+style |
| Answers (navigation) | `FormStateManager.responses_by_step{,_raw}` | **ambiguous** with above | `save_response` | no | no |
| Previous answer for refill | `FormStateManager._previous_response{,_raw}` | transient | `go_back`, `clear_previous_response` | no | derived |
| Component selection | `view.response` / `view.responses` / `SummaryCardView.state` | per child view | button/select callbacks, `fill_*`, `parse_cogs_*`, `set_defaults`, `update_state` | no | becomes an answer on confirm |
| Saved config snapshot | `Form.cogs`, `Manager.cogs`, `FormComposition.cogs`, `RemoveItem.cogs` | Mongo is | `remove.py:117`, `manager.py:337`, `composition.py:41,49`, birthday `add_birthdays_manager_item` | Mongo (full `$set`) | stale after any other write |
| Rendering mode | `Form._using_layout_view` + `interaction.message.flags.components_v2` | neither | `_transition_from_layout_view`, `_send_layout_view` | no | should be derived from step kind |
| Preview task | `Form._preview_task` | itself | `_start_preview_pregeneration` | no | feature-specific |
| Cross-view links | `parent_view.edited_form_view`, `parent_view.form_view`, `parent_view._original_embed`, `Form.after_callback`, `Form.persistence_callback`, `composition_index`, `prefilled_step_keys`, `prefilled_composition_fields`, `all_cogs`, `composition_responses` | set by other objects via attribute injection | `buttons.py:124-127,318-322`, `edit.py:155-158,169`, `composition.py:96-98,108,142` | no | implicit state |
| Redis cog cache | `cache.py` | Mongo is | writes through `services/cogs.py` | 30-day TTL | derived |
| Moderations flags | `moderations` collection | itself (feature enabled/paused) | `update_moderations_by_guild`, pause/unpause | Mongo | can diverge from cog doc (`_finish` writes both, non-atomically) |

Invalid combinations reachable today: `moderations[key]=True` with no cog document
(cog insert fails after moderations update, `form.py:860-865`); a `Form` whose
`responses` still contain answers for steps behind the cursor (back over a
multi-answer step, `form.py:1068-1082`); a `Manager.cogs` list that no longer
matches Mongo (another panel wrote); `enabled=False` in the cog document while the
manager `pause_handler` was built from a stale `cogs` (`manager.py:158-168`).

---

## 11. Architectural gap analysis

| # | Intended property | Current behaviour | Evidence | Consequence | Severity | Scope |
|---|---|---|---|---|---|---|
| G1 | One authoritative session state with identity and revision | Two answer stores + per-view dicts, no id/revision | `form.py:68`, `form_state.py:13-16`, `summary_card.py:65` | stale state, impossible to dedup or trace | **Critical** | Structural |
| G2 | State changes only through transitions | 14 direct mutation sites incl. feature code | §5 I2, I7 | incorrect state updates; features break engine | **Critical** | Structural |
| G3 | Deterministic core testable without Discord | Engine *is* a View; every test needs the harness | `form.py:47`, `tests/behavioral/harness/` | slow feedback, low coverage of edge paths (back, double click) | **High** | Structural |
| G4 | Idempotent / stale-safe event handling | none | §13 | double advance, double commit, stale click routed to new view | **High** | Cross-cutting |
| G5 | Explicit lifecycle | booleans and absence (`hasattr(self, "view")`, `_using_layout_view`, `hasattr(self, "after_callback")`) | `form.py:200,155,1125` | closed sessions keep accepting input; expiry invisible | **High** | Cross-cutting |
| G6 | Feature logic outside the engine | 9 command-key branches / lookups in engine + constants | §5 I10 | shotgun surgery per feature; regressions in other forms | **High** | Cross-cutting |
| G7 | Side effects with failure boundaries | inline sequences, swallowed exceptions | `form.py:851-903`, `manager.py:151-154,290-293` | mixed guild state; "thinking…" forever on error | **High** | Cross-cutting |
| G8 | Event loop never blocked | sync `requests`/pymongo/redis in handlers; `time.sleep(15)` | `notifications_twitch.py:189`, `modals.py:737`, `form.py:940`, `welcome_messages.py:188-192` | interaction-failed for *all* users during blocks | **Critical** (cheap fix) | Cross-cutting |
| G9 | Validated, versioned definitions | raw dicts, cached, unknown actions no-op | `utils.py:75-82`, `form.py:1065` | typos become dead steps; no upgrade path for saved docs | **Medium** | Local |
| G10 | Rendering derived from state | in-place embed mutation, rendering-mode flag | `form.py:682-684,1036-1040,1124-1129` | messages showing outdated state | **Medium** | Cross-cutting |
| G11 | Stable component identity | random ids (default) or deterministic reused ids; localized text as id | `summary_card.py:103-143`, `buttons.py:139,175,187` | stale clicks mis-routed; confirmation word depends on locale of the button | **Medium** | Local |
| G12 | Concurrency-safe persistence for shared documents | full-document `$set` from in-memory snapshot | `manager.py:337-338,454`, `data/cogs.py:43-47` | lost items when two panels write | **Medium** | Local |
| G13 | Single validation path with one error channel | four validation sites, three channels | §9 | inconsistent UX; `channel.send` (non-ephemeral) on required-options error (`options.py:96-100`) | **Medium** | Cross-cutting |
| G14 | Storage shape independent of presentation | `{style, values}` in documents; `hidden` in items | `form.py:411-422`, `birthdays.py:151-183` | four unwrap helpers; migrations coupled to UI | **Medium** | Structural |
| G15 | Compositions as a first-class step kind | nested `Form("")` with attribute injection and aliasing | `composition.py:95-98,41,49` | most fragile path; edit/add/remove all differ | **High** | Structural |
| G16 | Observability by session | logs carry interaction/guild/user; no session id, revision, step, event | `app/logger.py:219-247`, `form.py:75-88` | cannot reconstruct one form lifecycle | **Medium** | Cross-cutting |

---

## 12. Architectural decisions worth reconsidering

| Current decision | Why it probably exists | Choose again today? | Alternative | Migration difficulty |
|---|---|---|---|---|
| `Form` extends `discord.ui.View` | Fastest way to get callbacks in discord.py | **No** | Session object + adapter; views are throwaway renderers | M (Stages 2–4) |
| YAML per command with `steps:` only, inline locales | Simple; keeps copy next to structure | **Yes** (add `version`, `kind` naming, schema) | — | S |
| Step "action" names double as UI widget names (`channels`, `modal`, `options`) | Grew from the UI | **Mostly yes**; rename to *kinds* and make `select: true` a kind, not a flag | `kind: channel_pick` etc. | S (alias old names in compiler) |
| Answers as list of `{key,title,value,style,hidden,_raw_value}` | Summary rendering needed titles | **No** | `Dict[step_key, Answer(raw, parts)]`; titles/styles resolved from definition at render | M |
| `FormStateManager` as a second store for back-navigation | Added later to fix refill | **No** | one session; `render` reads `answers[prev_key]` | S once G1 is fixed |
| Persisted document `{key: {style, values}}` | Manager needs to format values | **No** (but keep reading it) | store raw values; formatting from definition; migrate lazily via `from_document` | M (data migration or dual-read) |
| `persistence_callback` + `settings_provider` + `lifecycle_callbacks` + `pre_finish_step` | Added one at a time as birthdays/twitch needed them | **No** | one `FeatureModule` protocol | S–M |
| Composition limits and keys in `app/constants.py` | Needed by both Form and Manager | **No** | `items: {max, unique_by}` in YAML | XS |
| Edit = re-run filtered `Form` with `cogs` | Reuses one code path | **Yes as a mode**, no as an attribute-injected `Form` | `mode=edit(subset)` in session; `from_document` seeds answers | M |
| Compositions as nested `Form` | Reuse | **No** | composition step kind that runs a *child session* with explicit parent link | M–L |
| LayoutView cards with internal `state` dict and `_render()` | Components V2 requires rebuilding the tree | **Keep the rebuild, move the state** | card `state` = the session's partial answer for that step | S |
| Delete + resend when switching embed ↔ LayoutView | Discord cannot convert a message in place | **Yes** (Discord constraint) | make it an adapter concern with failure handling | S |
| Localized label as `custom_id` for Pause/Unpause/Disable, reused as the confirmation word | Convenient | **No** | fixed ids; confirmation word from i18n key | XS |
| `functools.cache` on YAML load, no validation | Simplicity | **No** | compile at startup, fail fast | S |
| Sync pymongo / requests on the loop | Historical | **No** | `asyncio.to_thread` now; async clients later | S (mechanical) |
| Views time out silently after 1800 s | Default discord.py behaviour | **No** | `on_timeout` → Expired finalisation | XS |
| Ephemeral messages as the identity guard | Correct and cheap | **Yes** | keep; add explicit user check in adapter anyway (defensive) | XS |

---

## 13. Reliability analysis

### 13.1 Ghost / stale interactions — investigated from code

> Status of each cause on `origin/main` is in **II.0**; C1 is fixed at the known call sites there and P3 is fixed in one direction.

**Confirmed causes (Fact).**

- **C1 — Event-loop blocking.** `handle_send_streamer_notification` is scheduled on
  `bot.loop` (`app/webhooks/twitch.py:25`) and calls `wait_for_stream_info`, which does
  up to three `time.sleep(15)` (`app/services/notifications_twitch.py:174-189`). While
  the loop is blocked no interaction anywhere can be acknowledged within Discord's
  3-second window, and the client shows "This interaction failed" — indistinguishable
  from a ghost click. The same class, shorter: every `requests.*` call in
  `app/integrations/*` runs on the loop, including inside modal submit
  (`validate_streamer_name`, `modals.py:730-738`), inside `_finish`
  (`StreamElementsClient.get_channel_info`, `form.py:939-940`), inside preview
  generation (`welcome_messages.py:187-192`), and every pymongo/redis call in
  `app/data` and `app/services/cache.py`.
- **C2 — Silent expiry and restarts.** Form views live 1800 s (`form.py:71`), modals
  300 s; no view implements `on_timeout` (pinned by
  `tests/behavioral/contracts/test_view_timeouts.py:47`), and no form view is
  persistent (`bot.add_view` is only used for `GreetingsView`, `app/cogs/events.py:46`).
  After expiry or a deploy the buttons remain rendered on the ephemeral message but
  discord.py has no view to dispatch to → interaction failed. Interaction *tokens*
  also expire at 15 minutes, so between 15 and 30 minutes some followup-based paths
  (`_send_layout_view`'s `followup.delete_message`, `form.py:1161`) work only because
  they use the *new* interaction's token — any path that reuses an older token fails.

**Possible causes with supporting evidence (Strong inference).**

- **P1 — Concurrent dispatch, no latch.** discord.py schedules each component
  interaction as an independent task; nothing in the engine serialises them or marks
  a session as "processing". Double-click on the card's Done runs `on_done` twice →
  `form._callback` twice → two `advance()`s (`summary_card.py:163-165,881-890`,
  `form.py:104-117`). Double-click on the review Confirm runs `_finish` twice → two
  `insert_cog_event` documents, two `persistence_callback` calls and two external
  subscribe attempts (`form.py:846-891`). The second interaction then fails on
  `edit_original_response` or the deleted message → user sees an error on a form that
  "already worked".
- **P2 — Reused deterministic `custom_id`s.** `SummaryCardView._render` rebuilds
  buttons with the same ids on every state change (`summary_card.py:101-143`), and
  `DesignSelectView`, pickers and pagination use fixed ids. A click captured against a
  previous render is routed by discord.py to the *current* view's handler with the
  same id. For cards that means "customize section N" of the new state; for
  `OptionsButton` (random ids) it means a silent failure.
- **P3 — Replace-message paths that swallow failures.** LayoutView ↔ embed transitions
  delete and resend (`form.py:1128-1133,1161-1162`); `Manager.update_command` and
  `disable_callback` swallow the delete/edit error (`manager.py:151-154,290-293`). When
  the delete fails (token expired, ephemeral already dismissed) the old message stays
  with live-looking buttons bound to a stale or stopped view.
- **P4 — Back navigation corrupts answers.** `_go_back` pops exactly one entry from
  `responses` per step (`form.py:1072-1073,1080-1081`), but `multi_select` produces one
  entry per select (`form.py:239-262`) and cards produce many (`:375-394`). The leftover
  entries feed `_should_skip_step`, `update_resume`, `_parse_responses_to_cog` and card
  hydration (`summary_card.py:838`). This is a direct source of "incorrect state
  updates" and "messages representing outdated state".
- **P5 — Stale in-memory documents.** `Manager.cogs` is a snapshot from Redis/Mongo at
  panel open; `add_item_callback` appends to it and writes the whole document with
  `$set` (`manager.py:337-338`, `data/cogs.py:43-47`); `RemoveItem` mutates it in place
  before the write (`remove.py:116-118`). Two open panels (same admin twice, or two
  admins) → last writer wins, items lost.

**Unlikely.** Multiple bot instances (single `commands.Bot`, one container); Discord
retrying component interactions (Discord delivers each interaction once; retries are
for webhooks); YAML cache mutation (no in-place mutation of step dicts found;
`summary_card.py:690` deep-copies).

**Unknown.** The production distribution of these causes: the repo has no error
metrics (`app/cogs/prometheus.py` exists on this branch — the review did not audit its
counters), and logs are not in the repo. See §25.

### 13.2 Concurrency and consistency scenarios (current behaviour)

| Scenario | What happens today | Evidence |
|---|---|---|
| Click A then B (sequential, fast) | Works if A's edit lands before B's dispatch; otherwise B's `interaction.message` is the pre-A message and B's callback edits it again (double render) or fails (`40060`/deleted). | `form.py:1094-1114`, discord.py task-per-interaction |
| Click A twice (double click) | Two tasks. Options button: toggles on then off (deselect). Confirm/Done: double advance or double `_finish` (P1). | `buttons.py:57-73`, P1 |
| Two simultaneous interactions on one form | No lock; interleaved mutation of `Form.responses`, `step_index`. | G1/G2 |
| Interaction on an outdated message | Random ids → interaction failed; deterministic ids → routed to the current handler (P2). | P2 |
| Process restart mid-form | All sessions lost silently; buttons remain (C2). | C2 |
| Multiple bot instances | Not a deployment mode today. | `docker-compose.yml` |
| DB succeeds, Discord fails (final edit) | `_finish` falls back to `followup.send`; `Manager.*` swallow; state committed, user may see "thinking…" if both fail. | `form.py:900-903`, `manager.py:151-154` |
| Discord succeeds, DB fails | `_finish`: moderations may be `True` with no cog doc (insert after moderations); user sees `on_error` log only, message stays deferred. `disable`: unsubscribe done, then delete fails → subscriptions gone, config remains. | `form.py:860-865`, `manager.py:250-262` |
| External API fails in `pre_finish_step` | Exception propagates → `on_error` → nothing persisted, deferred message never resolved. | `form.py:851,905-941` |

Mechanisms justified by these scenarios: per-session lock, `event_id` dedup,
`expected_revision`, explicit status, `on_timeout` finalisation, unblocking the loop,
document-level `revision` or array operators for item lists. **Not** justified: CAS on
Discord messages, transaction log, distributed locks, retries with backoff beyond one
followup fallback, reconciliation jobs.

### 13.3 Side-effect boundaries

`Form._finish` (`form.py:846-903`), in order, with what a failure at each step leaves:

| N | Operation | If N fails | State after N-1 succeeded, N failed |
|---|---|---|---|
| 1 | `interaction.response.defer(ephemeral=True)` | rare | nothing |
| 2 | `pre_finish_step` → Twitch/YouTube subscribe or StreamElements HTTP (sync) | exception | external subscription may exist; nothing saved; message deferred forever |
| 3 | `_parse_responses_to_cog` | pure | — |
| 4a | `persistence_callback` (birthday: config upsert + reminder API + items) | exception mid-way | partial birthday state (config saved, reminders half-created) |
| 4b | `update_moderations_by_guild(True)` | exception | subscription exists, nothing else |
| 5 | `insert_cog_by_guild` | exception | **moderations enabled, no config** → manager shows setup form again; `/setup` shows enabled |
| 6 | `insert_cog_event` | exception | config saved, no history entry; user never sees the final embed |
| 7 | `edit_original_response` | exception | falls back to followup (ok) |

`Manager.disable_callback` (`manager.py:239-293`): unsubscribe external →
custom disable → `unpause` → `delete_cog` → event → send → edit. A failure after
unsubscribe leaves a configured feature with no subscription.

`Manager.add_item_callback` (`manager.py:316-375`): duplicate check on stale snapshot
→ `pre_finish_step` (external) → append + full `$set` → event → edit. Lost-update
window between snapshot and write.

---

## 14. YAML architecture review

What YAML is today: **definition** (steps, copy, options, fields, selects, designs,
sections), **light behaviour** (`condition`, `required`, `unique`, `auto_confirm`,
`reset-on-change`, `template-vars`, `response_transform`, `validation`), **UI
configuration** (`style`, `select: true`, `emoji`, `header.thumbnail` by constant
name, `multiline`, `enumerate`, `max_length`), and **a few reflective escapes**
(`from: interaction, attr: guild.name`; `thumbnail: BIRTHDAY_GIF` resolved with
`getattr(KeikoIcons, ...)`, `summary_card.py:798-799`).

Semantic ambiguities found:

- `style` means (a) how to format a value in summaries, (b) which Discord component
  to use in `selects[]`, (c) a persisted tag in the document, (d) `composition`.
- `key` on a `configuration_card` field is sometimes a state key, sometimes a
  persisted key, sometimes a hidden part (`month`/`day` vs `date`).
- `hidden: true` on a step means "not in the edit picker and not listed in the intro"
  (`utils.py:121-126`, `form.py:448`), on a field means "not rendered in summaries".
- `required` on a step vs on a card (`required: [keys]`) vs on a modal field.
- `condition.not_in` with three spellings of false because the options step stores
  `True`/`False` as strings sometimes (`reminders_birthday.yml:288-292`).
- `select: true` turns `channels`/`roles` into a different component; `available_roles`
  is a separate action that also honours `select`.
- Actions `month_select` and `summary_card` are registered but dead
  (`docs/form-configuration.md` §3).

Validation today: none at load; contract tests cover dispatchability, validators,
transforms, styles, locales, condition ordering, section types
(`tests/behavioral/test_form_yaml_contracts.py`). No unknown-field rejection, no
typing, no defaults declaration, no version, no CI-vs-startup distinction (the tests
are the CI check; startup trusts the file).

Target contract (§4.8) in one sentence: **YAML declares structure, copy, constraints
and *names* of registered behaviours; it never contains an expression, a path into a
runtime object, or a Python identifier that is not in a registry.**

---

## 15. Extension architecture review

How product-specific behaviour enters the engine today, ranked from most to least
disciplined:

1. **Registries by name** — `ModalValidations` (getattr, `modals.py:220-229`),
   `RESPONSE_TRANSFORMS`, `format_values_by_style`, `SECTION_TYPES`,
   `MultiSelectView` types. Good shape; wrong granularity in places (validators are
   methods on a class that also holds `cogs`; `validate_date` reaches into
   `cogs["responses"]`, `modals.py:765-772`).
2. **Hook parameters** — `persistence_callback`, `settings_provider`,
   `lifecycle_callbacks` (`moderations.py:104-133`). Good idea, but they receive the
   *views* (`manager_view`, `edited_form_view`) and therefore can read and mutate
   engine internals (`reminders_birthdays.py:184-185,520-525`).
3. **Constants consulted by the engine** — `COMPOSITION_COMMANDS_LIST`,
   `COMPOSITION_MAX_LENGTH`, `COMMAND_KEY_TO_COMPOSITION_KEY`,
   `ExecuteCommandButton.COMMAND_SERVICES`. Configuration that belongs in the YAML
   or in a feature registration.
4. **Branches inside the engine** — `pre_finish_step`, `_start_preview_pregeneration`,
   `parse_cogs_to_modal` date split, `_parse_cogs_to_select` month, `manager.py`
   twitch/youtube unsubscribe. Documented as exceptions in
   `docs/form-configuration.md` §7; still live.
5. **Attribute injection between views** — the undocumented protocol
   (`edited_form_view`, `form_view`, `composition_index`, `prefilled_*`, `all_cogs`,
   `composition_responses`, `_original_embed`, `after_callback`). This is the real
   extension mechanism today and it is invisible.

The target replaces 2–5 with one `FeatureModule` protocol and keeps 1 as registries
of pure functions.

---

## 16. Architecture alternatives

### Option A — Harden the existing architecture

Keep `Form(discord.ui.View)`, `FormStateManager`, `Manager`, the view classes and
the hook parameters. Add: a per-form processing latch and `event_id` dedup inside
`Form._callback`; `on_timeout` finalisation on every view; `to_thread` for sync I/O
and removal of `time.sleep`; a typed YAML schema validated at startup; fixed
`custom_id`s; a fix for `_go_back`; array operators or a document revision for item
lists; session ids in logs; move the §7 exceptions into `lifecycle_callbacks`.

- Stays: everything structural. Changes: ~15 local edits.
- Migration: trivial, no compatibility concern.
- Risks: low per change; the class of "shared helper change breaks another form"
  remains, because behaviour still has nowhere else to go.
- Bugs reduced: C1, C2, P1 (partly), P3 (partly), P4, P5. Not G1–G3, G6, G15.
- Future velocity: unchanged; each new feature still touches the engine.

### Option B — Introduce a dedicated form engine (recommended)

Extract `FormSession`, `Event`, `Effect`, `Screen`, `Engine.decide` and a
`SessionStore` as pure Python; turn the existing views into renderers of `Screen`
and the existing `show_*`/`_finish`/`Manager` callbacks into an adapter that maps
interactions to events and executes effects; replace hook parameters and constants
with `FeatureModule`; keep YAML with a schema and a version; migrate forms one at a
time behind the same `send_command_form_message` / `send_command_manager_message`
entry points.

- Stays: YAML files (with additive schema), component views (as renderers),
  `app/data` and services, the behavioral harness (as adapter tests), the persisted
  document shape (read via `from_document`, written unchanged at first).
- Changes: `Form`, `FormStateManager`, `Manager`, `EditCommand`, `RemoveItem`,
  `FormComposition` are replaced by engine + adapter over several stages.
- Migration: incremental; both engines coexist behind the entry points, selected per
  command key.
- Risks: medium; the LayoutView ↔ embed transition and the composition mode are the
  two places where the adapter must reproduce today's Discord choreography exactly.
- Bugs reduced: all of §13 by construction, plus G1–G7, G10–G16.
- Future velocity: new form = YAML + optional `FeatureModule`.

### Option C — Redesign around a persisted workflow / state-machine platform

Option B plus: sessions persisted in Mongo/Redis and resumable after restarts,
persistent Discord views (`timeout=None` + `bot.add_view`) with sessions rebuilt from
storage, an append-only transition log per session, a generic workflow graph
(branches, parallel steps) in YAML.

- Stays / changes: as B, plus a storage model for sessions and a message registry.
- Migration: L–XL; every message becomes a durable pointer.
- Risks: high — ephemeral messages cannot be re-fetched after restart, so
  "resume" would mean "send a new message"; persistent views require stable
  `custom_id`s for every component including modals; the workflow-graph generality
  has no consumer among the seven forms.
- Bugs reduced: same as B plus C2-after-restart (partially).
- Future velocity: the same as B for the forms Keiko has; slower for everything else
  because of the extra machinery.

---

## 17. Side-by-side comparison

| Dimension | Current | Option A | Option B | Option C |
|---|---|---|---|---|
| Architectural coherence | Low (View = engine) | Low+ | High | High |
| State safety | None | Latch + dedup | Session + revision + status | Same + durable |
| YAML safety | Tests only | Schema at startup | Schema + version + compiler | Same + graph semantics |
| Extension model | 5 mechanisms | 3 (hooks + registries + constants) | 1 protocol + registries | Same |
| Testing | Harness only | Harness + a few unit | Pure engine tests + harness for adapter | Same + storage tests |
| Concurrency safety | None | Per-form latch | Lock + dedup + revision | Same + durable dedup |
| Migration risk | — | Low | Medium (incremental) | High |
| Implementation effort | — | S–M total | M–L total, staged | XL |
| Future feature velocity | Low | Low | High | Medium |
| Debuggability | Interaction id + guild | + session id | Full session trace | Full + history |
| Survives restart | No | No (visible expiry) | No (visible expiry) | Partially |

---

## 18. Recommended target architecture (Option B in detail)

### 18.1 Core types (pseudo-code, no Discord imports)

```python
# app/forms/definitions.py
@dataclass(frozen=True)
class StepDef:
    key: str
    kind: str                      # "text" | "single_choice" | "channel_pick" | "role_pick" | ...
    spec: Mapping[str, Any]        # kind-specific, validated by the kind's schema
    copy: Copy                     # title/description/footer per locale
    required: bool = False
    condition: Condition | None = None   # {key, one_of | not_one_of}

@dataclass(frozen=True)
class FormDefinition:
    key: str
    version: int
    steps: tuple[StepDef, ...]
    items: ItemsSpec | None        # composition: max, unique_by, steps


# app/forms/session.py
class Status(Enum): ACTIVE, AWAITING, COMMITTING, COMPLETED, CANCELLED, FAILED, EXPIRED

@dataclass(frozen=True)
class Answer:
    raw: Any                       # machine value(s)
    parts: Mapping[str, Any] = {}  # multi-part steps (cards, keyed modals)

@dataclass(frozen=True)
class FormSession:
    id: str
    definition: tuple[str, int]
    mode: Mode                     # Setup | Edit(keys) | AddItem | EditItem(index)
    origin: Origin                 # guild_id, user_id, locale
    status: Status
    cursor: str | None             # current step key
    answers: Mapping[str, Answer]
    revision: int
    seen_events: tuple[str, ...]   # last N interaction ids
    expires_at: datetime


# app/forms/engine.py
def decide(defn: FormDefinition, session: FormSession, event: Event, ctx: Context) -> Decision:
    if event.event_id in session.seen_events:
        return Decision(session, [Rerender()])
    if event.expected_revision is not None and event.expected_revision != session.revision:
        return Decision(session, [Rerender(notice="stale")])
    if session.status not in (ACTIVE, AWAITING):
        return Decision(session, [Rerender(notice="closed")])
    ...
    match event:
        case Answered(step_key, payload):
            step = defn.step(step_key)
            parsed = KINDS[step.kind].parse(step, payload, ctx)
            if isinstance(parsed, ValidationError):
                return Decision(session, [ShowError(parsed.key)])
            s = session.with_answer(step_key, parsed).advance(defn)
            return Decision(s, [render(defn, s)])
        case Back():
            s = session.back(defn)
            return Decision(s, [render(defn, s)])
        case ReviewConfirmed():
            s = session.with_status(COMMITTING)
            return Decision(s, [Commit(s)])
        ...
```

### 18.2 Adapter (owns everything Discord)

```python
# app/forms/adapters/discord/executor.py
async def handle(interaction, session_id):
    async with locks[session_id]:
        session = store.get(session_id)
        event = to_event(interaction, session)          # reads custom_id "k:<sid>:<rev>:<action>"
        ctx = await prefetch(defn, session, event)       # guild roles, feature lookups, in to_thread
        decision = decide(defn, session, event, ctx)
        store.put(decision.session)
        for effect in decision.effects:
            await execute(effect, interaction, decision.session)   # render/modal/error/commit/finalize
```

`execute(Commit)` calls `FeatureModule.commit(session, ctx)` in `to_thread` when the
module is sync, then produces `Finalize(result)`; a raised exception moves the
session to `FAILED` and renders the error screen with the session id.

### 18.3 Feature protocol

```python
class FeatureModule(Protocol):
    key: str
    def to_document(self, answers: Mapping[str, Answer], origin: Origin) -> dict: ...
    def from_document(self, doc: dict) -> Mapping[str, Answer]: ...
    async def commit(self, session: FormSession, ctx: Context) -> CommitResult: ...
    async def on_disable(self, doc: dict, ctx: Context) -> None: ...
    async def on_item_added(self, doc: dict, item: Mapping[str, Answer], ctx) -> None: ...
    async def on_item_removed(self, doc: dict, item: Mapping[str, Answer], ctx) -> None: ...
    def summary(self, doc: dict, locale: str) -> list[SummaryLine]: ...

DEFAULT = GenericCogFeature()   # today's insert_cog_by_guild path; used when a key has no module
```

### 18.4 Boundaries kept from today

`app/data/*`, `app/services/cache.py`, `app/services/{dates,transforms,compositions}.py`,
the component classes in `app/components/` and `app/views/summary_card.py` (as
renderers), the i18n stack, the behavioral harness.

---

## 19. Migration plan

Stages are independent deliverables; each leaves the product working with all seven
forms.

### Stage 0 — Safety and visibility

- **Goal:** stop the two confirmed ghost-click causes and make double dispatch
  harmless; get a session id into every log line.
- **Problem solved:** C1, C2, P1, part of P3.
- **Change:** `asyncio.sleep` / `to_thread` for `wait_for_stream_info` and the
  `integrations` clients called from handlers; a `_busy` latch + `interaction.id`
  set in `Form._callback`, `Manager.*` and `SummaryCardView.interaction_check`
  (reject with a short ephemeral notice); `on_timeout` on `Form`, `Manager`,
  `OptionsView`, `SummaryCardView`, select views that strips components and edits
  the last known message; a `session_id` (uuid) on `Form`/`Manager` included in
  `ErrorContext.extra` and in the info logs of `_finish` / lifecycle actions.
- **Files:** `notifications_twitch.py`, `app/integrations/*` call sites,
  `form.py`, `manager.py`, `summary_card.py`, `select_views.py`, `options.py`,
  `logger.py`.
- **Compatibility:** none affected.
- **Tests:** harness scenarios for double click on Done/Confirm (must not double
  advance / double persist), expiry finalisation (contract test currently pins "no
  custom timeout behaviour" — update it deliberately), an event-loop-lag assertion
  for the Twitch webhook path.
- **Risk:** Low. **Effort:** S. **Gain:** removes the most visible failures.
- **Dependencies:** none. **Rollback:** revert per file.

### Stage 1 — Make contracts explicit

- **Goal:** definitions are typed, validated at startup and in CI, versioned.
- **Problem solved:** G9, part of G14; enables Stage 3.
- **Change:** typed schema (dataclasses or pydantic) for steps and each kind's spec;
  compiler `load_definition(key) -> FormDefinition` with unknown-field rejection and
  the semantic checks now in `test_form_yaml_contracts.py`; `version: 1` added to each
  YAML; move composition limits into YAML (`items: {max, unique_by}`) and read them
  from the definition (constants become derived); alias old action names to kinds.
- **Files:** new `app/forms/definitions.py`, `app/languages/form/*.yml`,
  `utils.py:parse_form_yaml_to_dict` (returns compiled definition's raw view for
  legacy callers), `constants.py`.
- **Compatibility:** legacy callers keep receiving step dicts via a `to_legacy_steps()`
  shim.
- **Tests:** schema tests (valid/invalid files), CI test loading every definition,
  existing contract tests re-pointed at the compiler.
- **Risk:** Low–Medium. **Effort:** S–M. **Gain:** typos and drift fail fast.
- **Dependencies:** none. **Rollback:** keep the raw loader; the shim isolates callers.

### Stage 2 — Establish state ownership

- **Goal:** one `FormSession` with `answers`, `cursor`, `status`, `revision`; all
  mutations through session methods.
- **Problem solved:** G1, G2, G5, P4, P5 (for item lists).
- **Change:** introduce `FormSession` and `SessionStore`; `Form` holds a `session_id`
  and delegates `responses`/`state` to the session (`responses` becomes a computed
  legacy view: `[{key, title, value, style, hidden, _raw_value}]` derived from
  answers + definition); `_go_back` removes answers by step key; `Manager` reloads the
  document before add/remove and writes item lists with `$push`/`$pull` (or a
  document `revision` check); `FormComposition` becomes a child session with an
  explicit parent id.
- **Files:** new `app/forms/session.py`, `store.py`; `form.py`, `form_state.py`
  (deleted at the end), `manager.py`, `composition.py`, `remove.py`, `data/cogs.py`.
- **Compatibility:** persisted document shape unchanged; hooks still receive the
  same `responses` list (computed).
- **Tests:** pure session tests (advance/back/skip/answer replacement/status);
  regression scenarios for back over multi_select and cards; consumer contracts.
- **Risk:** Medium. **Effort:** M. **Gain:** stale-state class of bugs closed.
- **Dependencies:** Stage 1 (definition to derive the legacy view).
- **Rollback:** the computed `responses` keeps external shape; revert per file.

### Stage 3 — Extract the form engine

- **Goal:** `decide(definition, session, event) -> (session, effects)` pure; step
  kinds registry with `render`/`parse`; `Screen` model.
- **Problem solved:** G3, G4, G6 (engine side), G10, G13.
- **Change:** move `_update_form_step`, `_should_skip_step`, `_save_step_response`,
  `_transform_step_response`, `_save_summary_card_response`, `_go_back`, the
  validation sites, and `_parse_responses_to_cog` into engine functions; each
  `show_*` becomes `KIND.render(step, session) -> Screen`; each child view's
  `get_response()` becomes `KIND.parse(step, payload)`; `Form` becomes a thin
  compatibility façade that calls the adapter.
- **Files:** new `app/forms/engine.py`, `events.py`, `effects.py`, `screen.py`,
  `kinds/*.py`; `form.py` shrinks to the façade.
- **Compatibility:** entry points unchanged; forms opt in per command key
  (`ENGINE_V2_KEYS`), starting with `block_links`.
- **Tests:** engine table tests per kind; property tests (idempotency on
  `event_id`, revision monotonic, closed sessions reject); harness scenarios for the
  migrated form unchanged.
- **Risk:** Medium. **Effort:** M–L. **Gain:** most of the architecture.
- **Dependencies:** Stages 1–2. **Rollback:** per-key opt-in flag.

### Stage 4 — Separate Discord

- **Goal:** one adapter that maps interactions to events, encodes
  `session:revision:action` in `custom_id`s, executes effects, owns the embed ↔
  LayoutView transition, timeouts and message identity.
- **Problem solved:** P2, P3, G11, remaining G10.
- **Change:** `adapters/discord/{interactions,renderer,executor,entrypoints}.py`;
  views in `app/components` and `summary_card.py` become renderers taking a `Screen`;
  `send_command_form_message` / `send_command_manager_message` call the adapter.
- **Files:** as above; `moderations.py` entry points; `buttons.py` (fixed ids).
- **Compatibility:** cogs unchanged.
- **Tests:** the behavioral harness becomes the adapter suite (unchanged API);
  contract tests for `custom_id` codec and CV2 limits.
- **Risk:** Medium (choreography). **Effort:** M. **Gain:** stale-click safety;
  testable rendering.
- **Dependencies:** Stage 3. **Rollback:** per-key flag.

### Stage 5 — Standardise extensions

- **Goal:** one `FeatureModule` protocol; remove hook parameters, constants lists,
  engine branches, attribute injection.
- **Problem solved:** G6, G7 (feature side), I7, I10.
- **Change:** `features/<key>.py` implementing the protocol; `GenericCogFeature` as
  default; `pre_finish_step` → `commit`; `lifecycle_callbacks` → methods;
  `settings_provider` → `summary`; `COMMAND_SERVICES` → feature registry; validators
  become pure functions with `needs`.
- **Files:** `app/services/{reminders_birthdays,notifications_twitch,notifications_youtube_video,welcome_messages,stream_elements}.py`, `constants.py`, `modals.py` (validators), `form.py`/`manager.py` leftovers.
- **Compatibility:** feature by feature.
- **Tests:** feature module unit tests (to/from document round-trip, commit result);
  consumer contracts across all keys.
- **Risk:** Low–Medium. **Effort:** S–M. **Gain:** no engine edits per feature.
- **Dependencies:** Stage 3. **Rollback:** keep old hook path per key.

### Stage 6 — Migrate the remaining forms and delete the old engine

- **Goal:** all seven forms on the new engine; delete `form_state.py`, old `Form`
  internals, `EditCommand`/`RemoveItem` (become modes), dead actions.
- **Order:** block_links → default_roles → stream_elements → welcome_messages →
  notifications_twitch → notifications_youtube → reminders_birthday.
- **Tests:** every existing scenario runs against the new engine; snapshot of
  persisted documents per form before/after (the harness `expect_persisted`).
- **Risk:** Medium for birthday (most surface). **Effort:** M–L cumulative.
- **Dependencies:** Stages 3–5. **Rollback:** per-key flag until deletion.

---

## 20. Effort vs gain

| Change | Pain solved | Effort | Risk | Gain | Leverage |
|---|---|---|---|---|---|
| Unblock the event loop (`to_thread`, no `time.sleep`) | interaction-failed storms | S | Low | High | Medium |
| Latch + `interaction.id` dedup per view | double advance / double commit | XS | Low | High | Low |
| `on_timeout` finalisation | silent expiry ghosts | XS | Low | Medium | Low |
| Fix `_go_back` (remove by step keys) | stale answers | XS | Low | Medium | Low |
| Fixed `custom_id`s; i18n confirmation word | mis-routed / locale-dependent | XS | Low | Low | Low |
| Item-list writes via `$push`/`$pull` or doc revision | lost items | S | Low | Medium | Medium |
| Session id in logs | undiagnosable incidents | XS | Low | Medium | Medium |
| Typed YAML schema + compiler + version | drift, typos, dead paths | S–M | Low | Medium | **High** |
| `FormSession` + store (single state) | whole stale-state class | M | Medium | High | **High** |
| Engine `decide` + step kinds + `Screen` | testability, isolation | M–L | Medium | High | **High** |
| Discord adapter + `custom_id` codec | stale-click safety | M | Medium | High | High |
| `FeatureModule` protocol | shotgun surgery | S–M | Low | High | **High** |
| Composition as child session | most fragile path | M | Medium | High | Medium |
| Persisted sessions / persistent views | survive restarts | XL | High | Low | Low |
| Generic workflow graph in YAML | none today | L | High | None | None |

**Quick wins:** rows 1–7. **Foundations:** schema/compiler, `FormSession`, session id.
**Structural:** engine, adapter, `FeatureModule`, composition. **Optional
sophistication:** persisted sessions, workflow graph.

---

## 21. Before vs after

| Dimension | Today | Recommended |
|---|---|---|
| Creating a new form | YAML + constant + service module + `COMMAND_SERVICES` + cog + language files; know 4 hook kinds | YAML + cog + language files; `FeatureModule` only if persistence is not generic |
| Custom behaviour | hooks receiving views, engine branches, constants | `FeatureModule` methods + registered pure validators |
| State ownership | `Form.responses` + `FormStateManager` + view dicts + `Manager.cogs` | `FormSession` in `SessionStore` |
| Lifecycle | implicit (`hasattr`, booleans) | explicit `Status` enum with transition table |
| YAML validation | tests only, cached raw dicts | compiled, typed, versioned, startup + CI |
| Discord interactions | handled inside engine methods | adapter; engine never imports discord |
| Stale events | routed or failed silently | rejected by `expected_revision`; re-render with notice |
| Duplicate events | double advance / double commit | `event_id` dedup + per-session lock |
| Persistence | inline sequences, full `$set` | `Commit` effect → `FeatureModule.commit` → `CommitResult`; array ops for items |
| Testing | harness only | engine unit + property tests; harness for adapter |
| Debugging | interaction id + guild | session id, revision, step, event, effect outcome |
| Adding features | touch engine + shared helpers | YAML + feature module |
| Files touched (simple form) | 5–6 | 3 |
| Risk of regressions | high (shared helpers) | low (features isolated; engine covered by pure tests) |

---

## 22. Testing strategy for the target

| Level | What | Without Discord? | Seeds already in repo |
|---|---|---|---|
| Schema | every YAML compiles; invalid fixtures rejected (unknown field, missing locale, bad condition) | yes | `test_form_yaml_contracts.py` |
| Semantic config | conditions reference earlier keys; registries resolve; composition limits | yes | same |
| Engine tables | `(defn, session, event) → (session', effects)` per kind, incl. back, skip, conditional, card required, transform round-trip | yes | `tests/test_form.py`, `tests/test_form_state.py` (to be rewritten) |
| Invariants / property | revision strictly increases; replay of `event_id` is a no-op; closed sessions reject; `answers` keys ⊆ definition keys; render is deterministic | yes | none |
| Extension contracts | each `FeatureModule`: `from_document(to_document(x)) == x`; `commit` result shape; each validator pure | yes (mock integrations) | `tests/mocks/*` |
| Idempotency / concurrency | two identical events concurrently → one transition; stale revision → rejected; lock serialises | yes | none |
| Adapter | `custom_id` codec; Screen → components within CV2 limits; embed ↔ LayoutView choreography; timeout finalisation | harness | `tests/behavioral/harness/`, `test_components_v2_limits.py`, `test_view_timeouts.py` |
| Discord integration | live smoke on a test guild | no | documented in `docs/testing-strategy.md` |
| Regression | one scenario per incident, named by contract | mostly engine-level now | `tests/behavioral/regressions/` |

---

## 23. Observability strategy

Identifiers that make one form lifecycle reconstructible (each is either already
available or costs one field): `command_key`, `definition_version`, `session_id`,
`session_revision`, `mode`, `status`, `step_key`, `event_type`, `interaction_id`,
`message_id`, `guild_id`, `user_id`, `effect` (name + outcome + duration). Not
useful: full answers (PII, noise), component custom ids beyond the decoded action,
embed contents.

Emit: one info line per `decide` (`session_id rev step event → status effects=[...]`),
one line per effect execution (ok / failed + exception class), one warn per rejected
event (stale / duplicate / closed) with the reason, one error per failed commit with
the `CommitResult` so far. Add to `ErrorContext.extra` (`app/exceptions.py`) the
session fields so the Discord log channel (`app/logger.py:219-247`) shows them.
Metrics (the branch already carries `app/cogs/prometheus.py`): event-loop lag
(directly measures C1), sessions opened/completed/cancelled/expired/failed by
command key, rejected events by reason, commit duration by feature.

---

## 24. Immediate improvements (safe to start now)

1. Replace `time.sleep(15)` with `await asyncio.sleep(15)` and make
   `wait_for_stream_info` async (`notifications_twitch.py:174-189`); wrap
   `bot.twitch.*`, `StreamElementsClient.*`, `requests.get` in
   `asyncio.to_thread` at the handler call sites (`modals.py:737,747`,
   `form.py:940`, `welcome_messages.py:188`).
2. Add a processing latch and `interaction.id` memory to `Form._callback`,
   `Form._finish`, `SummaryCardView.interaction_check`, `Manager` callbacks.
3. Implement `on_timeout` on form/manager/card/select views: strip components and
   edit the last message with an "expired" notice.
4. Fix `_go_back` to remove answers by the step's produced keys, not by popping one.
5. Add a YAML schema check at startup that fails on unknown `action`, unknown
   `validation`/`response_transform`/`style`/section `type`, missing locales.
6. Stop using localized labels as `custom_id`s; take the confirmation word from an
   i18n key.
7. Log a per-form `session_id` (uuid4 at `Form.__init__` / `Manager.__init__`) in
   every engine log line and in `ErrorContext`.
8. Write the regression scenarios for: double click on card Done, double click on
   review Confirm, back over `multi_select`, two manager panels adding items.

None of these changes the persisted shape or the YAML files, except (5) which only
reads them.

---

## 25. Long-term opportunities (after the foundation)

- Per-guild draft persistence for long forms (only if users ask for "continue where I
  left off"); it becomes a `SessionStore` implementation, not an engine change.
- A `/setup` dashboard that opens any form in *edit* mode for a single field
  directly (mode = `Edit([key])` is already a first-class concept).
- Non-Discord front-ends (a web admin page) reusing `FormDefinition`, `Engine` and
  `FeatureModule` with a different adapter.
- Definition upgrades: `from_document` per version enables schema migrations of saved
  configs without touching the engine.
- Generic "review" screen with per-field edit buttons rendered from the definition
  (replaces `EditCommand`'s select picker).

---

## 26. Open questions (not provable from the repository)

1. What share of reported ghost clicks happens (a) >30 min after opening, (b) right
   after deploys, (c) during Twitch online events? This decides how much of the pain
   Stage 0 removes.
2. Do two admins ever manage the same feature concurrently in practice? Decides
   whether item-list array ops are a quick win or optional.
3. Must a form survive a bot restart? (Product decision; drives Option C.)
4. Is the 15-minute interaction-token vs 30-minute view-timeout gap intentional?
5. Are there saved documents in production whose shape predates the current
   `{style, values}` convention (migration notes exist in `~/task-state`; the repo
   has none)? Decides the `from_document` versioning scope.
6. Which metrics does `app/cogs/prometheus.py` already export on this branch?
   (Not audited here; event-loop lag and session counters are the ones the review
   needs.)

---

## 27. Recommended execution order

```
Do first (Stage 0, days)
  unblock the event loop · latch + dedup · on_timeout · _go_back fix · session_id in logs
  · regression scenarios for double click / back / concurrent panels
        ↓
Do next (Stage 1–2, weeks)
  typed YAML schema + compiler + version · FormSession + SessionStore behind Form
  · item-list writes with array ops · composition as child session
        ↓
Do after stabilisation (Stage 3–5)
  engine.decide + step kinds + Screen · Discord adapter + custom_id codec
  · FeatureModule protocol; migrate block_links → … → reminders_birthday
        ↓
Optional later (Stage 6+ / Option C pieces)
  delete legacy engine · persisted sessions only if the product asks for resume
  · non-Discord adapter
```

---

## Appendix — The 25 questions, answered in one line each

1. **Build from zero?** A pure engine over compiled definitions and identified,
   versioned sessions, with Discord as an adapter and features as modules (§4, §6).
2. **Assumptions we would drop:** Form = View; handlers mutate state; feature
   branches in the engine; presentation tags in storage; nested `Form` compositions
   (§3, §12).
3. **What is a Form:** a single-actor, short-lived guided configuration session that
   commits one document and some domain actions (§2).
4. **Is YAML representing the right things:** mostly yes; it leaks behaviour and
   reflection in a few places (§14).
5. **YAML responsibilities:** structure, copy, constraints, names of registered
   behaviours, version (§4.8).
6. **Never in YAML:** expressions, attribute paths, callables, persistence logic,
   Python names outside registries (§4.8).
7. **Single source of truth:** the `FormSession` in the store; documents after commit
   (§4.4).
8. **Lifecycle states:** ACTIVE, AWAITING, COMMITTING, COMPLETED, CANCELLED, FAILED,
   EXPIRED (§4.5).
9. **Transitions:** the table in §4.5.
10. **Invariants:** I1–I12 (§5).
11. **Who mutates state:** only `Engine.decide` (§5 I2).
12. **Why one form breaks another today:** behaviour can only live in shared helpers
    and engine branches (§8.1, §11 G6).
13. **Why ghost clicks:** blocked event loop, silent expiry/restarts, no latch, reused
    ids, swallowed replace failures (§13.1).
14. **Can duplicates be safe:** yes with `event_id` dedup + per-session lock + status
    (§4.6).
15. **Partial failures:** today mixed states (§13.3); target: `Commit` effect with
    `CommitResult` and FAILED status (§6.3, §6.4).
16. **Product-specific extension:** `FeatureModule` + pure registries (§4.7, §18.3).
17. **Public API:** YAML schema, `FeatureModule`, registries, test helpers (§4.9).
18. **Discord coupling:** adapter only (§4.10).
19. **Accidental architecture:** §10 cross-view links, §15 items 3–5, §12.
20. **Preserve:** YAML-first, one entry point per mode, component views as
    renderers, the harness, `app/data`, transforms/formatters registries.
21. **Replace:** `Form`/`FormStateManager`/`Manager` internals, hook parameters,
    constants lists, nested `Form` compositions, storage `style` tags.
22. **Best foundation for 20–50 features:** Option B (§17, §18).
23. **Incremental:** everything in §19 is per-stage and per-command-key.
24. **Highest leverage per effort:** schema/compiler, `FormSession`, `FeatureModule`
    (§20).
25. **What makes it boring:** a transition table you can read, tests that need no
    Discord, and a feature protocol that cannot reach the engine.


---
---

# Part II — Follow-up analysis (baseline corrected to `origin/main` @ `65f7ec1`)

Part I was written against the worktree branch `rukasudev/adding-metrics` at
`6cf2016`. `origin/main` is 175 files ahead of that commit (PRs #28–#32: block links
redesign, product analytics + traces + journeys, Components V2 manager panel, the
event-loop fix). Several claims in Part I are therefore about code that main has
already changed.

**Stance.** The code that arrived with those PRs gets exactly the treatment Part I gave
the older code: it is **evidence and a migration constraint, not the architecture**. Where
it fixes a symptom, the fix is acknowledged and then judged against the north star of
§4–§6; where it adds a concept the product needs (a journey, a catalog of events), the
concept is kept and its implementation is re-derived from zero like everything else.
Nothing below is "build on what main has". Line references prefixed with `main:` are
read from `origin/main`; unprefixed ones still refer to Part I's baseline.

## II.0 Baseline correction — Part I claims re-evaluated, and the post-pull code reviewed

### II.0.a Part I claims against main

| Part I claim | Status on main | Evidence | Judgement against the north star |
|---|---|---|---|
| C1 — event loop blocked by sync I/O and `time.sleep` | **Patched at the known call sites** (PR #32, merged 2026-09-13) | `off_loop` (`main:app/services/utils.py:30-36`) wraps Twitch/StreamElements/Mongo calls in `form.py`, `notifications_twitch.py`, `stream_elements.py`, `block_links.py`, `birthday_handler.py`; regression `test_event_loop_is_never_blocked.py` | A wrapper the caller must remember is not I12. Every other `pymongo`/`redis`/`requests` call in `app/data`, `cache.py`, validators and the manager still runs on the loop. From zero: an async data layer; `off_loop` does not exist. |
| C2 — silent expiry, dead buttons | **Reported, not fixed** | `on_timeout` → `report_abandoned()` emits `setup.abandoned`; docstring: "Nothing is said to the user — the buttons already stop responding" (`main:app/views/form_state.py:175-193`) | The user still sees live-looking buttons that fail. From zero: expiry is a transition with a visible `Finalize(expired)`. |
| P1 — no latch against double dispatch | **Partially, and deliberately not for navigation** | `ActionCooldown` for informational buttons only; "Navigation buttons … have NO cooldown: double-clicking them is protected behavior" (`test_view_action_cooldown.py`) | Card **Done** and review **Confirm** still run twice. A cooldown per button instance is the wrong unit; the unit is the session event. |
| P2 — deterministic `custom_id`s reused across re-renders | **Unchanged** | `summary_card.py` `_render` | — |
| P3 — delete-then-send swallows failures | **Half** | `_send_layout_view` sends then deletes (`main:form.py:1368-1390`); `transition_to_embed` still deletes then sends (`main:panel_transitions.py:23-28`) | Two functions own the same choreography with opposite orders — the symptom of choreography living in views instead of in one effect executor. |
| P4 — `_go_back` pops one answer per step | **Unchanged** | `main:form.py` `_go_back` | — |
| P5 — full-document `$set` from a stale snapshot | **Unchanged** | `manager.py`, `data/cogs.py` | — |
| G16 — no session id / no trace | **Surfaces exist** (see II.0.b, II.2) | `FormSession` + `SessionAwareView`, `trace.py`, `journey.py`, `catalog.yml` | The *surfaces* (journey message, catalog, logs archive) answer real needs and are kept as product requirements. The *implementation* is a mixin on the View plus 12 emission seams — reviewed below. |
| G6 — feature branches in the engine | **Unchanged** (some moved) | `pre_finish_step` still branches (`main:form.py:1143-1160`); `COMPOSITION_*` constants now also list `block_links` | — |
| G13 — three error channels | **Improved** | duplicate-item rejection uses `response_error_embed` | `OptionsView._confirm_callback` still uses `channel.send` |
| G15 — composition as nested `Form` | **Unchanged, plus one more injected attribute** | `FormComposition(parent_context=..., parent_form=...)` | — |
| §14 YAML grammar | **Extended organically** | `condition.matches`, `description-when`, `visible-when`, `{response:key|fallback}`, `multi-select` section, `picker-description`, `options[].style` | Reviewed in II.0.b and II.4. |
| Manager is an embed + buttons | **Replaced by a Components V2 panel** | `ManagerPanelView`, `_announce_event` (`main:manager_panel.py`, `manager.py`) | Reviewed in II.0.b. |
| Code style rules | **Exist** | `.claude/rules/code-style.md` (12 rules) | Kept; II.6 extends. |

### II.0.b The post-pull code, reviewed against the north star

Same method as Part I: what it is, why it probably exists, would we choose it from zero,
what replaces it.

| New on main | What it is | Would we choose it from zero? | From-zero equivalent |
|---|---|---|---|
| `off_loop(func, *args)` (`utils.py:30-36`) | `asyncio.to_thread` behind a name; used at 12 call sites | **No.** It converts a structural property into a discipline. Two of the four original sites were missed in the same PR (PROD-ERRORS F5). | Async data layer (`motor`, async redis) and async integration clients; a lint that bans sync clients outside `app/data` and `app/integrations`. The helper is deleted. |
| `FormSession` + `SessionAwareView` mixin (`form_state.py:12-193`) | A counter object (steps viewed, back count, failures, duration) mixed into `Form` and `Manager`; `view` setter writes `owner_form` onto every child view | **No.** It is named "session" but holds no answers, no cursor, no status, no revision: it is analytics bookkeeping bolted onto the View, and a *third* holder of state next to `Form.responses` and `FormStateManager`. The setter that back-references `owner_form` is attribute injection with a decorator. | The `FormSession` of §4.1 (answers, cursor, status, revision). Friction numbers are *derived* from the event log of the session, not counted by hand. The name is reclaimed. |
| `trace.py` — one unit of work, one message; `ContextVar`; sinks | Correct product idea; implementation lives in the logging handler tree (`TraceFoldingHandler`, `DiscordLogsHandler.send_trace`) | **Concept yes, placement partly.** A trace per interaction/webhook/job is right. Deciding what a trace *contains* by folding every `logger.*` call is how "clean is not the same as silent" (the lost "Left Guild" message) happened. | Keep the trace boundary in the adapter (one trace per event handled). Trace lines for form work come from `Decision` and effect outcomes, not from folding free-text logs. |
| `journey.py` — one session, one message, edited until it ends | Correct product surface; built by observing analytics events (`register_observer`) and re-rendering a `Trace` | **Concept yes, source no.** The journey is rendered from events that 12 hand-placed seams emit; a missing seam is a missing line. | The journey is a projection of the session's `Decision` history: every transition is a line by construction. Same message, same reader experience, zero seams. |
| `analytics/catalog.yml` + `analytics.emit` + 12 engine seams (`docs/analytics.md` §2) | Closed vocabulary of 30 events, privacy by construction, storage tiers | **Catalog yes, seams no.** The catalog, the privacy rule and the storage design are right and stay as requirements. Twelve emission points inside view callbacks are the drift risk the catalog contract test cannot see. | One emission point: `Decision → events`. The catalog contract checks the engine's event table against the catalog. |
| `ManagerPanelView` (`manager_panel.py`) | Components V2 panel with a ✏️ per section; `__getattr__` proxies every unknown attribute to the `Manager`; properties forward `edited_form_view`, `form_view`, `_original_embed` | **Panel yes, plumbing no.** Per-section editing is the right product. The proxy exists so the old `Manager` callbacks keep working through the container — the same family as `parent_view.edited_form_view = …` in Part I (§10), now with `__getattr__`. | `ManagerPanel` is a `Screen`; the renderer draws it; section buttons encode `k:<session>:<rev>:edit:<step_key>`. No back-references. |
| `panel_transitions.py` | The rule "Components V2 flags are fixed at send time, so replace instead of edit" | **Rule yes, function no.** The rule is a Discord constraint and belongs in the renderer's knowledge. Having it as a helper that two callers use with opposite delete/send orders is the defect. | One `Replace` effect executor: send, then delete, log a failed delete. |
| `ActionCooldown` + `acknowledge_hot_click` (`buttons.py`) | Per-button-instance rate limit with a self-deleting notice | **No.** The unit of "the same click twice" is the session event, not the button instance; the exemption for navigation buttons proves it (they needed dedup, not a cooldown). | `event_id` dedup + `expected_revision` in the adapter; a stale/duplicate event answers with `Notice` + re-render. |
| `condition_allows` with `matches` (regex in YAML), `description-when`, `{response:key:formatter|fallback}` with `RESPONSE_TOKEN_FORMATTERS = {"host": …}`, `_condition_value` lookup order answers → cogs → `parent_context` | The condition grammar grew a regex, a copy-variant list and a template mini-language with a formatter registry of one entry | **No.** This is the "accidental programming language in YAML" of §3, one PR further along; three lookup sources for one key is implicit scoping. | The `when` grammar of II.4 with explicit scopes; copy tokens resolved from a closed formatter catalog declared in the definition schema. |
| `FormComposition(parent_context=…, parent_form=…)` | Two more injected attributes so a sub-step can read parent answers | **No.** | Child session with a scope chain (II.4). |
| `keep_cancel_button_last` | Re-anchors the Cancel button because views are built by add-order | **No.** A layout rule enforced by post-hoc list surgery. | `Screen` declares button order; the renderer obeys. |
| `hidden` copied into every response entry; `styled_values` label/raw split in `_save_step_response` | More fields on the untyped response dict | **No.** | Typed `Answer`; visibility and labels are definition facts, resolved at render. |
| `RecordsBrowser`, `base_embed`, `ViewConstants`, `DiscordLimits` | Generic view for record lists; shared embed skeleton; global tunables and limits | **Yes.** Generic, no engine coupling. | Kept as generic views/constants outside the platform; the browser becomes a renderer consumer. |
| `.claude/rules/code-style.md` | 12 maintainer-reviewed rules | **Yes.** | Kept; extended by II.6 and enforced by tooling. |
| Debug logs in `guild.logs` + daily archive + `tools/keiko logs` | Storage and query of everything logged | **Yes.** Outside the form platform; it is what made II.1 possible. | Kept unchanged. |
| `block_links` redesign (card with modes, custom rules composition, conditions, records) | The first form that exercises card + composition + conditions together | **As a form, yes; as evidence, invaluable.** It is the form that pushed the grammar past its design and produced H10–H12. | It is the first form to migrate *after* the simple ones, and the primary consumer for the `when` grammar tests. |

Net effect on Part I's conclusions: the **diagnosis stands**; two symptoms were
**patched, not removed** (C1, P3); the post-pull code **adds product surfaces the target
must preserve** (journey message, event catalog, panel with per-section editing, logs
archive) and **adds accidental architecture of the same family Part I identified**
(a third state holder, `__getattr__` proxies, attribute back-references, a growing YAML
mini-language, choreography split across helpers); the **recommended direction is
unchanged**.

## II.1 Does the target architecture eliminate the historical errors? (read from the real archive)

Sources, read in full by a dedicated analysis pass (report:
`docs/form-platform-error-history.md`, 508 lines, read-only):

- **A** — the local index of two years of daily log files, `~/.keiko/logs.db`
  (2024-07-27 → 2026-08-20; 18,054 records, 2,355 ERROR + 676 WARNING, tracebacks on
  1,656 of the errors).
- **B** — production `guild.logs` (2026-08-21 → 2026-09-13; 988 ERROR + 2,263 WARNING).

Records were grouped by signature (exception type + normalised first line + last
`app/` frame) and classified. Totals:

| Class | A (2 years) | B (24 days) | What it is |
|---|---:|---:|---|
| LOGGER_INFRA | 2,124 | 2,920 | 429s on the two log channels (2,319), werkzeug port-scanner lines (1,547), one DNS/gateway burst on 2024-12-23 (714), embed > 4096 (141), heartbeat-blocked warnings (129), restarts |
| INTEGRATION_WEBHOOK | 489 | 325 | Twitch/YouTube notification services (`get_channel` on `None` 211, bad YouTube payloads 131, 429 on message fetch 344), reminder API |
| PERSISTENCE | 266 | 0 | `KeyError 'allowed_chats'` (105 incidents, logged twice), `welcome_messages_channel` (3), `default_roles` `None` (4), Mongo Atlas outages hit through a sync `find_one` inside `on_message` (47) |
| FORM_ENGINE | 21 | 6 | 14 groups, listed below |
| DISCORD_LIBRARY | 68 | 0 | Discord 5xx bursts |
| OTHER | 63 | 0 | cog-level bugs (`/help` in DMs, error handler) |
| **Total** | **3,031** | **3,251** | |

Reading this honestly: the interactive configuration flows are **0.4 % of the error
volume** over two years. The review is not justified by error volume. It is justified by
what each of those 27 records is — one admin's configuration attempt broken mid-way —
and by the fact that the silent failure classes (wrong state after Back, lost items from
two panels, a stale click routed to the wrong handler) **do not throw and therefore
cannot appear in this archive at all**. The archive can confirm causes; it cannot rule
out the silent ones.

### Every in-scope failure group, and what the target does with it

| # | Failure (records, period) | Mechanism found in the traceback | Target mechanism | Verdict |
|---|---|---|---|---|
| H1 | `KeyError: 'allowed_chats'` in `block_links.check_message` on every message of a guild — 105 incidents (×2 logging), 2026-05-18 → 07-12 | document written before the key existed, read raw (`cogs[KEY]["values"]`) | `FeatureModule.from_document(schema_version)` is the only reader; a document the module cannot upgrade is a logged `FAILED` load, never a subscript | **Structural** |
| H2 | `KeyError: 'welcome_messages_channel'` in `send_welcome_message` — 3, 2026-03-30 | same | same | **Structural** |
| H3 | `TypeError: 'NoneType' not iterable` in `default_roles.filter_roles` — 4, 2026-04-02/03 | same (missing roles key → `None`) | same, plus typed `Answer` (a missing list is `[]`, never `None`) | **Structural** |
| H4 | Mongo Atlas `ServerSelectionTimeoutError` / `_OperationCancelled` reached through `cache.get_cog_data_or_populate → data/cogs.find_one` inside `on_message` — 47 records, 2026-03-25 → 06-25; plus 129 `heartbeat blocked` warnings whose loop traceback ends in `find_one` (55), `redis.get` (9), `requests` image fetch (6), `wait_for_stream_info` (4); one 350-second continuous block on 2026-08-31 | synchronous driver on the event loop | async data layer (`motor`, async redis) end to end; validators declare `needs` so the adapter fetches before `decide`; lint bans sync clients outside `app/data`. The outage still fails the check; it no longer stalls the gateway | **Structural** for the stall; the outage is infra |
| H5 | `select.py:update` with `self.view is None` from `/help` — 8, 2026-02-24 → 03-19 | a `Select` item detached from its stopped/cleared View before an update ran | views are throwaway renderers; nothing updates a component in place; state lives in the session | **Structural** |
| H6 | `View interaction referencing unknown view` on manager `Editar`/`Confirmar`/`Add` — 2 in A, 2 in B (2026-09-10) | a click on a View that had stopped, timed out or belonged to a previous process | `custom_id` codec resolves the click through the `SessionStore`; an expired or unknown session answers with a visible "expired" finalisation instead of a dead button | **Structural** |
| H7 | `KeyError: 'en-gb'` / `'en-US'` in `_set_titles_and_descriptions` and `parse_command_event_description` — 5, 2026-04-05 | user locale used as a dict key without fallback | `Origin.locale` normalised once when the session opens; copy resolution goes through one function | **Structural** (already fixed on main, `f964951`) |
| H8 | `DesignSelectView` edit with `content=` on a Components V2 message, hidden by an `on_error` arity bug — 1, 2026-03-09 | renderer did not know the message flavour; error handler signature drift | the renderer owns the embed ↔ Components V2 decision per `Screen`; one `on_error` path in the adapter | **Structural** |
| H9 | `IndexError` in `form._handle_subscription` during the edit flow (`manager.update_command → pre_finish_step`) — 1, 2026-04-25 | feature code indexing the shared `self.responses` list with an index computed for another list | `FeatureModule.commit(session)` receives typed answers for the item being edited; no shared list, no engine branch | **Structural** (I2, I7, I10) |
| H10 | `10062 Unknown interaction` in `manager.add_item_callback` — 1, **new in B** (2026-09-10) | `form.update_counter → _after_callback → composition.finish → parent_callback → form.update_counter → _after_callback → manager.add_item_callback → defer()`: two nested after-callbacks ran on one interaction before it was acknowledged | the adapter acknowledges each interaction exactly once, then calls `decide`; child sessions return a `Decision` to the parent, they never re-enter its callback chain | **Structural** |
| H11 | `10008 Unknown Message` in `panel_transitions.transition_to_embed` from `show_buttons` — 1, **new in B** (2026-09-09) | edit of a layout message the flow had already deleted (delete-then-edit ordering in the embed direction) | `Replace` effect executed by one adapter function: send first, then delete, failure leaves the previous message | **Structural** |
| H12 | `50035 In components.0: type must be one of (1, 9, 10, 12, 13, 14, 17)` in `_send_layout_view` from `show_design_select` — 1, **new in B** (2026-09-09) | a Components V2 payload carrying a legacy component | `Screen → renderer` validates the component tree against the message flavour at build time; pinned by the existing CV2 contract tests | **Structural** |
| H13 | `50035 In type: Value must be one of {4, 5, 6, 7, 10, 12}` on `FileUploadModal` — 4 in A (2026-07-14), 1 in B (2026-09-09), **still open in production** | a modal built with a component type Discord does not accept in that modal (discord.py master `ui.FileUpload` inside `ui.Label`) | a modal `Screen` is validated against the modal component whitelist before `OpenModal`; the first failing build fails a contract test, not a user | **Mitigated** — the rule comes from Discord and can drift; the target catches it in CI, it cannot prevent the API from changing |

Out of scope for the form platform (already fixed on main or infrastructure): the 429
storms on the log channels amplified by the port scanner, the werkzeug lines, the
2024-12-23 DNS burst, the embed-size limit, the Twitch `get_channel` on `None` (211
records, 2025-06 → 2026-03), the YouTube payload errors, the new steady 429 on message
fetch (324 in B, caller unknown), the reminder API failures.

Tally: **13 failure groups in scope; 12 removed structurally, 1 mitigated.** No group
needed a mechanism beyond §4's proposal. Three of the thirteen are **new in the last 30
days** (H10, H11, H12) and all three are transition-boundary failures — exactly the
class Part I named as the core defect (§4.3, I11).

### What the archive says about Part I's predictions

| Prediction | Archive evidence | Reading |
|---|---|---|
| P1 — double dispatch on Done / Confirm | no `40060` in two years; H10 is a cousin (one interaction consumed twice through nested callbacks) | the mechanism is real; the exact double-click symptom is not recorded |
| P2 — stale click routed to a newer view by a reused `custom_id` | none | cannot be recorded: it does not raise |
| P3 — delete-then-send strands the user | H11 (embed direction), and the 2026-09-09 session that Part I's `test_layout_view_transition` describes | **confirmed** |
| P4 — Back over a multi-answer step leaves stale answers | none | cannot be recorded: it does not raise; needs a regression scenario |
| P5 — two panels lose items | none | cannot be recorded: `$set` succeeds |
| C1 — blocked loop | 129 heartbeat warnings + H4 + the `10062` on a youtuber save (PROD-ERRORS 2026-09-10) | **confirmed**, fixed at known sites on main |
| C2 — expiry / restart leaves dead buttons | H6 (4 records, both sources) | **confirmed**, still open |

Consequence for the plan: P2, P4 and P5 must be pinned by scenarios in Phase B because
no log will ever show them; H13 must be investigated against current Discord modal
rules before Phase D, because it is the only in-scope failure the target cannot make
impossible.

## II.2 Observability — what main already has, what the target adds

Main has more than Part I described. These are the surfaces that exist today (`docs/analytics.md`), listed as **requirements the target must keep satisfying**, not as an implementation to extend (II.0.b says why):

- **Trace** per unit of work in a `ContextVar` (`app/services/trace.py`): one Discord
  message per slash command / webhook request / deferred job; listeners silent unless
  they fail or report an event.
- **Journey** per configuration session (`app/services/journey.py`): one message,
  re-rendered until the session ends, with step lines resolved to the titles the user
  saw, and a 24h history footer per feature.
- **Product analytics** with a catalog of 30 events (`app/analytics/catalog.yml`),
  emitted from 12 engine seams, privacy enforced by a test that types a secret into a
  real flow; storage split into events (90d), monthly counters (13mo) and a guild
  profile.
- **Debug logs** in `guild.logs` (30d, indexed by `session_id`) plus a daily archive
  and a CLI with full-text search and error grouping (`tools/keiko logs`).
- **Session friction** (`FormSession`): steps viewed, validation failures, back count,
  duration bucket, per-step time.
- Weekly digest and `/admin insights`.

What the target changes, from zero, keeping every surface above:

| Question an on-call engineer asks | Today (main) | Target |
|---|---|---|
| "Which click did this?" | `interaction_id` on error embeds; journey lines by time | `event_id` on every decision line; rejected events logged with reason (`stale`, `duplicate`, `closed`) |
| "What state was the form in?" | last step key (`session.last_step_key`) | `session_id`, `revision`, `status`, `cursor`, and the answer **keys** present (never values) |
| "Which version of the form definition ran?" | none — YAML is process-cached and unversioned | `definition_version` on the session and on every event |
| "Which effect failed, after which one succeeded?" | trace lines from whatever `logger.*` calls exist along the path | one line per effect: `Render`, `Replace`, `OpenModal`, `Commit`, `Finalize` with outcome and duration; `CommitResult` on failure |
| "Can I reproduce it?" | read the journey, re-click by hand in a test guild | **replay**: the journey's ordered events + the definition version are the exact input of the pure `decide`; a failing production session becomes a unit test (`replay(session_events) == expected`) |
| "Is it the engine or the feature?" | judgment from the traceback | the effect that failed says which layer: engine (`decide` raised — a bug), adapter (`Render`/`Replace` raised — Discord), feature (`Commit` raised — domain) |
| "Where do events come from?" | 12 seams listed by hand in `docs/analytics.md` §2 | **one seam**: the engine emits analytics from the `Decision` (`setup.step_viewed` = a `Render` of a new cursor; `setup.validation_failed` = a `ShowError`; `setup.completed` = a `Commit` ok). The catalog contract test then checks the engine, not 12 call sites |
| "Is the loop healthy?" | Prometheus cog exists; no metric names found for loop lag on main | event-loop lag gauge, rejected events counter by reason, effect failure counter by kind, sessions by status per feature |
| "What did the user see?" | journey lines, transcript in tests | the `Screen` that each `Render` produced is loggable as a compact structure (title + component kinds), so "messages representing outdated state" becomes diffable |

Estimated gain, honestly: the visible artefacts (journey message, catalog, logs
archive) already exist and stay as surfaces; their implementations are re-derived. The target's contribution is **determinism and
attribution** — every observation gains a revision and a layer, and a session
becomes replayable. That is the difference between "we can see it happened" and "we
can prove why", and it costs nothing extra once `decide` is pure because the
emission point is the return value of one function.

## II.3 New premise — the user-visible surface does not change by accident

Premise (added to the north star, §4 and §6):

> A migration of the engine is invisible to server admins. Every screen, every
> message, every button label, order and colour, every error copy and every
> sequence of send/edit/delete choreography that an admin can perceive stays the
> same, unless a change is listed in `docs/ux-changes.md` with a before/after and a
> reason.

Mechanism, using what main already has:

1. **Golden transcripts.** The behavioural harness already produces a deterministic
   normalised event stream (`scenario.outputs`, stability enforced by
   `test_harness.py`). Record one golden transcript per form per canonical path
   (setup happy path, validation error + recovery, back, cancel + keep, cancel +
   discard, edit one step, add item, remove item, pause/unpause/disable) — roughly
   60 transcripts. Store them under `tests/behavioral/golden/`.
2. **Equality contract.** During the migration, the new engine must reproduce each
   golden transcript byte-for-byte, except for entries listed in the UX changelog. The
   test fails with a diff, the same way the current failure messages embed the
   transcript.
3. **Classification of deltas.** `invisible` (custom_id encoding, internal ids),
   `cosmetic` (a footer line, an icon), `behavioural` (a new notice, a different
   message count). Only `cosmetic` and `behavioural` go in the changelog; `invisible`
   is asserted to be invisible by the normaliser dropping those fields (it already
   drops auto-generated `custom_id`s and timestamps).
4. **Known deltas the target will introduce** — to be documented, not hidden:
   - a short ephemeral notice on a stale or duplicate click ("this screen is out of
     date, here is the current one") — **new behaviour**;
   - an "expired" notice replacing dead buttons after timeout — **new behaviour**;
   - the required-options error moving from a public `channel.send` to an ephemeral
     embed (`options.py:96-100`) — **behavioural, a bug fix**;
   - the embed-direction transition sending before deleting — **invisible** when it
     succeeds, **behavioural** when it fails (the user keeps the old screen instead of
     losing it).
5. **Copy stays in YAML and language files.** The engine never introduces a string;
   `Screen` carries keys, the adapter resolves them through `ml()` exactly as today.

## II.4 Multi-conditional forms at several depths

What exists on main (`docs/form-configuration.md`, `main:app/services/utils.py:178-192`):

- `condition: {key, not_in, matches}` on a step — one key, AND of two operators,
  evaluated by `condition_allows`; value looked up in **answers → saved document →
  parent context** (`Form._condition_value`).
- `description-when: [{condition, en-us, pt-br}]` — same grammar for copy.
- `visible-when: {key, not_in}` on a card section — `not_in` only, against card state.
- `{response:key|fallback}` tokens in descriptions.
- Composition nesting: **one level**, with `parent_context` injected so a sub-step can
  read the parent's answers.
- Contract tests: conditions reference earlier keys; `visible-when` references card
  state keys; `description-when` references produced keys.

Limits that will bite as soon as "more of this" arrives:

| Need | Today | Gap |
|---|---|---|
| Two keys in one condition (`mode == custom AND channel set`) | impossible; only one `key` | grammar |
| OR / NOT | impossible | grammar |
| Equality / membership (`in`) | only `not_in` (hence `[false, "false", "False"]`) and regex | grammar + typed answers |
| A condition on a composition item field from the parent, or on the item count | impossible (`parent_context` is one way, parent → child) | scoping |
| Compositions nested two or more levels | `Form("")` inside `FormComposition` inside `Form`; attribute injection does not compose | structure |
| Conditional `required`, conditional `options`, conditional defaults | none (`required` is static; options are static) | grammar coverage |
| Card sections depending on other sections with `in`/`matches` | `visible-when` is `not_in` only | consistency |
| Static guarantee that conditions form a DAG and every branch is reachable | partially (references resolve); no reachability, no cycle check | compiler |
| Explaining to a user why a step was skipped (support) | nothing | observability |

Target design (added to §4.8 and §18):

```yaml
when:                      # one grammar, used everywhere a rule is needed
  all:
    - {key: mode, in: [block_all, custom]}
    - any:
        - {key: allowed_chats, present: true}
        - {key: parent.register_now, is: true}
    - not: {key: link, matches: "^https?://"}
```

- **Leaves**: `is`, `in`, `not_in`, `matches`, `present`, `absent`, `count` (for item
  lists: `{key: custom_links, count: {min: 1}}`). Values are typed (`Answer.raw` is a
  real boolean for boolean steps), so `is: true` is one spelling.
- **Combinators**: `all`, `any`, `not`, nestable.
- **Scopes**: a bare `key` resolves in the current session; `parent.key` walks up one
  child-session level (repeatable: `parent.parent.key`); `item.key` inside a
  composition step refers to the item being edited; `items.count` to the list.
- **Uniform use**: `when` (step runs), `visible-when` (section renders and counts for
  `required`), `required-when`, `description-when`, `options-when` (filter options),
  `default-when`. Same evaluator (`evaluate(rule, scope) -> bool`, pure).
- **Compiler checks**: every referenced key exists at the same or an outer scope and
  is produced by an earlier step; no cycles; every `in`/`is` value is one of the
  producing step's declared options when that step has options; warning for a branch
  no combination of declared options can reach.
- **Runtime**: evaluated inside `decide`; the `Decision` records which rules were
  evaluated and their outcome, so the journey can say "skipped `custom_links` because
  `add_custom` is false" — the answer to the support question.
- **Depth**: a composition step opens a **child session** (`mode=EditItem(i)` /
  `AddItem`) with an explicit `parent_id`; the child's `decide` receives a scope chain.
  Depth is unbounded by construction; the Components V2 limits (40 components, 4000
  chars) remain the only practical bound and are checked per `Screen`.
- **Testing**: table tests for the evaluator; a property test that generates answer
  combinations from the declared options of every YAML and asserts that every step is
  reached by at least one combination (branch coverage of the definition).

## II.5 Readiness for a YAML builder UI and AI-generated features with Discord preview

Two capabilities are implied: **(a)** a person composes a form in a UI and sees a
preview; **(b)** an AI writes the YAML from a prompt and the result is previewed in
Discord before activation. What each needs, and where the target stands:

| Requirement | Today (main) | Target (Option B) | Still needed for the builder |
|---|---|---|---|
| A formal, documented schema of the definition | none; grammar lives in the engine and in prose | typed models compiled from YAML | **export JSON Schema** from the typed models (one function), with descriptions per field; the builder validates client-side, the AI validates its own output |
| A catalogue of primitives with metadata | registries exist but are Python dicts | registries stay | each registry entry carries `description`, `params`, `example`, both locales — the builder lists them, the AI reads them |
| Render a form without Discord | impossible (rendering *is* the Discord call) | `Screen` model from `render(step, session)` | a second renderer: `Screen → HTML` (builder preview) — small, because `Screen` is already abstract |
| Preview inside Discord before activation | impossible | `Mode.DryRun`: a session whose `decide` never emits `Commit` and whose `Finalize` says "preview" | an admin command `/admin preview <definition>` that runs a dry-run session on an ephemeral message |
| Definitions from somewhere other than disk | `parse_form_yaml_to_dict` reads `app/languages/form/<key>.yml`, cached forever | `DefinitionRegistry` keyed by `(key, version)` | a `DefinitionSource` interface (`disk`, `database draft`, `inline`) — the builder saves drafts to Mongo per guild; activation promotes a draft to a versioned definition |
| Persistence without Python for a new form | generic cog storage works for the flat shape only | `GenericCogFeature` (default `FeatureModule`) with typed answers → document | no change; a generated form that needs domain actions still needs a `FeatureModule`, which is the right boundary for AI-generated code to stop at |
| Safety before activation | contract tests in CI | compile: schema + semantic + condition DAG + CV2 limits per screen + both locales present | the same compile step runs on a draft; a draft that fails to compile cannot be previewed |
| Copy quality | writing-style skill for humans | unchanged | a lint on copy (both locales, emoji conventions from the YAML contract tests) becomes part of the compiler's warnings |

Verdict: **the target is the prerequisite, and it gets ~80% of the way**. The three
builder-specific additions (JSON Schema export, `DefinitionSource`, `Mode.DryRun`) are
small once definitions are typed and rendering is a pure `Screen`. Without the target
(rendering inside `discord.ui.View`, YAML cached from disk, no schema), a builder would
have to re-implement the engine to show a preview, and an AI would have no contract to
validate against.

One caution for (b): AI-generated definitions must never reach a live guild without
the compile step and a dry-run; the compile step is the gate, and it must reject
anything outside the closed grammar (no reflective `attr:` escapes — II.4 removes the
last one).

## II.6 New premise — code style

`.claude/rules/code-style.md` (12 rules on main) already settles: no narrative
comments, where constants live, flat data layer, generic views only in `app/views/`,
generic helpers in `utils.py`, UI tunables in `ViewConstants`. The premise below is
added to the north star and extends those rules; it is enforced by tooling, not by
review alone.

> Code reads top-down without comments. Names carry the meaning; types carry the
> contract; the structure carries the flow. A comment is a defect report against a
> name.

- **Docstrings**: one sentence, present tense, on public modules, classes and
  functions only; never mid-function; never restating the signature; never history
  ("this used to…").
- **Comments**: none, except a single line for an external constraint the code
  cannot express (a Discord limit, an API quirk) — and that line names the constraint,
  not the story. The existing `# Send before deleting: …` and `# Both branches reach
  a third-party API…` blocks in `main:form.py` are examples of what the target removes
  by making the constraint structural (an effect executor that always sends first).
- **Visual rhythm**: two blank lines between top-level definitions, one between
  logical blocks inside a function, none inside a block; imports in three groups;
  line length 88; trailing commas in multi-line literals; early returns over nested
  `if`; no `hasattr`/`getattr` ladders (they are the symptom of implicit state — the
  target removes their cause).
- **Size**: a function fits on one screen; a module has one reason to change; a class
  has no method it does not need to be a class for.
- **Types**: frozen dataclasses for definitions, sessions, events, effects and screens;
  `Protocol` for extension points; no `Dict[str, Any]` crossing a public boundary.
- **Tooling** (`make lint`, CI): `ruff` (format + lint, including `D` docstring rules
  limited to public objects and one-line summaries, `C901` complexity, `ARG`, `RET`),
  `mypy --strict` on `app/forms/`, and a custom check that fails on a `#` comment
  longer than one line outside `app/integrations/`.

Why the architecture serves the style rather than fighting it: the four "unwrap
`value|values`" helpers, the `isinstance(cogs, list)` ladders, the `hasattr(self,
"after_callback")` checks and the fifteen reassignments of `self.view` exist because
state has no type. Give it a type and the code that needed a comment disappears with
the comment.

## II.7 The masterpiece path — no effort constraint

Part I optimised for migration safety with compatibility shims (a computed legacy
`responses` view, `to_legacy_steps()`, per-key engine flags, seven stages). With the
constraint removed, the shims are the wrong trade: every shim is code that exists to
be deleted and that keeps the old model alive in reviewers' heads. The path below
builds the platform clean, proves it against every existing form with the harness and
the golden transcripts, cuts over, and deletes the old engine. It keeps Option B as the
architecture (Option C's persisted sessions still buy nothing the product asked for),
and folds in every item Part I had deferred as "optional" that a masterpiece would not
leave out.

### Target, final form (deltas over §18)

- `app/forms/` is a package with **no import of `discord`** outside
  `app/forms/adapters/discord/`, enforced by a boundary test like
  `tests/tools/test_tools_boundary.py`.
- **Async end to end**: the data layer moves to `motor`; `off_loop` is deleted with the
  last sync call site. I12 becomes true by construction, not by wrapper.
- **Definition**: typed models, JSON Schema export, `version`, the `when` grammar of
  II.4, `DefinitionSource` (disk now; database drafts when the builder arrives).
- **Session**: frozen `FormSession` with `id`, `revision`, `status`, `mode`, `cursor`,
  `answers`, `parent_id`, `seen_events`, `definition_ref`; `SessionStore` interface
  with an in-memory implementation.
- **Engine**: `decide` pure; `Decision` carries the evaluated rules and the emitted
  analytics events; replay from a journey is a first-class test helper.
- **Screen** model + two renderers: Discord (embed/View or Components V2 container,
  chosen by step kind, CV2 limits asserted) and HTML (builder preview; can wait, but
  the seam exists from day one).
- **Effects**: `Render`, `Replace`, `OpenModal`, `ShowError`, `Notice`, `Commit`,
  `Finalize`; executed by one adapter function under a trace the adapter opens per
  event; one log line per effect.
- **Adapter**: `custom_id` codec `k:<session>:<rev>:<action>`, per-session
  `asyncio.Lock`, `event_id` dedup, stale/duplicate/closed → `Notice` + re-render,
  `on_timeout` → `Expired` → `Finalize(expired)` that strips components and says so.
- **Features**: `FeatureModule` protocol; `GenericCogFeature` default; per-feature
  modules for birthday, twitch, youtube, welcome, stream_elements; **no** constants
  lists, **no** hook parameters, **no** `pre_finish_step`.
- **Persistence**: documents store raw typed values plus `schema_version`; item lists
  written with array operators; `from_document` upgrades older shapes; one-off
  migration script kept outside the repo (code-style rule 9).
- **Observability**: analytics emitted from `Decision`; `revision`, `event_id`,
  `definition_version`, effect outcomes; the journey message is a projection of the
  session's decisions (same reader experience, no emission seams); loop-lag,
  rejected-events and effect-failure metrics in the Prometheus cog.
- **UX contract**: golden transcripts (II.3) and `docs/ux-changes.md`.
- **Style**: II.6 tooling in CI from the first commit of `app/forms/`.

### Phases (each ends green on the full suite)

**Phase A — Bridge patches on the old engine (one afternoon).** Only what protects
production while the new platform is built: a session latch on `Form._callback`,
`_finish` and `SummaryCardView.interaction_check`; `_go_back` removing answers by the
step's produced keys; `transition_to_embed` sending before deleting. Each with its
regression scenario (P1, P4, P3-embed). Nothing else is touched in the old engine.

**Phase B — Golden transcripts (two to three days).** Record the ~60 transcripts of
II.3 against the old engine on main. From here on they are the definition of "the
user sees the same thing".

**Phase C — Build `app/forms/` complete, in isolation (the bulk of the work).**
Definitions + compiler + JSON Schema; `when` evaluator; session + store; engine +
kinds (one kind per existing action, plus composition as child session); screen +
Discord renderer; effects + adapter; `FeatureModule` + generic + five feature modules;
observability seam. Test layers: pure engine tables, evaluator property tests,
replay tests from recorded journeys, adapter tests through the existing harness with
the engine switched by a fixture, boundary test, style tooling. The old engine keeps
serving production untouched during this phase.

**Phase D — Cut-over, all seven forms, ordered by risk in one release train.**
`block_links`, `default_roles`, `stream_elements`, `welcome_messages`,
`notifications_twitch`, `notifications_youtube`, `reminders_birthday`. The gate for
each: its golden transcripts match (with the documented deltas), the consumer contracts
pass, `expect_persisted` shapes match or the documented `schema_version` upgrade
applies. Entry points (`send_command_form_message`, `send_command_manager_message`,
`run_feature_command`) switch to the adapter; cogs do not change.

**Phase E — Delete.** `app/views/form.py`, `form_state.py` (including
`SessionAwareView`), `composition.py`, `edit.py`, `remove.py`, `manager.py`,
`manager_panel.py` (rebuilt as a `Screen` renderer without proxies),
`panel_transitions.py` (absorbed by the `Replace` executor), `ActionCooldown`,
`keep_cancel_button_last`, the four hook parameters, `COMPOSITION_*` and
`COMMAND_SERVICES` constants, the dead actions, the unwrap helpers, `off_loop`, the
12 analytics seams (replaced by the `Decision` table). `docs/form-configuration.md` is rewritten
around the new contract; `docs/analytics.md` §2 shrinks to one seam.

**Phase F — Builder readiness.** `DefinitionSource` with a database draft
implementation, `Mode.DryRun`, `/admin preview`, HTML renderer for `Screen`. Only
after E, and only when the product decides to build the UI or the AI path.

### What this path deliberately does not do

- It does not persist sessions across restarts (a restart still expires open forms,
  now visibly). The `SessionStore` interface makes that a later drop-in if the product
  ever asks.
- It does not keep two engines alive for longer than Phase D. A per-key flag exists
  only inside the release train and is deleted in Phase E.
- It does not change any user-facing copy except the four documented deltas of II.3.

### Effort vs gain, revised for "no constraint"

The question is no longer "which stage is cheap" but "what is the order that keeps
production safe while the whole thing is built". Phases A and B are the safety net;
C is where the craft goes; D is the proof; E is the reward (the code base loses about
3,000 lines of engine and gains a package a new contributor can read top-down); F is
optional and unlocks the product ideas in II.5.

## II.8 Updated answers to Part I's final questions

- **Q13 (why ghost clicks)** — archive: H6 (dead views) and H4/C1 (blocked loop) are recorded; on main: the loop is no longer blocked at the known
  sites; expiry is reported but still invisible to the user; double clicks on Done /
  Confirm, reused ids and the embed-direction transition remain.
- **Q14 (can duplicates be safe)** — unchanged; the latch is a Phase A bridge, the
  dedup + revision is the target.
- **Q20 (preserve)** — add to the list, as *surfaces and rules*, not as code: the
  journey message, the analytics catalog and its storage tiers, the manager panel with
  per-section editing, the Components V2 rule (flags fixed at send time),
  `.claude/rules/code-style.md`, the debug-logs archive and tool, `RecordsBrowser` as
  a generic view, the harness's ViewStore model. Their current implementations are
  reviewed in II.0.b and re-derived in II.7.
- **Q22 (best foundation for 20–50 features)** — still Option B; with no effort
  constraint the path is II.7, not §19.
- **Q25 (what makes it boring)** — plus: a golden transcript per path, so "did I
  change what the user sees?" is a test, not a review question.
