# Keiko Writing Style System

A reusable writing/style system extracted from the existing Keiko Discord bot codebase. Use it whenever you write or edit user-facing text — slash command descriptions, embeds, modals, buttons, forms, error/success messages, and notification copy.

All user-facing strings live in `app/languages/**/*.yml` and are routed through `ml()` (`app/services/utils.py:292`). Embed/modal/button construction is centralized in `app/components/embed.py`, `app/components/modals.py`, and `app/components/buttons.py`.

---

# 1. Writing Style Guide

## 1.1 Personality & voice

Keiko is a friendly, loyal dog companion. Two voices coexist depending on **where** the text appears:

| Surface | Voice | Example |
|---|---|---|
| Slash command `desc` (Discord picker metadata) | **3rd person** ("Keiko {verbs}…") | `Keiko helps you understand how to use the available commands` |
| Embeds, responses, modals, errors, forms (everything the user sees AS the bot) | **1st person** ("I'll…", "my…", "me") | `I'm sorry, but I couldn't process the command correctly.` |

**Personality markers** (use sparingly, never stack more than one per message):
- Warm adverbs: *lovingly*, *warmly*, *carefully*
- Cute superlatives: *cozy*, *even cuter*, *even more awesome*
- Friend/companion frame: *loyal companion*, *make new friends*, *tricks I know*
- Light dog motifs: 🐾, 🐶, *just call me*
- **Subtle dog wordplay** (especially pt-br "au au" puns): the existing greetings line `Se precisar de *aujuda*` blends *ajuda* (help) with *au au* (Portuguese for "woof woof"). Ship gentle, clear puns like this when they fit naturally.

**Formality:** Friendly-casual. Contractions allowed (`I'm`, `don't`, `you'll`). No slang, no irony, no sarcasm. Never childish, never overly cute (one cute beat per message max). Keep it natural and simple; if a sentence reads like a press release, rewrite it.

**Humor:** Limited to gentle warmth ("Now everyone is even cuter! 🐶"), the occasional emoticon (`:)`, `:p`, `:/`), and subtle dog puns. No memes, no pop-culture references.

## 1.2 Sentence structure

- **Length:** Short to medium. 1 to 2 sentences in titles, 1 to 4 in descriptions. Long blocks split with `\n\n`.
- **Punctuation:**
  - Frequent `!` (mainly success/instructional). Periods otherwise.
  - **Never use em-dash (`—`) or en-dash (`–`).** They sound AI-like and overly formal. Rewrite with a comma, period, or split into two sentences. No existing Keiko message uses them.
  - **`...` is allowed only for async/in-progress states** (e.g., `🔄 Syncing roles...`, `Loading...`). It signals "I'm still working." Do not use `...` as a stylistic ellipsis or trailing-thought device anywhere else.
- **Lists:**
  - Numbered for sequential instructions: `"1. Go to **#support**\n2. Wait for a team member\n3. ..."`
  - Dash bullets for feature recaps: `"- **Moderation:** ...\n- **Notifications:** ..."`
  - `•` only as the **footer prefix**, never as inline bullets in body copy.
- **Line breaks:** `\n\n` between paragraphs. `\n- ` to start list items inside a string.

## 1.3 Discord UI patterns

### Embeds
- **Default color:** `Style.BACKGROUND_COLOR` = `0x4F97F9` (light blue).
- **Error color:** `Style.RED_COLOR` = `0xff0000`.
- **Title format:** `"{emoji} {Title Text}"` — exactly one leading emoji, Title Case body. Errors prefix with `🚨` automatically (`response_error_embed`).
- **Description vs fields:**
  - Use `description` for: a single message, a short body with one CTA, instructional copy, error messages.
  - Use `add_field(inline=False)` for: stacked record-style content (report DMs at `embed.py:120-129`), command/button caption lists (`buttons.py:333-347` Help embed), multi-section help.
- **Footer:** Always prefixed with `"• "` when set (`embed.py:48,73,86,132`). Standard form footer: `"• Use the command /report to tell me a bug"`.
- **Thumbnail:** Use `KeikoIcons.IMAGE_01` for branded responses, or `KeikoIcons.ACTION_IMAGE.get(action)` for action-specific icons (`embed.py:23-24`).

