# Code style rules

Patterns established through PR review. Every one of these was pointed out by the
maintainer on real code; do not reintroduce them.

## 1. No narrative comments in code

Do not write comments that explain design rationale, history ("this used to break
because..."), or anything the code already says. Multi-line comment blocks are never
acceptable. Design rationale belongs in `docs/`, the commit message, or the PR
description — not in the source.

```python
# WRONG
# Never clear_items() here: the ViewStore reads `item.view` live, so
# clearing a view whose message is still on screen kills every button
# the user can see. The transition below replaces the message.
parent_view = self.view

# RIGHT
parent_view = self.view
```

The rare fact that is essential and impossible to express in code may keep ONE short
line. Short docstrings on public modules/classes/functions are fine; docstrings never
appear mid-function.

## 2. Magic values become constants in `app/constants.py`

Never define module-level constants in components, views, or services. A shared or
tunable value (a Discord limit, a window in seconds) goes to the matching class in
`app/constants.py` (`DiscordLimits`, `Commands`, `Style`...).

## 3. No hyper-specific constant explosions

A constant exists for a genuinely shared or tunable value — not to rename every domain
literal. Closed vocabularies (enum values stored in documents, i18n key suffixes like
`"block_all"`, `"role-exempt"`) are used as literals where they occur. Field-name
`*_KEY` constants follow the pre-existing project pattern and are fine.

## 4. No module-level "constants" in services

i18n namespaces and similar strings live inline in the helper that uses them, not as
module-level names in `app/services/*.py`.

## 5. `app/data/` follows the flat existing pattern

Plain functions calling `mongo_client.guild.<collection>` directly — exactly like
`app/data/moderations.py`. No `DATABASE`/`COLLECTION` constants, no `_collection()`
helper, no alternative database names.

## 6. Data-layer files are named after the feature

The file name matches the feature/cog key (`block_links.py`), not the content it
stores (`blocked_links.py`).

## 7. `app/views/` is generic infrastructure only

Files in `app/views/` must be generic and reusable across commands. A screen that
belongs to one command lives in that command's service module and composes the generic
views (`PaginationView`, `ManagerPanelView`...).

## 8. Redis key constants carry a `REDIS_` prefix

When a Redis key template lives in `app/constants.py`, its NAME starts with `REDIS_`
(or `CACHE_`), and the key format follows the existing convention in
`app/services/cache.py` (`guild:{guild_id}:...`).

## 9. One-off migration scripts are not versioned

Data-migration scripts run once; they do not enter the repository. Keep them outside
the repo (the maintainer stores them with his task notes) and record what was run in
the PR description or deploy notes.

## 10. Reusable orchestration becomes a generic view

Rule 7 says where a command's screen lives; this rule says what must be extracted
from it. When a screen's structure could serve another command — fetch records →
format → paginate/filter, an interaction round trip, an embed skeleton — the
orchestration becomes (or extends) a generic primitive (`RecordsBrowser`,
`PaginationView`, `ManagerPanelView` in `app/views/`; `base_embed` in
`app/components/embed.py`), and the feature service keeps only the fetch, the
formatter, and the copy. The smell: near-identical screen functions accumulating
across `app/services/*.py`.

## 11. Feature services hold only domain logic

A helper with no feature-specific content (URL parsing, timestamp formatting,
envelope unwrapping) does not live in a feature service — it goes to the generic
home: `app/services/utils.py`, a focused module like `app/services/dates.py`, or
`app/components/embed.py` for embeds. Absolute red flag: generic engine code
(validators, transforms, the form engine) importing from a feature service — that
inversion means the helper is in the wrong module.

## 12. View/UI tunables are global

Timeouts, cooldowns, notice lifetimes, and preview limits live in `ViewConstants`
(`app/constants.py`), and every view/modal reads them from there — never a literal,
never a command-prefixed constant. Domain numerics (a retention TTL, a per-message
cap) keep the feature prefix in `Commands`. Refines rules 2-3: a UI tunable is
always "genuinely shared".
