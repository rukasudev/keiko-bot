---
name: keiko-writing-style
description: Use when writing or editing user-facing text for the Keiko Discord bot, including slash command descriptions, embeds, modals, buttons, forms, error messages, success messages, greetings, notification copy, welcome messages, or any UI text rendered to Discord users. Applies to YAML language files under app/languages/ and any embed/modal/view construction in app/components/. Enforces Keiko's friendly-loyal-dog companion tone, voice rules (3rd person for slash command descs, 1st person everywhere else), formatting (single leading emoji on titles, "• " footer prefix, backticked slash commands, bold **Keiko**), localization (every string in both en-us and pt-br), and Discord UI conventions (embed colors 0x4F97F9 default / 0xff0000 errors, button styles, modal patterns, form structure).
---

# Keiko Writing Style

You are writing or editing user-facing copy for the Keiko Discord bot. Apply every rule below. Do not paraphrase or soften them.

## Personality

Keiko is a friendly, loyal, cute dog companion. Warm and helpful, never childish or sarcastic. One cute beat per message; never stack them.

Subtle dog-themed wordplay is welcome when it lands naturally. Example: the pt-br greetings line `Se precisar de *aujuda*` puns "ajuda" (help) with "au au" (Portuguese for "woof woof"). Ship these sparingly and only when the joke is gentle and clear.

## Voice rules

- **3rd person ONLY** in slash command `desc:` fields (Discord's command picker metadata): `Keiko {verbs} ...`.
- **1st person ALWAYS** in every embed, modal, response, error, form description, and notification: `I, my, me, I'll, I'm`.
- Never mix voices in the same string.
- Never use slang, memes, irony, sarcasm, or pop-culture references.

## Writing rules

- Sentences are short to medium (1 to 4 sentences per description).
- Use contractions (`I'm`, `don't`, `you'll`).
- Frequent `!` for instructional/success copy. Periods otherwise.
- **Never use em-dash (`—`) or en-dash (`–`).** They sound AI-like and overly formal. Rewrite with a comma, period, or split into two sentences. No existing Keiko message uses them; new ones must not either.
- **`...` (three dots) is allowed only for async/in-progress states** (e.g., `🔄 Syncing roles...`, `Loading...`). It signals "I'm still working." Do not use `...` as a stylistic ellipsis or trailing-thought device anywhere else.
- Errors begin with apology: `I'm sorry, but ...` or `Oops! ...`. Always include a recovery hint (`Please try again later`, `Check the name and try again`, or `send us a report using the /report command`).
- Success titles end with `... successfully!` or `... sent!`. Optional warm closer (`Now everyone is even cuter! 🐶`).
- Instructions address the user directly (`Choose which channel I should ...`).
- Keep it natural and simple. Avoid corporate or formal phrasing; if a sentence reads like a press release, rewrite it.
- **Explanatory clarity is Keiko's superpower.** Prefer explicit descriptive phrasing over compact labels: `Todos os links desse site` / `Só esse link específico` beats `Site inteiro` / `Link exato`. Whenever a configuration choice could be ambiguous, add a concrete backticked example showing the difference (`youtube.com` vs `youtube.com/watch?v=abc123`).
- **Explain with the user's own data, not canned examples.** When a step explains a choice about something the user just provided, echo their real input via the `{response:<key>|<fallback>}` token in the step description (the form engine substitutes the earlier answer; the fallback shows while it doesn't exist yet). "How far does this rule go for `meusite.com.br`?" teaches instantly; a generic example makes the user translate in their head.
- **Never explain two or more choices inside one paragraph.** A wall of prose with the options bolded inline is unreadable in Discord: the user has to parse a paragraph to find which button to press. Always use the choice layout below.

## Choice layout (steps that ask the user to pick)

Whenever a step presents options whose consequences are not self-evident from the labels
(modes, matching rules, scopes), the description follows exactly three parts, separated by
blank lines (`\n\n`). A plain yes/no gate ("Want to add your own links?") needs only the
question.

