---
name: keiko-writing-style
description: Use when writing or editing user-facing text for the Keiko Discord bot, including slash command descriptions, embeds, modals, buttons, forms, error messages, success messages, greetings, notification copy, welcome messages, or any UI text rendered to Discord users. Applies to the shared YAML language files under app/languages/, each command's copy.yml and form.yml under app/commands/<command>/, the forms' shared vocabulary app/forms/vocabulary.yml, and any embed/modal/view construction in app/ui/. Enforces Keiko's friendly-loyal-dog companion tone, voice rules (3rd person for slash command descs, 1st person everywhere else), formatting (single leading emoji on titles, "• " footer prefix, backticked slash commands, bold **Keiko**), localization (every string in both en-us and pt-br), and Discord UI conventions (embed colors 0x4F97F9 default / 0xff0000 errors / 0xFEE75C warnings, button styles, modal patterns, form structure).
---

# Keiko Writing Style

You are writing or editing user-facing copy for the Keiko Discord bot. Apply every rule below. Do not paraphrase or soften them.

## Personality

Keiko is a friendly, loyal, cute dog companion. Warm and helpful, never childish or sarcastic. One cute beat per message; never stack them.

Subtle dog-themed wordplay is welcome when it lands naturally. Example: the pt-br greetings line `Se precisar de *aujuda*` puns "ajuda" (help) with "au au" (Portuguese for "woof woof"). Ship these sparingly and only when the joke is gentle and clear.

## Voice rules

- **3rd person ONLY** in slash command `description:` fields (Discord's command picker metadata): `Keiko {verbs} ...`.
- **1st person ALWAYS** in every embed, modal, response, error, form description, and notification: `I, my, me, I'll, I'm`.
- Never mix voices in the same string.
- Never use slang, memes, irony, sarcasm, or pop-culture references.

## Writing rules

- Sentences are short to medium (1 to 4 sentences per description).
- Use contractions (`I'm`, `don't`, `you'll`).
- Frequent `!` for instructional/success copy. Periods otherwise.
- **Never use em-dash (`—`) or en-dash (`–`).** They sound AI-like and overly formal. Rewrite with a comma, period, or split into two sentences. No existing Keiko message uses them; new ones must not either.
- **`...` (three dots) is allowed only for async/in-progress states** (e.g., `🔄 Syncing roles...`, `Loading...`). It signals "I'm still working." Do not use `...` as a stylistic ellipsis or trailing-thought device anywhere else. The one exception is the owner's own line for an expired form, `Ei... esse formulário expirou!` / `Hey... this form expired!` (`texts.notices.expired`).
- Errors begin with apology: `I'm sorry, but ...` or `Oops! ...`. Always include a recovery hint (`Please try again later`, `Check the name and try again`, or `send us a report using the /report command`).
- Success titles end with `... successfully!` or `... sent!`. Optional warm closer (`Now everyone is even cuter! 🐶`).
- Instructions address the user directly (`Choose which channel I should ...`).
- Keep it natural and simple. Avoid corporate or formal phrasing; if a sentence reads like a press release, rewrite it.
- **Explanatory clarity is Keiko's superpower.** Prefer explicit descriptive phrasing over compact labels: `Todos os links desse site` / `Só esse link específico` beats `Site inteiro` / `Link exato`. Whenever a configuration choice could be ambiguous, add a concrete backticked example showing the difference (`youtube.com` vs `youtube.com/watch?v=abc123`).
- **Explain with the user's own data, not canned examples.** When a step explains a choice about something the user just provided, echo their real input via the `${answer.<key>|<fallback>}` token in the step description (the form engine substitutes the earlier answer; the fallback shows while it doesn't exist yet). "How far does this rule go for `meusite.com.br`?" teaches instantly; a generic example makes the user translate in their head.
- **Never explain two or more choices inside one paragraph.** A wall of prose with the options bolded inline is unreadable in Discord: the user has to parse a paragraph to find which button to press. Always use the choice layout below.

## Choice layout (steps that ask the user to pick)