### Buttons
| Purpose | Style | Source |
|---|---|---|
| Confirm / positive | `ButtonStyle.green` | `buttons.py:20` |
| Cancel / destructive | `ButtonStyle.red` | `buttons.py:29` |
| Back / primary action / selected state | `ButtonStyle.primary` | `buttons.py:194,49` |
| Edit, Pause, Preview, Help, Disable, Add, Remove, History, Sync | `ButtonStyle.gray` / `grey` | `buttons.py:108,134,268` |
| Form back button | `ButtonStyle.secondary` | `buttons.py:253` |

Button labels live in `buttons.{lang}.yml` under generic keys (`confirm`, `cancel`, `edit`, `add`, `remove`, `preview`, `back`, `pause`, `unpause`, `select`, `continue`, `history`). UI-component-specific copy (e.g., select placeholders) lives under `components.select.*`.

### Modals
- `title` = `config["title"][locale]` (`modals.py:18`). Timeout 300s.
- `max_length` defaults to **40** (`modals.py:32`); override via YAML `max_length`.
- `placeholder` falls back to `description` when not set explicitly (`modals.py:34`).
- Multi-line: `style: long` when YAML sets `multiline: true`; otherwise `short`.
- Repeated labels in the same modal get `#1`, `#2`, etc., appended automatically.
- Confirmation modal asks the user to type the action verb back (`modals.py:142-156`).

### Forms (YAML schema)
Each step has: `action`, `key`, optional `emoji`, `title.{en-us,pt-br}`, `description.{en-us,pt-br}`, `footer.{en-us,pt-br}`. Step actions seen: `form`, `channels`, `modal`, `multi_select`, `options`, `button`, `design_select`, `file_upload`, `composition`, `resume`.

- Every form **starts** with an `action: form` intro step (title + emoji + 1-sentence description).
- Every form **ends** with `action: resume` confirmation: title `":white_check_mark: Alright?"` / `"Tudo certo?"`.
- Footer on every step is the **same line**: `"• Use the command /report to tell me a bug"` / `"• Utilize o comando /reportar para me contar um bug"`.

## 1.4 Messaging patterns

### Errors (always)
1. Embed color: red (`ff0000`).
2. Title: `"🚨 {Problem Name}"` (added automatically by `response_error_embed`).
3. Message: starts with apology — `"I'm sorry, but ..."` or `"Oops! ..."`.
4. Always offer the next step: `"Please try again later."`, `"Check the name and try again."`, or `"send us a report using the /report command"`.

### Success
1. Title: emoji + `"... successfully!"` for state changes (`✅`, `🎉`, `✨`, `📝`, `➕`, `🗑️`, `⏸️`, `▶️`, `🚫`).
2. Often closes with celebratory beat (`"Now everyone is even cuter! 🐶"`, `"🚀"`, `"✨"`).

### Instructions
- Address the user directly (`"Choose which channel..."`, `"Use the buttons below..."`).
- When introducing variables, list them last with bullets and inline-code keys: `` "- `{user}`: User mention" ``.
- Always backtick slash-command names: `` `/help` ``, `` `/setup` ``, `` `/report` ``, `` `/support` ``.

### Feedback / state changes
- Use the `command-events.{enabled,paused,unpaused,disabled,edited,added,removed}` keys; never invent new state-change copy.

## 1.5 Vocabulary

**Preferred** (drawn from existing copy):
- Verbs: *help*, *make*, *configure*, *manage*, *set up*, *welcome*, *notify*, *protect*, *block*, *assign*, *send*, *fetch*
- Adjectives/adverbs: *cozy*, *personalized*, *automatic(ally)*, *warmly*, *lovingly*, *loyal*, *favorite*
- Calls-to-action: *Get started*, *Just call me*, *Don't hesitate*, *Read the following notes carefully*
- Closers: `✨`, `💌`, `🐾`, `🐶`, `🚀`, `🎉`

**Avoid:**
- Corporate/dry: *initialize*, *invoke*, *execute* (in body copy; `Execute` is fine as a button label), *instantiate*, *parameter*
- Negative scoldy tone: *invalid*, *forbidden*, *malformed* (rephrase apologetically)
- Filler: *kindly*, *please be advised*, *we regret to inform*
- Slang/memes/irony
- **Em-dash (`—`) and en-dash (`–`):** never. They sound AI-like. Use commas, periods, or two sentences instead.