1. **The question**, one line, naming what it applies to (echo the user's own data with
   `{response:<key>|<fallback>}` when the choice is about something they just typed).
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
  en-us: "This rule will **always allow** `{response:link|youtube.com/watch?v=abc123}`. Tell me how far it goes!\n\n🌐 **Every link from this website**: the rule covers the whole website, any page or video on it.\n🔗 **Only this specific link**: the rule covers only that exact address, and the rest of the website stays out of it.\n\nWhich one do you prefer? Just pick it on the buttons below! 🐶"
  pt-br: "Essa regra vai **sempre liberar** `{response:link|youtube.com/watch?v=abc123}`. Me diga o alcance dela!\n\n🌐 **Todos os links desse site**: a regra vale para o site inteiro, qualquer página ou vídeo dele.\n🔗 **Só esse link específico**: a regra vale só para esse endereço exato, e o resto do site fica de fora dela.\n\nQual você prefere? É só escolher nos botões abaixo! 🐶"
```

When the same step means different things depending on an earlier answer, do not write one
sentence covering both cases: give it `description-when:` variants (see
`docs/form-configuration.md`), so each case reads as if it were the only one.

Pinned by `test_option_explanations_are_written_as_own_lines`
(`tests/behavioral/test_form_yaml_contracts.py`): an options step that names its own labels
in prose, or that puts a bullet in front of an emoji, fails the suite.

**Visual rhythm, generally:** any description longer than about three lines gets structure.
One idea per line, blank line between blocks, bullets for anything enumerable. A dense block
of text is a bug, not a style choice.

## Discord UI rules

### Embeds (`app/components/embed.py`)
- Default color: `Style.BACKGROUND_COLOR` = `0x4F97F9`.
- Error color: `Style.RED_COLOR` = `0xff0000`. `response_error_embed()` auto-prefixes the title with `🚨` — do not duplicate.
- Title format: `"{ONE_EMOJI} {Title Case}"`. Exactly one leading emoji.
- Use `description` for short bodies. Use `add_field(name=..., value=..., inline=False)` for stacked record-style content (DM reports, button caption lists, help indexes).
- Footer always: `embed.set_footer(text=f"• {text}")`. The `"• "` prefix is mandatory.
- Card screens (`configuration_card` / `summary_card`) are Components V2 LayoutViews, not embeds: their footer comes from the step's `footer:` and renders as `-# {text}` subtext at the bottom of the container (`_add_footer`, `app/views/summary_card.py`). Pickers opened from a card keep the same footer. Pinned by `tests/behavioral/contracts/test_card_footer_and_option_styles.py`.
- Thumbnail: `KeikoIcons.IMAGE_01` for branded responses; `KeikoIcons.ACTION_IMAGE.get(action)` for action-specific icons.

### Buttons (`app/components/buttons.py`)
- `ButtonStyle.green` → Confirm / positive
- `ButtonStyle.red` → Cancel / destructive
- `ButtonStyle.primary` → Back / selected state / primary navigation
- `ButtonStyle.gray` / `grey` → Edit, Pause, Unpause, Preview, Help, Disable, Add, Remove, History, Sync
- `ButtonStyle.secondary` → form back navigation
- Reuse generic labels from `buttons.{lang}.yml` (`confirm`, `cancel`, `edit`, `add`, `remove`, `back`, `select`, `continue`, `pause`, `unpause`, `preview`, `history`). Only add a new key when the action is genuinely new.
- **Cancel is ALWAYS the last button in the view.** Order: the step's own actions first
  (options, Confirm, Done, Add/Remove), then Back, then Cancel. Buttons render in the order
  they are added, so anything appended after the view is built (like the form back button)
  must re-anchor Cancel at the end with `keep_cancel_button_last(view)`
  (`app/components/buttons.py`). Pinned by
  `tests/behavioral/contracts/test_cancel_button_last.py`.

### Modals (`app/components/modals.py`)
- Default `max_length` = 40. Cap at 100 for free-text fields.
- `placeholder` falls back to `description` if not set; otherwise use a realistic example (`"shroud"`, `"pewdiepie"`, `"No links here! :p"`).
- `multiline: true` in YAML → `discord.TextStyle.long`. Otherwise `short`.
- Repeated labels in the same modal auto-append `#1`, `#2`, etc.

### Forms (`app/languages/form/*.yml`)
- Open with `action: form` (intro).
- Close with `action: resume` titled `":white_check_mark: Alright?"` / `":white_check_mark: Tudo certo?"`.
- Every step's footer: `"• Use the command /report to tell me a bug"` / `"• Utilize o comando /reportar para me contar um bug"`.
- Every step has `title`, `description`, `footer` for both `en-us` and `pt-br`.
- Step actions: `form`, `channels`, `modal`, `multi_select`, `options`, `button`, `design_select`, `file_upload`, `composition`, `resume`.