Whenever a step presents options whose consequences are not self-evident from the labels
(modes, matching rules, scopes), the description follows exactly three parts, separated by
blank lines (`\n\n`). A plain yes/no gate ("Want to add your own links?") needs only the
question.

1. **The question**, one line, naming what it applies to (echo the user's own data with
   `${answer.<key>|<fallback>}` when the choice is about something they just typed).
2. **One line per option**, in the same order as the buttons, each led by that option's
   emoji: `{emoji} **{exact button label}**: {what happens, in one sentence}`. The emoji and
   the label must match the button character for character, so the eye maps line to button
   without rereading. **The emoji IS the bullet: never write `- 🚫 ...`.** A `- ` bullet is
   only for options whose label carries no emoji. Never explain an option outside its line.
3. **A short closing invitation** pointing at the buttons, with one cute beat:
   `Qual você prefere? É só escolher nos botões abaixo! 🐶`.

Keep each line to one sentence. If an option needs a caveat, it belongs in that option's
line, not in a trailing paragraph. Give the option a `style:` (`success` / `danger`) when one
choice is the permissive one and the other the strict one: the card picker and the options
step both paint the button from it.

```yaml
description:
  en-us: "This rule will **always allow** `${answer.link|youtube.com/watch?v=abc123}`. Tell me how far it goes!\n\n🌐 **Every link from this website**: the rule covers the whole website, any page or video on it.\n🔗 **Only this specific link**: the rule covers only that exact address, and the rest of the website stays out of it.\n\nWhich one do you prefer? Just pick it on the buttons below! 🐶"
  pt-br: "Essa regra vai **sempre liberar** `${answer.link|youtube.com/watch?v=abc123}`. Me diga o alcance dela!\n\n🌐 **Todos os links desse site**: a regra vale para o site inteiro, qualquer página ou vídeo dele.\n🔗 **Só esse link específico**: a regra vale só para esse endereço exato, e o resto do site fica de fora dela.\n\nQual você prefere? É só escolher nos botões abaixo! 🐶"
```

**Exception: block options.** A card picker whose options each declare a `description`
draws every option as its own block (the label in bold, the description under it, a
**Choose** / **Escolher** button beside it, primary on the current choice and painted by the option's `style` otherwise) under a divider. The block links modes declare `style: primary` on both, so their two Choose buttons read the same blue and neither mode looks like the wrong one.
There the title is the question and the blocks are the option lines: write each
`description` as that option's one sentence, and add no option lines or closing invitation
to the section's `screen.description`. On the card itself every pick is a select drawn on the card, so the
same `description` reads under its option's label in the dropdown (the block links modes,
at most 100 characters); the blocks are the picker screen of a part edit. A yes or no on a
card is two buttons, **Sim** / **Não** (**Yes** / **No**), the current one primary and the
other secondary.

When the same step means different things depending on an earlier answer, do not write one
sentence covering both cases: give it `overrides:` that replace its `description` (see
`docs/form-configuration.md`), so each case reads as if it were the only one. The same
holds for copy: a text that changes with the state is the block's default plus
`overrides` on the facts that pick it (`docs/commands.md`, "Overrides and facts"), never
a second key the code chooses by name.

Pinned by `test_mode_picker_explains_each_mode_in_its_own_line`
(`tests/behavioral/scenarios/test_block_links_flow.py`, the mode options) and by the
golden transcripts: an
options step that names its own labels in prose, or that puts a bullet in front of an
emoji, fails the suite.

**Visual rhythm, generally:** any description longer than about three lines gets structure.
One idea per line, blank line between blocks, bullets for anything enumerable. A dense block
of text is a bug, not a style choice.

## Discord UI rules