## 1.6 Formatting rules

| Element | Format | Example |
|---|---|---|
| Slash commands | Backticks | `` `/setup` `` |
| Bot names / important nouns | Bold | `**Keiko**`, `**#support**` |
| Command names in event copy | Bold + variable | `**$command_name**` |
| Variables in user-facing strings | Curly braces, inline-code in docs | `{user}`, `{server}`, `{member_count}` |
| Variables in system/event descriptions | Dollar prefix | `$user`, `$command_name`, `$date` |
| Stressed words | Italics | `*help*` |
| Examples / values | Backticks | `` `ks!mouse` `` |
| Footer prefix | `"• "` (bullet + space) | `"• Use the command /report..."` |
| Title emoji | Single leading emoji + space | `"📚 Available commands"` |

**Spacing:** No double spaces. No trailing newlines inside YAML strings. Keep one blank line between embed sections (`\n\n`).

---

# 2. Do & Don't Examples

### Title

✅ `"🎉 Welcome Messages"`
✅ `"📚 Available commands"`
✅ `":white_check_mark: Alright?"`
❌ `"Welcome Messages 🎉"` — emoji must lead.
❌ `"📚 ✨ Available commands"` — only one emoji.
❌ `"Available commands"` — needs an emoji.

### Description

✅ `"Here are my commands that will help you make your server more cozy! ✨"`
✅ `"Enabling this feature allows me to send a personalized welcome message to new members when they join your server"`
❌ `"This command lists all available commands. Click below to invoke one."` — corporate, no warmth, no emoji, no first person.
❌ `"omg here are all my super cool commands lol"` — slang, over-cute.

### Error

✅ `"I'm sorry, but I couldn't translate the message correctly. Please try again later."`
✅ `"Oops! Sorry, but you don't have the permissions to run this command :/"`
❌ `"ERROR: Translation failed."` — not apologetic, all caps.
❌ `"Permission denied."` — no apology, no path forward.

### Success

✅ `"I've added the default roles to all members of the server! Now everyone is even cuter! 🐶"`
✅ `"📝 Report sent!"`
❌ `"Operation completed successfully."` — sterile.
❌ `"YESSS done!! 🎉🎉🎉🎉"` — over-emoji, over-casual.

### Instruction

✅ `"Use the buttons below or type the command to get started 🐾"`
✅ `"Choose which channel I should send the welcome messages"`
❌ `"Please select a channel from the dropdown menu in order to proceed."` — verbose, no warmth.

### Slash command desc

✅ `"Keiko helps you report any bugs or issues you encounter"`
✅ `"Keiko warmly welcomes new members with a personalized message"`
❌ `"Reports a bug"` — no Keiko-prefix, no warmth.
❌ `"I help you report bugs"` — wrong voice (must be 3rd person here).

---

# 3. Reusable Templates

### 3.1 Slash command desc (YAML)
```yaml
{command-key}:
  name: {kebab-name}
  desc: Keiko {verb}s {what} {how/why}
```
Example: `Keiko lovingly protects our server by blocking any suspicious links`.

### 3.2 Standard response embed (YAML)
```yaml
{namespace}:
  response:
    title: {EMOJI} {Title}!
    message: {1st-person sentence ending with emoji or .}
    footer: {actionable hint, e.g. "If you need help, don't hesitate to call us on our official server, using the /support command."}
```
Render via `response_embed("commands.{namespace}.response", locale, footer=True, image=True)`.

### 3.3 Error message (YAML)
```yaml
{error-key}:
  title: {Problem Name in Title Case}
  message: I'm sorry, but {what went wrong}. {How to recover}.
```
Render via `response_error_embed("{error-key}", locale)` — auto-prefixes `🚨`.

### 3.4 Success / state-change embed
Use existing `command-events.{enabled,paused,unpaused,disabled,edited,added,removed}`. Do not invent new state keys; only fill in `$command_name`, `$user`, `$date`.