## Formatting rules

- Slash commands → backticks: `` `/setup` ``, `` `/report` ``, `` `/support` ``.
- Bot name and emphasized nouns → bold: `**Keiko**`, `**#support**`, `**$command_name**`.
- Stressed words → italics: `*help*`.
- Examples / values → backticks: `` `ks!mouse` ``.
- Variables shown to the user (welcome / notification message templates) → curly braces: `{user}`, `{server}`, `{member_count}`, `{streamer}`, `{stream_link}`, `{youtuber}`, `{video_link}`.
- Variables in system/event descriptions (substituted by `services/utils.py`) → dollar prefix: `$user`, `$command_name`, `$date`, `$setup_command`, `$cog_name`, `$cog_key`, `$ping`, `$action`, `$roles`.
- Earlier form answers echoed in step descriptions → `{response:<key>|<fallback>}` (substituted by the form engine; always provide the fallback).
- Never mix `{}` and `$` in the same string.
- Footer prefix: `"• "` (bullet + space). Never use `•` as an inline bullet in body copy.
- Lists: `\n- ` for unordered, `\n1. ` / `\n2. ` for sequential. `\n\n` between paragraphs.
- One leading emoji on titles. 0–2 emoji elsewhere per paragraph.

## Vocabulary

**Prefer:** *help, make, configure, manage, set up, welcome, notify, protect, block, assign, send, fetch, cozy, personalized, automatic(ally), warmly, lovingly, loyal, favorite, get started, just call me, don't hesitate*.

**Avoid:** *execute, invoke, initialize, instantiate, parameter, invalid, forbidden, malformed, kindly, please be advised*. Avoid ALL CAPS, slang, memes, ironic phrasing, em-dashes (`—`), and en-dashes (`–`).

## Localization

- Every new user-facing string MUST exist in BOTH `app/languages/.../*.en-us.yml` and `*.pt-br.yml`.
- Python code must NEVER hardcode user-facing strings. Always: `ml("namespace.key", locale)`.
- pt-br tone matches en-us exactly: same warmth, same emoji, same 1st person, same `**Keiko**` bolding, same `"• "` footer prefix.

## Where these rules do NOT apply: operator surfaces

Everything above governs what a Discord **user** sees. Two surfaces are read only by whoever runs the bot, and they are a taxonomy for on-call, not Keiko talking:

- the `/admin` commands, restricted to `ADMIN_GUILD_ID` (`app/services/admin_analytics.py`, `app/services/admin_digest.py`);
- the admin log channels: trace and journey messages, and the labels around them (`app/logger.py`, `app/services/journey.py`).

On those two surfaces only: English only, no `ml()` and no pt-br counterpart; no personality, no 1st person, no apology-plus-recovery shape; structural punctuation such as `—` and `·` is allowed, because the line is a record, not a sentence; and `• ` opens each item of a list in the body, which is how the weekly digest and every `/admin` report are already written. Keep every other habit — one leading emoji on a title, `"• "` on a footer, `**bold**` for names — so the messages still read as one product.

Everything else stays fully under the rules above, including anything a server member can see: an error a user triggers is user-facing even when an operator also reads it, and a notification is user-facing even when it was posted by a job.

The boundary is deliberately narrow and it lives here. Do not widen it by analogy — a new surface is user-facing unless it is one of the two listed. `docs/analytics.md` explains the reasoning behind the split.

## Strict behavior rules

Every rule below is about what a user sees; on the two operator surfaces named above, the English-only, no-personality, structural-punctuation exceptions apply instead.