### Embeds (`app/ui/embed.py`)
- Default color: `Style.BACKGROUND_COLOR` = `0x4F97F9`.
- Error color: `Style.RED_COLOR` = `0xff0000`. `response_error_embed()` auto-prefixes the title with `🚨` — do not duplicate.
- Warning color: `Style.WARNING_COLOR` = `0xFEE75C`, Discord's yellow, for a message that tells the admin Keiko changed something they did, with no error to fix (the typed mention taken out of a text, `commands.responses.mention_removed`). The Response block declares `tone: warning`; `response_embed` and the forms' `Announce` read it. Never paint an error or a success yellow.
- Title format: `"{ONE_EMOJI} {Title Case}"`. Exactly one leading emoji.
- Use `description` for short bodies. Use `add_field(name=..., value=..., inline=False)` for stacked record-style content (DM reports, button caption lists, help indexes).
- Closing line: the report line renders as `-# • {text}` subtext, always the last line of the embed, through `app.ui.embed.with_footer`: at the end of the description, or in a full-width field of its own under the fields when the embed has some. A `Response` block decides it: no `footer` draws the standard `/report` line, `footer: null` none, a text that text. An embed footer renders no markdown, so `/report` came out with its backticks showing. A domain footer that carries no markdown may still use `embed.set_footer(text=f"• {text}")`. The `"• "` prefix is mandatory either way.
- Card screens (`configuration_card`) and the manager panel are Components V2 LayoutViews, not embeds: their footer comes from the step's `footer:` (the standard one when the step writes none) and renders as `-# {text}` subtext at the bottom of the container (`app/forms/discord/views.py`). Pickers opened from a card keep the same footer. Pinned by the golden transcripts (`tests/behavioral/golden/`).
- Thumbnail: `KeikoIcons.IMAGE_01` for branded responses; `KeikoIcons.ACTION_IMAGE.get(action)` for action-specific icons.

