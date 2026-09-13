# UX changes of the form platform migration

The golden transcripts under `tests/behavioral/golden/` are the record of what an
admin sees today, path by path. A migration of the engine is invisible to admins
unless a change is listed here first (review II.3). Every row names the golden it
moves; the golden test accepts the row's id in `allowed=` and nothing else.

Classes: `invisible` (ids, internal encodings: never listed, the normalizer drops
them), `cosmetic` (a footer, an icon), `behavioural` (a new notice, a different
message count, a different choreography).

| id | form / screen | before | after | class | reason | golden affected | since |
|---|---|---|---|---|---|---|---|
| ux-1 | every form, any button or select | a click on an outdated screen is routed to the current handler (deterministic ids) or fails silently (random ids); a double click on Done / Confirm runs the step twice | a short ephemeral notice ("this screen is out of date, here is the current one") and the current screen is redrawn once; the second click of a double click is ignored | behavioural | dedup by interaction id and revision in the adapter (review 4.6, plan 3.5) | none of the recorded paths clicks twice or on an old screen; new scenarios in Phase C | Phase D |
| ux-2 | every form, after 30 minutes without a click | the buttons stay on the message and every click fails with "This interaction failed" | the message loses its buttons and says the form expired, in the admin's locale | behavioural | expiry becomes a visible transition (review C2, H6) | none of the recorded paths waits for a timeout; `scenario.expire()` in Phase C | Phase D |
| ux-3 | options steps, confirming with nothing selected | a public `channel.send` in the channel where the command ran | an ephemeral error embed, like every other validation error | behavioural, a bug fix | one error channel (review G13) | none of the recorded paths confirms an empty options step | Phase D |
| ux-4 | every transition from a card or gallery back to an embed screen | the old message is deleted first, then the new one is sent; when the delete fails the admin loses the screen (H11) | the new message is sent first, then the old one is deleted | invisible when it succeeds, behavioural when the delete fails | one replacement choreography in the effect executor (review P3, H11) | every golden with `delete` before `followup_send`: block_links edit and remove, default_roles edit, twitch and youtube add, edit and remove, birthday edit and remove, every manager pause / disable | Phase D |
| ux-5 | adding an item from a Components V2 card (block_links from the panel, birthday member card) | the final "item added" embed cannot be edited into the card (Discord refuses embeds on a Components V2 message), so the bot sends it as a new message and the card stays on screen with buttons that no longer work | the card is replaced by the "item added" message | behavioural, a bug fix | the renderer decides embed vs Components V2 per screen and replaces instead of editing (review H8, H12) | `block_links/manager_add_item`, `reminders_birthday/manager_add_item` | Phase D |