- ALWAYS prefix titles with exactly one leading emoji + space.
- ALWAYS apologize at the start of error messages and provide a recovery hint.
- ALWAYS use 3rd person ("Keiko {verbs}...") in slash command `desc:` fields.
- ALWAYS use 1st person elsewhere.
- ALWAYS prefix footer text with `"• "`.
- ALWAYS bold `**Keiko**` and `**$command_name**` when referenced inline.
- ALWAYS backtick slash commands.
- ALWAYS provide both `en-us` and `pt-br` for new strings.
- ALWAYS reuse `command-events.{enabled,paused,unpaused,disabled,edited,added,removed}` for state changes; do not invent new state-change copy.
- ALWAYS reuse generic button keys from `buttons.{lang}.yml` when an existing label fits.
- ALWAYS place the Cancel button last in a view (own actions, then Back, then Cancel).
- ALWAYS use the choice layout (question, one line per option led by its emoji, closing invitation) when a step asks the user to pick between options.
- NEVER put a `- ` bullet in front of an emoji: the emoji is already the bullet.
- NEVER explain two or more options inside a single paragraph.
- NEVER stack multiple emojis adjacent.
- NEVER hardcode user-facing strings in Python.
- NEVER use `•` as an inline bullet in body copy.
- NEVER use em-dashes (`—`) or en-dashes (`–`) anywhere. They sound AI-like; rewrite with commas, periods, or two sentences.
- NEVER use `...` outside async/in-progress states (loading, syncing). It is reserved for "still working" UX cues.
- NEVER mix `{var}` and `$var` syntaxes in the same string.
- NEVER use ALL CAPS, slang, memes, or sarcasm.
- NEVER skip the standard form footer (`"• Use the command /report to tell me a bug"`).
- IF writing an error → red embed (`Style.RED_COLOR`) + apology + recovery hint + use `response_error_embed("{key}", locale)`.
- IF writing a success → reuse `command-events.*` keys.
- IF documenting variables for the user → use a `-` bullet list: `\n- ` `` `{var}` ``: {meaning}". Never `•` here.
- IF the message describes an async/in-progress state → trailing `...` is allowed and expected (e.g., `🔄 Syncing roles...`).
- IF a subtle dog pun fits naturally (especially pt-br "au au" wordplay like *aujuda*) → ship it sparingly.
- IF building a multi-design selection → header `"⬇️ Select one of the designs below:"`, footer `"⬆️ Scroll up to see all options"`. Do not hardcode the count.

## Output patterns

### Slash command desc (YAML)
```yaml
{command-key}:
  name: {kebab-name}
  desc: Keiko {verb}s {what} {how/why}
```

### Standard response embed (YAML)
```yaml
{namespace}:
  response:
    title: {EMOJI} {Title}!
    message: {1st-person sentence}
    footer: {actionable hint, e.g. "If you need help, don't hesitate to call us on our official server, using the /support command."}
```
Render: `response_embed("commands.{namespace}.response", locale, footer=True, image=True)`.

### Error message (YAML)
```yaml
{error-key}:
  title: {Problem Name in Title Case}
  message: I'm sorry, but {what went wrong}. {How to recover}.
```
Render: `response_error_embed("{error-key}", locale)` (auto-prefixes `🚨`).

### Success / state-change embed
Reuse `command-events.{enabled|paused|unpaused|disabled|edited|added|removed}`. Substitute `$command_name`, `$user`, `$date` only.

### Modal step (YAML)
```yaml
- action: modal
  key: {snake_key}
  max_length: {<= 100}
  title:
    en-us: {Short Title}
    pt-br: {Título Curto}
  label:
    en-us: {Question}
    pt-br: {Pergunta}
  description:
    en-us: {1-sentence hint with example}
    pt-br: {dica em pt-br}
  placeholder:
    en-us: {realistic example}
    pt-br: {exemplo}
```

### Form intro / outro (YAML)
```yaml
- action: form
  key: form
  title:
    en-us: "{EMOJI} {Feature Name}"
    pt-br: "{EMOJI} {Nome em pt-br}"
  description:
    en-us: Enabling this feature allows me to {what I'll do}!
    pt-br: Ativar esta função faz com que eu {o que farei}!
  footer:
    en-us: "• Use the command /report to tell me a bug"
    pt-br: "• Utilize o comando /reportar para me contar um bug"

# ... steps ...

- action: resume
  key: confirm
  title:
    en-us: ":white_check_mark: Alright?"
    pt-br: ":white_check_mark: Tudo certo?"
  description: {same as form intro description}
  footer: {same standard footer}
```

### Variable instructions (YAML, button step)
```yaml
- action: button
  key: {key}
  emoji: "📩"
  title:
    en-us: "{Step Name}"
  description:
    en-us: "Now let's configure {what}! Read the following notes carefully:"
  fields:
    en-us:
      - title: "1. {Concept}"
        message: "{1-sentence explanation}"
      - title: "2. Variables"
        message: "You can use the following variables in your message:\n• `{var}`: {meaning}"
```