### Buttons (`app/ui/buttons.py`)
- `ButtonStyle.green` → Confirm / positive
- `ButtonStyle.red` → Cancel / destructive (the **Desativar e apagar** / **Disable and delete** of the question the panel's Desativar opens)
- `ButtonStyle.primary` → Back / selected state / primary navigation
- `ButtonStyle.gray` / `grey` → Edit, Pause, Unpause, Preview, Help, Disable, Add (the **➕ Add** of every list too, beside its title, on the summary, the panel and a card), Remove, History, Sync
- `ButtonStyle.secondary` → form back navigation
- Reuse generic labels from `app/languages/buttons.yml` (`confirm`, `cancel`, `edit`, `add`, `remove`, `back`, `select`, `choose`, `pause`, `unpause`, `disable`, `delete_all` (read **Disable and delete** / **Desativar e apagar**), `reopen` (the **Open again** / **Abrir de novo** of an expired form), `preview`, `history`, `done`), each a `ButtonCopy` with its `label` and, for a button the Help screen lists, its `description`. Only add a new key when the action is genuinely new.
- **Cancel is ALWAYS the last button in the view.** Order: the step's own actions first
  (options, Confirm, Done, Add/Remove), then Back, then Cancel. Inside a form the engine's
  screen model builds the row in that order by construction
  (`app/forms/form/actions/action.py`, `step_buttons`); a view outside the forms appends Cancel
  last by hand. The manager panel has no Cancel: its grey **🚫 Desativar** sits right after
  **⏸️ Pausar** and asks first on a red question (**Desativar e apagar**, then **Voltar**).
  Pinned by the golden transcripts.

### Modals (`app/forms/discord/views.py`)
- Default `max_length` = 40. Cap at 100 for free-text fields.
- `placeholder` falls back to `description` if not set; otherwise use a realistic example (`"shroud"`, `"pewdiepie"`, `"No links here! :p"`).
- `multiline: true` in YAML → `discord.TextStyle.long`. Otherwise `short`.
- Repeated labels in the same modal auto-append `#1`, `#2`, etc.

### Forms (`app/commands/*/form.yml`, `app/forms/vocabulary.yml`)
- Open with `type: intro` (intro).
- Close with `type: review` titled like the intro (the feature's emoji and name) and described as `"Here's a summary of your choices so far. All set for me to turn on the `/${context.command}` command with the settings below? ✅"` / `"Esse é o resumo das suas escolhas até aqui. Tudo certo para eu ativar o comando `/${context.command}` com as configurações abaixo? ✅"`. Both are the engine's defaults (`fallbacks.review_description` in `app/forms/vocabulary.yml` and the intro's title), so the summary writes neither. Its green button is the plain **Confirm** / **Confirmar**. StreamElements is the one summary that keeps its own description: it explains the prefix with the chosen streamer (`${answer.streamer}`), which says more than the generic sentence.
- A setting the intro lists gets one line, its title then its `description` (`settings_list` in `app/forms/form/actions/intro.py`; a card lists each field's `label` and `description`), so that description reads as one line.
- A feature has one name everywhere an admin reads it, the one its slash command uses (`setup.label` in its `copy.yml`, the intro and summary titles, the greeting), with the `/setup` emoji.
- Every step shows the standard footer, `"• Use the `/report` command to tell me a bug"` / `"• Use o comando `/reportar` para me contar um bug"`, which the engine draws from `fallbacks.footer` in `app/forms/vocabulary.yml`: a step leaves `footer` out. Only a step that must show no footer writes `footer: null`.
- Every step has `title` and `description` in both `en-us` and `pt-br`; the summary, and a card titled exactly like the intro, take the intro's title from the defaults.
- A text the shared vocabulary holds (`texts` in `app/forms/vocabulary.yml`: "Channel", "Choose channel", "Title"...) is named with `${ref.<name>}`, never written again; a text three fields would write goes there first, in both locales.
- No YAML anchors or aliases (`&name`, `*name`) in a `copy.yml` or a `form.yml`: a command must be able to live as a database document, and the two files are already its two halves.
- Step types: `intro`, `text`, `single_choice`, `card`, `list`, `review` (every key a form may write is in `docs/yaml-reference.html`).

## Formatting rules

- Slash commands → backticks around their context entry, never the name typed in: `` `${context.commands.setup}` `` (reads `/setup` or `/configurar`), `` `${context.commands.report}` ``, `` `${context.commands.block_links}` `` (`/moderations block links`), a parent group `` `${context.groups.moderations}` ``.
- Bot name and emphasized nouns → bold: `**Keiko**`, `**#support**`, `**$command_name**`.
- Stressed words → italics: `*help*`.
- Examples / values → backticks: `` `ks!mouse` ``.
- Variables of a template an admin writes (welcome / notification / birthday messages) → curly braces, and only those: `{user}`, `{server}`, `{member_count}`, `{streamer}`, `{stream_link}`, `{youtuber}`, `{video_link}`, `{date}`. Keiko's own defaults of those templates and the lines that explain them name only the variables the command's code fills (`tests/test_admin_templates.py`).
- Every value Keiko fills, in any YAML file → a token of the one syntax (`docs/commands.md`, "The token syntax"):
  - a fact the code passes → `$name`: `$user`, `$command_name`, `$date`, `$feature`, `$ping`, `$roles`. Each is a declared fact: the command's own in the `facts:` of its `copy.yml`, or a global one in `app/core/global.yml` (a shared file and a form read only global ones). The name is read whole, so text glued after it needs braces: `` `${prefix}ping` ``, never `$prefixping`;
  - an earlier form answer echoed in a step description → `${answer.<key>|<fallback>}`, with the fallback whenever the text can show before the answer exists, and `:<format>` when the value needs one (`${answer.default_roles:role|Empty}`, `${answer.link:host|youtube.com}`);
  - one or many, from an answer → `${plural.<key>|<one>|<many>}`;
  - a slash command or a parent group → its context entry, `${context.commands.<key>}` or `${context.groups.<group>}`, which the loader writes in as its localized name (`/setup` in en-us, `/configurar` in pt-br); on a form screen, the other values Keiko knows (`${context.prefix}`, `${context.server_name}`, `${context.command}`);
  - a text of the shared vocabulary → `${ref.<name>}`;
  - a literal dollar → `$$` (a `$` before a space or a digit, `R$ 5`, is already text).
  The loader and the form compiler refuse an undeclared name, an answer no earlier step (or the step itself) gives, an unknown format, an unclosed `${` and the old spellings (`{context:...}`, `{answer:...}`, `{plural:...}`, `{ref: name}`), naming the closest name; `fill` refuses a line that names a given value in braces.
- Braces are the admin's, `$` is Keiko's: never write a value Keiko fills as `{name}`, and never a `$` token in a default of an admin's template that a later pass would fill (the load-time `${context...}` and `${ref...}` are written in when the file loads; nothing else is filled in it). A Keiko text may show an admin variable as an example beside its own tokens, as a step does when it lists the variables a message accepts (`` `{user}`: the new member's mention `` above a line naming `` `${context.commands.setup}` ``): to Keiko's reader the braces are plain text.
- Who a notice or a birthday post pings is the card's **📣 Mencionar** / **📣 Mention**, never the text: the inputs of those messages declare `normalize: {name: without_group_mentions}`, which takes a typed @everyone, @here or role mention out on save (a member's stays) and sends `commands.responses.mention_removed` ("📣 Tirei a menção do texto!" / "📣 I removed the mention from the text!") as an embed that removes itself, like the pause's. The Mention section comes before the messages on the card and starts on @everyone in every command.
- What a person types in a modal is shown exactly as typed on every screen, Keiko's tokens, their old spellings and any other brace included; only the variables that field's code fills (`{user}` in a welcome message, `{streamer}` in a Twitch message, never `{streamer}` in a birthday message) are filled, each value once, on its previews and in the message the feature sends (`tests/behavioral/contracts/test_a_typed_answer_fills_only_its_own_variables.py`).
- Footer prefix: `"• "` (bullet + space). Never use `•` as an inline bullet in body copy.
- Lists: `\n- ` for unordered, `\n1. ` / `\n2. ` for sequential. `\n\n` between paragraphs.
- A list of message texts the admin wrote (notification and welcome messages) reads inside one code block of `• ` lines, through the `code_block` format, on cards, summaries and panels. Lists of names and links (streamers, websites, birthdays) stay `- ` bullets.
- One leading emoji on titles. 0–2 emoji elsewhere per paragraph.

## Vocabulary

**Prefer:** *help, make, configure, manage, set up, welcome, notify, protect, block, assign, send, fetch, cozy, personalized, automatic(ally), warmly, lovingly, loyal, favorite, get started, just call me, don't hesitate*.

**Avoid:** *execute, invoke, initialize, instantiate, parameter, invalid, forbidden, malformed, kindly, please be advised*. Avoid ALL CAPS, slang, memes, ironic phrasing, em-dashes (`—`), and en-dashes (`–`).

## Localization

- Every new user-facing string MUST exist in BOTH locales, as one node with its `en-us` and its `pt-br`: a command's own string in its folder's `copy.yml` (`app/commands/<command>/`, snake_case keys relative to the command; the /setup row under the fixed `command.setup.label`, `command.setup.description`, `command.stats.title`, `command.stats.delivered.one`, `command.stats.delivered.other`, `command.slash.subgroup` and `command.slash.name`), a shared one in `app/languages/<namespace>.yml`. The loader refuses a text missing a locale, and a file its schema refuses.
- A string goes in the block of the primitive that draws it: under `command`, the fixed places the shared screens fill (`slash`, `setup`, `stats`, `panel`); under `ui`, what the command draws itself (`cards.<name>`, `records.<name>` for `RecordsBrowser`, `responses.<name>` for `response_embed`, `errors.<name>` for an error, `buttons.<name>`, `announcement`, and `texts` only for what no primitive draws; the table in `docs/commands.md`). `description` names a block's body (an embed's, a list's, a card's) or a caption Discord shows (a slash command's, a button's Help line), `value` the text a field shows, and an error's text keeps `message`.
- Python code must NEVER hardcode user-facing strings. Always: the primitive that reads the block (`response_embed("<command-key>.ui.responses.<name>", locale)`), or `ml("<command-key>.<path>", locale)` for a command's copy and `ml("<namespace>.<key>", locale)` for shared copy.
- pt-br tone matches en-us exactly: same warmth, same emoji, same 1st person, same `**Keiko**` bolding, same `"• "` footer prefix.