### 3.5 Modal (YAML form step)
```yaml
- action: modal
  key: {snake_key}
  max_length: {<= 100}
  title:
    en-us: {Short Title}
    pt-br: {Título Curto}
  label:
    en-us: {Question / what to type}
    pt-br: {Pergunta}
  description:
    en-us: {1-sentence hint with example: "Only the streamer's username. Example: ninja"}
    pt-br: {dica em pt-br}
  placeholder:
    en-us: {realistic example value}
    pt-br: {exemplo}
```

### 3.6 Form intro / outro
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

- action: resume
  key: confirm
  title:
    en-us: ":white_check_mark: Alright?"
    pt-br: ":white_check_mark: Tudo certo?"
  description: {same as the form intro description}
  footer: {same standard footer}
```

### 3.7 Variable-instructions block (button step inside a form)
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
      - title: "2. {Variables}"
        message: "You can use the following variables in your message:\n• `{var}`: {description}"
```

### 3.8 Greeting / branded response
- Use 1st person, open with `"Hello, I'm Keiko, ..."` or analogous.
- Include `:dog:` once, `✨` for sparkle, `🐾` once.
- End with one CTA (`/help`, `/setup`, `/support`).

---

# 4. Generation Rules (strict checklist)

**Voice & tone**
1. ALWAYS use 3rd person (`"Keiko {verbs}..."`) in slash command `desc:` fields.
2. ALWAYS use 1st person (`I, my, me`) in every other user-facing string.
3. NEVER mix voices in the same string.
4. NEVER use slang, memes, irony, sarcasm, or pop-culture references.
5. ALWAYS apologize at the start of error messages (`"I'm sorry, but..."` or `"Oops!"`).
6. ALWAYS provide a recovery path in error messages.
6a. NEVER use em-dash (`—`) or en-dash (`–`). Rewrite with comma, period, or two sentences.
6b. USE `...` only for async/in-progress states (e.g., `🔄 Syncing roles...`); never as a stylistic ellipsis.
6c. SHIP subtle dog puns sparingly when natural (e.g., pt-br *aujuda* = *ajuda* + *au au*).

**Emoji**
7. ALWAYS prefix titles with exactly **one** leading emoji + a space.
8. NEVER stack more than one emoji adjacent (`✨🎉` is forbidden; use one).
9. LIMIT body emoji to **0–2** per paragraph.
10. USE 🚨 only for error titles (auto-added by `response_error_embed` — do not duplicate).
11. PREFER pet motifs (🐾, 🐶, :dog:) only for greetings, success closers, or playful confirmations — at most once per message.
12. EITHER Unicode emoji OR `:shortcode:` works; match the surrounding file's style.

**Formatting**
13. ALWAYS bold `**Keiko**` and `**$command_name**` when referenced inline.
14. ALWAYS backtick slash commands: `` `/setup` ``, `` `/report` ``, `` `/support` ``.
15. ALWAYS prefix footer text with `"• "`.
16. NEVER use `•` as inline bullets in body copy — use `-` or numbered lists.
17. USE `\n\n` between paragraphs, `\n- ` to start a list item inside a string.

**Variables**
18. USE `{user}`, `{server}`, `{member_count}`, `{streamer}`, `{stream_link}`, `{youtuber}`, `{video_link}` for user-customized message templates (welcome, notification messages).
19. USE `$user`, `$command_name`, `$date`, `$setup_command`, `$cog_name`, `$cog_key`, `$ping`, `$action`, `$roles` for system/event descriptions injected by `services/utils.py`.
20. NEVER mix the two syntaxes in the same key.

**Localization**
21. EVERY new user-facing string MUST exist in BOTH `en-us` and `pt-br`.
22. NEVER hardcode user-facing strings in Python — always go through `ml(key, locale)`.
23. KEEP pt-br tone identical to en-us (warm, 1st person, same emoji).

**Embeds**
24. USE `Style.BACKGROUND_COLOR` (`4F97F9`) by default; `Style.RED_COLOR` (`ff0000`) only for errors.
25. USE `description` for short bodies; `add_field(inline=False)` only for record-style stacks (DM reports, captions, help indexes).
26. SET footer via `embed.set_footer(text=f"• {ml(...)}")` — keep the bullet prefix.
27. USE `KeikoIcons.IMAGE_01` for branded thumbnails unless an action-specific icon exists in `KeikoIcons.ACTION_IMAGE`.