## Where these rules do NOT apply: operator surfaces

Everything above governs what a Discord **user** sees. Two surfaces are read only by whoever runs the bot, and they are a taxonomy for on-call, not Keiko talking:

- the `/admin` commands, restricted to `ADMIN_GUILD_ID` (`app/commands/admin/analytics.py`, `app/commands/admin/digest.py`);
- the admin log channels: trace and journey messages, the ticket `/report` posts in the reports channel, and the labels around them (`app/logger.py`, `app/core/journey.py`, `Report._create_report_embed` in `app/commands/report/cog.py`). The author's own copy of that ticket, sent by DM, is user-facing.

On those two surfaces only: English only, no `ml()` and no pt-br counterpart; no personality, no 1st person, no apology-plus-recovery shape; structural punctuation such as `—` and `·` is allowed, because the line is a record, not a sentence; and `• ` opens each item of a list in the body, which is how the weekly digest and every `/admin` report are already written. Keep every other habit — one leading emoji on a title, `"• "` on a footer, `**bold**` for names — so the messages still read as one product.

Everything else stays fully under the rules above, including anything a server member can see: an error a user triggers is user-facing even when an operator also reads it, and a notification is user-facing even when it was posted by a job.

The boundary is deliberately narrow and it lives here. Do not widen it by analogy — a new surface is user-facing unless it is one of the two listed. `docs/analytics.md` explains the reasoning behind the split.