**Buttons**
28. Confirm = `green`, Cancel = `red`, Back/selected/primary action = `primary`, everything else = `gray`/`grey`.
29. REUSE generic labels from `buttons.{lang}.yml` (`confirm`, `cancel`, `edit`, `add`, `remove`, `back`, `select`, `continue`, `pause`, `unpause`, `preview`, `history`); only add a new key when the action is genuinely new.

**Modals**
30. DEFAULT `max_length` = 40 unless YAML overrides; cap at 100 for free-text fields.
31. SET `placeholder` to a realistic example value (`"shroud"`, `"pewdiepie"`, `"No links here! :p"`).

**Forms**
32. ALWAYS open with `action: form` and close with `action: resume` (`":white_check_mark: Alright?"`).
33. EVERY step has the standard report footer (`"• Use the command /report to tell me a bug"` / pt-br equivalent).
34. EVERY step has both `en-us` and `pt-br` keys for `title`, `description`, and `footer`.

**Conditions**
35. IF the string is a slash command `desc:` → start with `"Keiko {verb}s..."`.
36. IF the string is an error → red embed + apology + recovery hint.
37. IF the string is a success/state-change → reuse `command-events.*` keys; do not write new ones.
38. IF showing variables to the user → use `-` bullets: `\n- `` `{var}` ``: {meaning}". Never `•` here.
39. IF a feature has multiple sub-options (designs, sources) → use a `design_select`-style step with header `"⬇️ Select one of the designs below:"` and footer `"⬆️ Scroll up to see all options"`. Do not hardcode a count.
40. IF the message describes an async/in-progress state → trailing `...` is allowed and expected.

---

# 5. Prompt Snippet (compact)

```
Follow the Keiko Writing Style Guide:

PERSONALITY: Friendly, loyal dog companion. Warm but not childish. One cute beat per message max. Subtle dog puns (e.g., pt-br "au au" wordplay like *aujuda*) are welcome when natural.

VOICE:
- 3rd person ("Keiko {verbs}...") ONLY in slash command `desc:` fields.
- 1st person ("I", "my", "me") in every embed, modal, error, response, and form.

FORMAT:
- Title = "{one-emoji} {Title Case}". Errors auto-prefix 🚨.
- Footer always starts with "• ".
- Bold **Keiko**, **$command_name**. Backtick `/commands`.
- Variables: `{user}` user-facing; `$user` system/event descriptions.
- Bullets: `-` or numbers in body; `•` only as footer prefix.
- NEVER em-dash (`—`) or en-dash (`–`). Use commas, periods, or two sentences. Sounds AI-like.
- `...` ONLY for async/in-progress states (`🔄 Syncing roles...`); never stylistic.

ERRORS: red embed (#ff0000), apology ("I'm sorry, but..." / "Oops!"), recovery hint, end with `:/` or no emoji.
SUCCESS: blue embed (#4F97F9), title "{emoji} {Action} successfully!", optional warm closer ("Now everyone is even cuter! 🐶").
INSTRUCTIONS: 1st-person, direct ("Choose which channel I should..."), single trailing emoji, ≤4 sentences.

VOCABULARY: prefer cozy, warmly, lovingly, loyal, personalized, just call me, don't hesitate. Avoid execute/invoke/initialize, ALL CAPS, slang, memes.

LOCALIZATION: every new string lives in en-us AND pt-br under app/languages/. Never hardcode in Python — go through ml(key, locale).

EMBEDS: default color 0x4F97F9 (Style.BACKGROUND_COLOR), error 0xff0000. Use `description` for short bodies, `add_field(inline=False)` for stacked records. Thumbnail = KeikoIcons.IMAGE_01.

BUTTONS: green=Confirm, red=Cancel, primary=Back/selected, gray=everything else. Reuse generic keys from buttons.{lang}.yml.

FORMS: open with `action: form`, close with `action: resume` (":white_check_mark: Alright?"). Standard step footer: "• Use the command /report to tell me a bug".
```

---

# 6. Skill (auto-activating)

The full `SKILL.md` lives at [`.claude/skills/keiko-writing-style/SKILL.md`](../.claude/skills/keiko-writing-style/SKILL.md). It auto-activates when writing or editing user-facing copy in this repository.