## Strict behavior rules

Every rule below is about what a user sees; on the two operator surfaces named above, the English-only, no-personality, structural-punctuation exceptions apply instead.

- ALWAYS prefix titles with exactly one leading emoji + space.
- ALWAYS apologize at the start of error messages and provide a recovery hint.
- ALWAYS use 3rd person ("Keiko {verbs}...") in slash command `description:` fields.
- ALWAYS use 1st person elsewhere.
- ALWAYS prefix footer text with `"• "`.
- ALWAYS bold `**Keiko**` and `**$command_name**` when referenced inline.
- ALWAYS backtick slash commands, written as their context entry (`${context.commands.<key>}`).
- ALWAYS provide both `en-us` and `pt-br` for new strings.
- ALWAYS reuse `commands.responses.{enabled,paused,unpaused,disabled}` for state changes: a pause and an unpause draw the panel again (its title says "(Pausado)" and its button **Despausar**) and send their message beside it on an ephemeral note that removes itself after `ViewConstants.ACTION_NOTICE_SECONDS`; do not invent new state-change copy.
- ALWAYS reuse generic button keys from `app/languages/buttons.yml` when an existing label fits.
- ALWAYS place the Cancel button last in a view (own actions, then Back, then Cancel).
- ALWAYS use the choice layout (question, one line per option led by its emoji, closing invitation) when a step asks the user to pick between options.
- NEVER put a `- ` bullet in front of an emoji: the emoji is already the bullet.
- NEVER explain two or more options inside a single paragraph.
- NEVER stack multiple emojis adjacent.
- NEVER hardcode user-facing strings in Python.
- NEVER use `•` as an inline bullet in body copy.
- NEVER use em-dashes (`—`) or en-dashes (`–`) anywhere. They sound AI-like; rewrite with commas, periods, or two sentences.
- NEVER use `...` outside async/in-progress states (loading, syncing). It is reserved for "still working" UX cues.
- NEVER write a value Keiko fills as `{var}`: `$var`, or the token of its kind (`${answer...}`, `${context...}`); braces are for the templates an admin writes.
- NEVER use ALL CAPS, slang, memes, or sarcasm.
- NEVER drop the standard form footer (`"• Use the `/report` command to tell me a bug"`): leave `footer` out of a step and the engine draws it from `app/forms/vocabulary.yml`; never write it out again, and never write `footer: null` without a reason.
- IF writing an error → red embed (`Style.RED_COLOR`) + apology + recovery hint + use `response_error_embed("<command-key>.ui.errors.<name>", locale)` (a shared one by its bare name).
- IF writing a success → reuse `commands.responses.*` keys.
- IF documenting the variables of an admin template for the user → use a `-` bullet list: `\n- ` `` `{var}` ``: {meaning}". Never `•` here.
- IF the message describes an async/in-progress state → trailing `...` is allowed and expected (e.g., `🔄 Syncing roles...`).
- IF a subtle dog pun fits naturally (especially pt-br "au au" wordplay like *aujuda*) → ship it sparingly.
- IF building a multi-design selection → header `"⬇️ Select one of the designs below:"`, footer `"⬆️ Scroll up to see all options"`. Do not hardcode the count.

## Output patterns

### Slash command description (in the command's `copy.yml`)
```yaml
slash:
  name:
    en-us: {kebab-name}
    pt-br: {nome-kebab}
  description:
    en-us: Keiko {verb}s {what} {how/why}
    pt-br: Keiko {verbo} {o quê} {como/por quê}
```

### Standard response embed (in the command's `copy.yml`)
```yaml
# open: response names -> a Response
responses:
  {response_name}:
    title:
      en-us: {EMOJI} {Title}!
      pt-br: {EMOJI} {Título}!
    description:
      en-us: {1st-person sentence, $value where the code fills one}
      pt-br: {frase em 1ª pessoa, $value onde o código preenche}
    footer: ${ref.support_footer}
```
Render: `response_embed("{command-key}.responses.{response_name}", locale, image=True, facts=command_facts(...))`. Leave `footer` out for the standard `/report` line, write `footer: null` for none, or a text (here the shared `support_footer` of `app/forms/vocabulary.yml`) for another.

### Error message (in the command's `copy.yml`, or `app/languages/errors.yml` when shared)
```yaml
# open: error names -> an ErrorBlock
errors:
  {error_name}:
    title:
      en-us: {Problem Name in Title Case}
      pt-br: {Nome do Problema}
    message:
      en-us: I'm sorry, but {what went wrong}. {How to recover}.
      pt-br: Sinto muito, mas {o que deu errado}. {Como resolver}.
```
Render: `response_error_embed("{command-key}.errors.{error_name}", locale)`, or `response_error_embed("{error_name}", locale)` for a shared one (auto-prefixes `🚨`); a form names it by the block, `error: {command-key}.errors.{error_name}`.
An error always writes both `title` and `message`: a missing one shows on screen as its own copy key (pinned by `tests/behavioral/regressions/test_language.py`).

### Success / state-change embed
Reuse `commands.responses.{enabled|paused|unpaused|disabled}`. Substitute `$command_name`, `$user`, `$date` only (`$date` is a Discord timestamp, so each reader sees their own date and time). A pause and an unpause draw the panel again and send their embed beside it, removed after `ViewConstants.ACTION_NOTICE_SECONDS`.

### Modal step (YAML)
```yaml
- type: text
  key: {snake_key}
  title:
    en-us: {Short Title}
    pt-br: {Título Curto}
  description:
    en-us: {1-sentence hint with example}
    pt-br: {dica em pt-br}
  label:
    en-us: {Question}
    pt-br: {Pergunta}
  placeholder:
    en-us: {realistic example}
    pt-br: {exemplo}
  max_length: {<= 100}
```

### Form intro / outro (YAML)
The steps below sit under `steps:` of the command's `form.yml`.
```yaml
- type: intro
  key: form
  title:
    en-us: "{EMOJI} {Feature Name}"
    pt-br: "{EMOJI} {Nome em pt-br}"
  description:
    en-us: Enabling this feature allows me to {what I'll do}!
    pt-br: Ativar esta função faz com que eu {o que farei}!

# ... steps, each without footer: the engine draws the standard one ...

- type: review
  key: confirm
```
The summary writes nothing else: its title is the intro's, and the standard summary sentence (`fallbacks.review_description`) and the standard footer (`fallbacks.footer`) come from `app/forms/vocabulary.yml`.
