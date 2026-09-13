# Keiko error history vs. the proposed Form-engine re-architecture

Sources: **A** = `~/.keiko/logs.db` (daily log files 2024-07-27 .. 2026-08-20, 2355 ERROR + 676 WARNING). **B** = production Mongo `guild.logs`, 2026-08-21 .. 2026-09-13 (988 ERROR + 2263 WARNING = 3251; the collection has no documents before 2026-08-21).

Signature = exception type + normalized first message line (ids/digits/urls/quoted strings/hex -> placeholders) + last `app/` frame of the traceback. Records whose first line is a generic wrapper ("Task exception was never retrieved", "Ignoring exception in X", "Exception on /webhooks/...") are keyed on the exception line inside the traceback instead.

## Totals

| class | source A (ERROR+WARNING) | source B (ERROR+WARNING) |
|---|---:|---:|
| FORM_ENGINE | 21 | 6 |
| PERSISTENCE | 266 | 0 |
| INTEGRATION_WEBHOOK | 489 | 325 |
| LOGGER_INFRA | 2124 | 2920 |
| DISCORD_LIBRARY | 68 | 0 |
| OTHER | 63 | 0 |
| **total** | **3031** | **3251** |

Reading: over two years the interactive configuration flows (FORM_ENGINE) produced **21** records in A (19 ERROR) and **6** in B (4 ERROR, 2 WARNING). PERSISTENCE produced 266 in A (266 ERROR; 210 of them are one `KeyError: allowed_chats` defect double-logged) and 0 in B. Everything else is webhook/integration, logger, port-scanner or gateway noise. Note the double-logging caveat in Limitations: "Error in on_message: X" (app logger) and "Ignoring exception in on_message :: X" (discord.py) are the same incidents, so PERSISTENCE and INTEGRATION_WEBHOOK counts in A are roughly 2x the incident count.

## Error signatures

Top 40 groups by combined count (A+B). `guilds` is 0 where the record carries no guild_id (all of A; all of B except the 6 form-engine records).

| # | signature | class | mechanism | count A | count B | first seen | last seen | guilds | app frame |
|---|---|---|---|---:|---:|---|---|---:|---|
| 1 | We are being rate limited. POST <url> responded with <n>. Retrying in <n>.<n> seconds. | LOGGER_INFRA | 429 on POST to the two log channels (logger.emit -> log_channel.send) | 448 | 1871 | 2026-04-13 | 2026-09-06 | 0 | - |
| 2 | <ip> - - [<date>] code <n>, message Bad request version/syntax / Bad HTTP request type / Invalid HTTP version | LOGGER_INFRA | werkzeug port-scanner noise on the Flask webhook port | 563 | 984 | 2026-04-07 | 2026-09-10 | 0 | - |
| 3 | Task exception was never retrieved :: aiohttp.client_exceptions.ClientConnectorError: Cannot connect to host discord.com:<n> ssl:default [Te | LOGGER_INFRA | gateway/DNS outage burst (single day) | 714 | 0 | 2024-12-23 | 2024-12-23 | 0 | - |
| 4 | We are being rate limited. GET <url> responded with <n>. Retrying in <n>.<n> seconds. | INTEGRATION_WEBHOOK | 429 on message fetch (channels/<id>/messages/<id>); caller not in record | 20 | 324 | 2026-08-14 | 2026-09-12 | 0 | - |
| 5 | Task exception was never retrieved :: In embeds.<n>.description: Must be <n> or fewer in length. | LOGGER_INFRA | embed size limit on Messageable.send (no app frame; log-embed sender) | 141 | 0 | 2025-03-07 | 2025-06-14 | 0 | - |
| 6 | Invalid Youtube webhook data | INTEGRATION_WEBHOOK | youtube webhook payload rejected | 131 | 0 | 2025-02-10 | 2026-07-18 | 0 | - |
| 7 | Shard ID None heartbeat blocked for more than <n> seconds. (A: app frame data/cogs.py:find_cog_by_guild_id 55, cache.py:get_cog_data_or_popu | LOGGER_INFRA | event loop blocked by sync call (frame shows the blocking app call) | 74 | 55 | 2026-05-04 | 2026-09-05 | 0 | - |
| 8 | KeyError: Error in on_message: KeyError: 'allowed_chats' @ app/services/block_links.py:check_message | PERSISTENCE | document shape / legacy document | 105 | 0 | 2026-05-18 | 2026-07-12 | 0 | app/services/block_links.py:check_message |
| 9 | Ignoring exception in on_message :: KeyError: 'allowed_chats' @ app/services/block_links.py:check_message | PERSISTENCE | document shape / legacy document | 105 | 0 | 2026-05-18 | 2026-07-12 | 0 | app/services/block_links.py:check_message |
| 10 | Task exception was never retrieved :: AttributeError: 'NoneType' object has no attribute 'get_channel' @ app/services/notifications_twitch.p | INTEGRATION_WEBHOOK | twitch notification service | 90 | 0 | 2025-06-11 | 2026-03-27 | 0 | app/services/notifications_twitch.py:update_notification_status |
| 11 | Task exception was never retrieved :: AttributeError: 'NoneType' object has no attribute 'get_channel' @ app/services/notifications_twitch.p | INTEGRATION_WEBHOOK | twitch notification service | 89 | 0 | 2025-06-11 | 2026-03-27 | 0 | app/services/notifications_twitch.py:process_notifications |
| 12 | PyNaCl is not installed, voice will NOT be supported | LOGGER_INFRA | startup warning (one per restart) | 83 | 4 | 2024-07-27 | 2026-09-13 | 0 | - |
| 13 | Task exception was never retrieved :: discord.errors.DiscordServerError: <n> Internal Server Error (error code: <n>): <n>: Internal Server E | DISCORD_LIBRARY | Discord 5xx outage (no form/manager frame) | 55 | 0 | 2024-11-17 | 2024-11-17 | 0 | - |
| 14 | Task exception was never retrieved :: discord.errors.HTTPException: <n> Too Many Requests (error code: <n>): You are being rate limited. | INTEGRATION_WEBHOOK | 429 raised in an unretrieved task (no app frame) | 23 | 0 | 2025-04-01 | 2026-05-13 | 0 | - |
| 15 | pymongo.errors.ServerSelectionTimeoutError: Error in on_message: ServerSelectionTimeoutError: No replica set members match selector "Primary | PERSISTENCE | other (Mongo Atlas unreachable; sync find_one on the event loop) | 23 | 0 | 2026-03-25 | 2026-06-25 | 0 | app/data/cogs.py:find_cog_by_guild_id |
| 16 | Ignoring exception in on_message :: pymongo.errors.ServerSelectionTimeoutError: No replica set members match selector "Primary()", Timeout:  | PERSISTENCE | other (Mongo Atlas unreachable; sync find_one on the event loop) | 21 | 0 | 2026-05-13 | 2026-06-25 | 0 | app/data/cogs.py:find_cog_by_guild_id |
| 17 | Error on request: :: AttributeError: '_MissingSentinel' object has no attribute 'create_task' @ app/logger.py:emit | LOGGER_INFRA | logger bug (emit before loop ready / exc_info collision / DM has no guild) | 20 | 0 | 2024-08-27 | 2024-12-07 | 0 | app/logger.py:emit |
| 18 | Exception on /v<n>/webhooks/twitch [POST] :: AttributeError: 'NoneType' object has no attribute 'get' @ app/services/notifications_twitch.py | INTEGRATION_WEBHOOK | twitch notification service | 20 | 0 | 2024-11-15 | 2025-01-18 | 0 | app/services/notifications_twitch.py:create_stream_notification_embed |
| 19 | Task exception was never retrieved :: KeyError: 'swzz<n>' @ app/services/notifications_twitch.py:parse_streamer_message | INTEGRATION_WEBHOOK | twitch notification service | 19 | 0 | 2025-09-08 | 2025-09-28 | 0 | app/services/notifications_twitch.py:parse_streamer_message |
| 20 | Task exception was never retrieved :: AttributeError: 'ContextMenu' object has no attribute '_attr' @ app/cogs/errors.py:send_default_error_ | OTHER | cog-level bug (error handler / help in DM / report) | 16 | 0 | 2025-01-24 | 2026-01-26 | 0 | app/cogs/errors.py:send_default_error_message |
| 21 | Task exception was never retrieved :: discord.errors.NotFound: <n> Not Found (error code: <n>): Unknown Message @ app/services/notifications | INTEGRATION_WEBHOOK | twitch notification service | 16 | 0 | 2025-02-14 | 2026-04-01 | 0 | app/services/notifications_twitch.py:fetch_notification_message |
| 22 | AttributeError: Failed to handle twitch notification: AttributeError: 'NoneType' object has no attribute 'get_channel' @ app/services/notifi | INTEGRATION_WEBHOOK | twitch notification service | 16 | 0 | 2026-02-20 | 2026-03-27 | 0 | app/services/notifications_twitch.py:process_notifications |
| 23 | AttributeError: Failed to handle twitch offline notification: AttributeError: 'NoneType' object has no attribute 'get_channel' @ app/service | INTEGRATION_WEBHOOK | twitch notification service | 16 | 0 | 2026-02-20 | 2026-03-27 | 0 | app/services/notifications_twitch.py:update_notification_status |
| 24 | davey is not installed, voice will NOT be supported | LOGGER_INFRA | startup warning (one per restart) | 12 | 4 | 2026-03-20 | 2026-09-13 | 0 | - |
| 25 | Exception on /api/webhooks/twitch [POST] :: AttributeError: '_MissingSentinel' object has no attribute 'create_task' @ app/logger.py:emit | LOGGER_INFRA | logger bug (emit before loop ready / exc_info collision / DM has no guild) | 12 | 0 | 2024-08-27 | 2024-08-27 | 0 | app/logger.py:emit |
| 26 | aiohttp.client_exceptions.WSServerHandshakeError: Attempting a reconnect in <n>.<n>s | LOGGER_INFRA | gateway/network reconnect | 11 | 0 | 2026-04-28 | 2026-06-09 | 0 | - |
| 27 | Can't keep up, shard ID None websocket is <n>.<n>s behind. | LOGGER_INFRA | gateway lag | 8 | 2 | 2026-05-04 | 2026-09-08 | 0 | - |
| 28 | Exception on /v<n>/webhooks/twitch [POST] :: AttributeError: '_MissingSentinel' object has no attribute 'create_task' @ app/logger.py:emit | LOGGER_INFRA | logger bug (emit before loop ready / exc_info collision / DM has no guild) | 8 | 0 | 2024-12-07 | 2024-12-07 | 0 | app/logger.py:emit |
| 29 | ```: The following command raised an exception: **help**```AttributeError: 'NoneType' object has no attribute 'select'``` @ app/components/s | FORM_ENGINE | view lifecycle (cleared or expired view) | 8 | 0 | 2026-02-24 | 2026-03-19 | 0 | app/components/select.py:update |
| 30 | Somehow timed out waiting for chunks for guild ID <id>. | LOGGER_INFRA | gateway lag | 7 | 0 | 2026-07-09 | 2026-07-09 | 0 | - |
| 31 | Exception on /api/webhooks/youtube [POST] :: IndexError: list index out of range @ app/integrations/youtube.py:get_video_info | INTEGRATION_WEBHOOK | youtube / reminder integration | 6 | 0 | 2024-08-21 | 2024-08-21 | 0 | app/integrations/youtube.py:get_video_info |
| 32 | aiohttp.client_exceptions.ClientConnectorError: Attempting a reconnect in <n>.<n>s | LOGGER_INFRA | gateway/network reconnect | 6 | 0 | 2026-06-30 | 2026-06-30 | 0 | - |
| 33 | Task exception was never retrieved :: ValueError: Single '}' encountered in format string @ app/services/notifications_twitch.py:parse_strea | INTEGRATION_WEBHOOK | twitch notification service | 5 | 0 | 2025-06-30 | 2026-04-02 | 0 | app/services/notifications_twitch.py:parse_streamer_message |
| 34 | Failed to create reminder title=birthday_reminder date=<n>-<n> | INTEGRATION_WEBHOOK | youtube / reminder integration | 5 | 0 | 2026-07-31 | 2026-08-08 | 0 | - |
| 35 | In type: FileUploadModal error: HTTPException: <n> Bad Request (error code: <n>): Invalid Form Body @ app/views/form.py:show_modal | FORM_ENGINE | other (modal built with a component type Discord rejects: In type must be one of {4,5,6,7,10,12}) | 4 | 1 | 2026-07-14 | 2026-09-09 | 1 | app/views/form.py:show_modal |
| 36 | Streamer jwayfps already subscribed | INTEGRATION_WEBHOOK | twitch notification service | 4 | 0 | 2024-09-13 | 2024-12-06 | 0 | - |
| 37 | Streamer vanessay_ already subscribed | INTEGRATION_WEBHOOK | twitch notification service | 4 | 0 | 2024-11-26 | 2024-12-23 | 0 | - |
| 38 | View interaction referencing unknown view for item <EditButton style=<ButtonStyle.secondary: <n>> url=None disabled=False label='Editar' emo | FORM_ENGINE | view lifecycle (cleared or expired view) | 2 | 2 | 2026-04-25 | 2026-09-10 | 1 | - |
| 39 | Task exception was never retrieved :: asyncio.exceptions.TimeoutError | OTHER | unclassified | 3 | 0 | 2024-10-23 | 2026-05-13 | 0 | - |
| 40 | ```: The following command raised an exception: **help**```AttributeError: 'NoneType' object has no attribute 'id'``` @ app/cogs/base/help.p | OTHER | cog-level bug (error handler / help in DM / report) | 3 | 0 | 2024-12-10 | 2025-03-06 | 0 | app/cogs/base/help.py:is_guild_command |

Long tail: 90 more groups with 115 records in A and 4 in B (mostly 1-2 records each: welcome-image `UnidentifiedImageError` triplets, single twitch `Unknown Message`, 8 more FORM_ENGINE/PERSISTENCE singletons listed below).

All FORM_ENGINE and PERSISTENCE groups (including those outside the top 40):

| # | signature | class | mechanism | A | B | first | last |
|---|---|---|---|---:|---:|---|---|
| 8 | KeyError: Error in on_message: KeyError: 'allowed_chats' @ app/services/block_links.py:check_message | PERSISTENCE | document shape / legacy document | 105 | 0 | 2026-05-18 | 2026-07-12 |
| 9 | Ignoring exception in on_message :: KeyError: 'allowed_chats' @ app/services/block_links.py:check_message | PERSISTENCE | document shape / legacy document | 105 | 0 | 2026-05-18 | 2026-07-12 |
| 15 | pymongo.errors.ServerSelectionTimeoutError: Error in on_message: ServerSelectionTimeoutError: No replica set members match selector "Primary()", Timeo | PERSISTENCE | other (Mongo Atlas unreachable; sync find_one on the event loop) | 23 | 0 | 2026-03-25 | 2026-06-25 |
| 16 | Ignoring exception in on_message :: pymongo.errors.ServerSelectionTimeoutError: No replica set members match selector "Primary()", Timeout: <n>s, Topo | PERSISTENCE | other (Mongo Atlas unreachable; sync find_one on the event loop) | 21 | 0 | 2026-05-13 | 2026-06-25 |
| 29 | The following command raised an exception: **help** AttributeError: 'NoneType' object has no attribute 'select' @ app/components/select.py:update | FORM_ENGINE | view lifecycle (cleared or expired view) | 8 | 0 | 2026-02-24 | 2026-03-19 |
| 35 | In type: FileUploadModal error: HTTPException: <n> Bad Request (error code: <n>): Invalid Form Body @ app/views/form.py:show_modal | FORM_ENGINE | other (modal built with a component type Discord rejects: In type must be one of {4,5,6,7,10,12}) | 4 | 1 | 2026-07-14 | 2026-09-09 |
| 38 | View interaction referencing unknown view for item <EditButton style=<ButtonStyle.secondary: <n>> url=None disabled=False label='Editar' emoji=<Partia | FORM_ENGINE | view lifecycle (cleared or expired view) | 2 | 2 | 2026-04-25 | 2026-09-10 |
| 43 | KeyError: Error in on_member_join: KeyError: 'welcome_messages_channel' @ app/services/welcome_messages.py:send_welcome_message | PERSISTENCE | document shape / legacy document | 3 | 0 | 2026-03-30 | 2026-03-30 |
| 53 | TypeError: Failed to set default roles on member join: TypeError: 'NoneType' object is not iterable @ app/services/default_roles.py:filter_roles | PERSISTENCE | document shape / legacy document | 2 | 0 | 2026-04-02 | 2026-04-03 |
| 54 | TypeError: Error in on_member_join: TypeError: 'NoneType' object is not iterable @ app/services/default_roles.py:filter_roles | PERSISTENCE | document shape / legacy document | 2 | 0 | 2026-04-02 | 2026-04-03 |
| 57 | KeyError: GreetingsView error: KeyError: 'en-gb' @ app/views/form.py:_set_titles_and_descriptions | FORM_ENGINE | locale handling | 2 | 0 | 2026-04-05 | 2026-04-05 |
| 58 | KeyError: SetupView error: KeyError: 'en-gb' @ app/views/form.py:_set_titles_and_descriptions | FORM_ENGINE | locale handling | 2 | 0 | 2026-04-05 | 2026-04-05 |
| 61 | pymongo.errors.ServerSelectionTimeoutError: Error in on_message: ServerSelectionTimeoutError: cluster<n>-shard-<n>-<n>.d<n>h<n>r.mongodb.net:<n>: time | PERSISTENCE | other (Mongo Atlas unreachable; sync find_one on the event loop) | 2 | 0 | 2026-06-25 | 2026-06-25 |
| 62 | Ignoring exception in on_message :: pymongo.errors.ServerSelectionTimeoutError: cluster<n>-shard-<n>-<n>.d<n>h<n>r.mongodb.net:<n>: timed out (configu | PERSISTENCE | other (Mongo Atlas unreachable; sync find_one on the event loop) | 2 | 0 | 2026-06-25 | 2026-06-25 |
| 74 | Task exception was never retrieved :: TypeError: DesignSelectView.on_error() takes <n> positional arguments but <n> were given @ app/components/select | FORM_ENGINE | message transition (edit with content on a Components-V2 message; on_error signature bug hid it) | 1 | 0 | 2026-03-09 | 2026-03-09 |
| 77 | pymongo.errors._OperationCancelled: Error in on_message: _OperationCancelled: operation cancelled @ app/data/cogs.py:find_cog_by_guild_id | PERSISTENCE | other (Mongo Atlas unreachable; sync find_one on the event loop) | 1 | 0 | 2026-03-25 | 2026-03-25 |
| 81 | KeyError: Form error: KeyError: 'en-US' @ app/services/utils.py:parse_command_event_description | FORM_ENGINE | locale handling | 1 | 0 | 2026-04-05 | 2026-04-05 |
| 92 | IndexError: EditCommand error: IndexError: list index out of range @ app/views/form.py:_handle_subscription | FORM_ENGINE | state mutation / stale in-memory state | 1 | 0 | 2026-04-25 | 2026-04-25 |
| 128 | Form error: NotFound: <n> Not Found (error code: 10062): Unknown interaction @ app/views/manager.py:add_item_callback | FORM_ENGINE | double or stale interaction | 0 | 1 | 2026-09-10 | 2026-09-10 |
| 129 | FileUploadModal error: NotFound: <n> Not Found (error code: 10008): Unknown Message @ app/views/panel_transitions.py:transition_to_embed | FORM_ENGINE | message transition (delete/edit/send ordering) | 0 | 1 | 2026-09-09 | 2026-09-09 |
| 130 | ChannelSelectView error: HTTPException: <n> Bad Request (error code: 50035): Invalid Form Body (In components.0 type) @ app/views/form.py:_send_layout | FORM_ENGINE | other (layout view sent with a component type Discord rejects for that message flavour) | 0 | 1 | 2026-09-09 | 2026-09-09 |

## Representative tracebacks for FORM_ENGINE and PERSISTENCE groups

### Group 8 - PERSISTENCE - document shape / legacy document
`KeyError: Error in on_message: KeyError: 'allowed_chats' @ app/services/block_links.py:check_message`

```
File "/app/app/decorators.py", line 82, in wrapper
    return await func(*args, **kwargs)
File "/app/app/cogs/events.py", line 86, in on_message
    await block_links_service.check_message(guild_id, message)
File "/app/app/services/block_links.py", line 52, in check_message
    allowed_chats = cogs[constants.BLOCK_LINKS_ALLOWED_CHATS_KEY].get("values")
KeyError: 'allowed_chats'
```

Evidence: `block_links.py:52 check_message -> cogs[constants.BLOCK_LINKS_ALLOWED_CHATS_KEY]` on a cached cog document that predates the `allowed_chats` key. 105 incidents (x2 logging) between 2026-05-18 and 2026-07-12, then stops - consistent with a data migration or a `.get()` fix. Same shape as `welcome_messages_channel` and `filter_roles(None)`.

### Group 9 - PERSISTENCE - document shape / legacy document
`Ignoring exception in on_message :: KeyError: 'allowed_chats' @ app/services/block_links.py:check_message`

```
File "/app/app/decorators.py", line 82, in wrapper
    return await func(*args, **kwargs)
File "/app/app/cogs/events.py", line 86, in on_message
    await block_links_service.check_message(guild_id, message)
File "/app/app/services/block_links.py", line 52, in check_message
    allowed_chats = cogs[constants.BLOCK_LINKS_ALLOWED_CHATS_KEY].get("values")
KeyError: 'allowed_chats'
```

Evidence: `block_links.py:52 check_message -> cogs[constants.BLOCK_LINKS_ALLOWED_CHATS_KEY]` on a cached cog document that predates the `allowed_chats` key. 105 incidents (x2 logging) between 2026-05-18 and 2026-07-12, then stops - consistent with a data migration or a `.get()` fix. Same shape as `welcome_messages_channel` and `filter_roles(None)`.

### Group 15 - PERSISTENCE - other (Mongo Atlas unreachable; sync find_one on the event loop)
`pymongo.errors.ServerSelectionTimeoutError: Error in on_message: ServerSelectionTimeoutError: No replica set members match selector "Primary()", Timeout: <n>s, `

```
File "/app/app/services/block_links.py", line 39, in check_message
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY)
File "/app/app/services/cache.py", line 38, in get_cog_data_or_populate
    data = cogs_data.find_cog_by_guild_id(str(guild_id), key)
File "/app/app/data/cogs.py", line 8, in find_cog_by_guild_id
    return mongo_client.guild[cog].find_one({"guild_id": str(guild_id)})
pymongo.errors.ServerSelectionTimeoutError: No replica set members match selector "Primary()", Timeout: 30s, Topology Description: <TopologyDescription id: 69bf21ea5536f6bea8577351, topology_type: ReplicaSetNoPrimary, servers: [<ServerDescription ('cluster0-shard-00-00.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-00.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>, <ServerDescription ('cluster0-shard-00-01.d7h6r.mongodb.net', 27017) server_type: RSSecondary, rtt: 0.13570685573420851>, <ServerDescription ('cluster0-shard-00-02.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-02.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>]>
```

Evidence: `data/cogs.py:8 find_cog_by_guild_id -> mongo_client.guild[cog].find_one(...)` is a synchronous pymongo call executed inside `on_message`; the same frame is the one the heartbeat-blocked warnings point at (55 of 74 warnings with a frame). Mongo outage, but every outage also stalls the gateway because the call is sync.

### Group 16 - PERSISTENCE - other (Mongo Atlas unreachable; sync find_one on the event loop)
`Ignoring exception in on_message :: pymongo.errors.ServerSelectionTimeoutError: No replica set members match selector "Primary()", Timeout: <n>s, Topology D @ a`

```
File "/app/app/services/block_links.py", line 39, in check_message
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY)
File "/app/app/services/cache.py", line 38, in get_cog_data_or_populate
    data = cogs_data.find_cog_by_guild_id(str(guild_id), key)
File "/app/app/data/cogs.py", line 8, in find_cog_by_guild_id
    return mongo_client.guild[cog].find_one({"guild_id": str(guild_id)})
pymongo.errors.ServerSelectionTimeoutError: No replica set members match selector "Primary()", Timeout: 30s, Topology Description: <TopologyDescription id: 6a00172cd10ab1e263793400, topology_type: ReplicaSetNoPrimary, servers: [<ServerDescription ('cluster0-shard-00-00.d7h6r.mongodb.net', 27017) server_type: RSSecondary, rtt: 0.13521193489432337>, <ServerDescription ('cluster0-shard-00-01.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-01.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>, <ServerDescription ('cluster0-shard-00-02.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-02.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>]>
```

Evidence: `data/cogs.py:8 find_cog_by_guild_id -> mongo_client.guild[cog].find_one(...)` is a synchronous pymongo call executed inside `on_message`; the same frame is the one the heartbeat-blocked warnings point at (55 of 74 warnings with a frame). Mongo outage, but every outage also stalls the gateway because the call is sync.

### Group 29 - FORM_ENGINE - view lifecycle (cleared or expired view)
`The following command raised an exception: **help** AttributeError: 'NoneType' object has no attribute 'select' @ app/components/select.py:update` (8x, 2026-02-24 .. 2026-03-19; stack is embedded in the message body)

```
File "/app/app/components/select.py", line 66, in update
    self.view.select.options = self.view.select.parse_options(data)
AttributeError: 'NoneType' object has no attribute 'select'
The above exception was the direct cause of the following exception:
File ".../discord/app_commands/commands.py", line 877, in _do_call
    raise CommandInvokeError(self, e) from e
discord.app_commands.errors.CommandInvokeError: Command 'help' raised an exception: AttributeError: 'NoneType' object has no attribute 'select'
```

Evidence: `components/select.py:66 update -> self.view.select.options = ...` with `self.view is None`: the Select item was detached from its View (view stopped/cleared) before the update ran. Raised from `/help`, which uses the shared select component.

### Group 35 - FORM_ENGINE - other (modal built with a component type Discord rejects: In type must be one of {4,5,6,7,10,12})
`In type: FileUploadModal error: HTTPException: <n> Bad Request (error code: <n>): Invalid Form Body @ app/views/form.py:show_modal`

```
File "/app/app/views/form.py", line 1087, in _callback
    return await self.get_action_by_type(action, interaction)
File "/app/app/views/form.py", line 980, in get_action_by_type
    return await action_dict[action](interaction)
File "/app/app/views/form.py", line 456, in show_modal
    await interaction.response.send_modal(self.view)
In type: Value must be one of {4, 5, 6, 7, 10, 12}.
```

Evidence: `form.py show_modal -> interaction.response.send_modal(self.view)` rejected by Discord with `In type: Value must be one of {4,5,6,7,10,12}` - the modal contains a component type Discord does not accept inside modals (FileUploadModal). Seen 2026-07-14 (x4) and again 2026-09-09 in prod: still open.

### Group 38 - FORM_ENGINE - view lifecycle (cleared or expired view)
`View interaction referencing unknown view for item <EditButton style=<ButtonStyle.secondary: <n>> url=None disabled=False label='Editar' emoji=<Partia`

```
(no traceback stored; discord.py WARNING line only)
```

Evidence: discord.py `dispatch_view` found no live View for `EditButton`/`ConfirmButton`/`AddItemButton` custom ids - a user clicked a manager-panel button whose View had been stopped, timed out, or belonged to a previous process (labels Editar/Confirmar/Edit/Add = manager panel and confirm views). 2 in A (2026-04-25, 2026-05-14), 2 in B (2026-09-10 12:14, both within 40 ms, 5 minutes before group with 10062 below in the same guild).

### Group 43 - PERSISTENCE - document shape / legacy document
`KeyError: Error in on_member_join: KeyError: 'welcome_messages_channel' @ app/services/welcome_messages.py:send_welcome_message`

```
File "/app/app/decorators.py", line 40, in wrapper
    return await func(*args, **kwargs)
File "/app/app/cogs/events.py", line 73, in on_member_join
    await send_welcome_message(member)
File "/app/app/services/welcome_messages.py", line 42, in send_welcome_message
    channel = cogs["welcome_messages_channel"].get("values")
KeyError: 'welcome_messages_channel'
```

Evidence: `welcome_messages.py:42 send_welcome_message -> cogs["welcome_messages_channel"].get("values")` on a document that lacks the key.

### Group 53 - PERSISTENCE - document shape / legacy document
`TypeError: Failed to set default roles on member join: TypeError: 'NoneType' object is not iterable @ app/services/default_roles.py:filter_roles`

```
File "/app/app/services/default_roles.py", line 31, in set_on_member_join
    await set_default_roles(cogs, member.guild, [member])
File "/app/app/services/default_roles.py", line 95, in set_default_roles
    constants.DEFAULT_ROLES_BOT_KEY: filter_roles(
File "/app/app/services/default_roles.py", line 129, in filter_roles
    return [role for role in roles if role in available_roles.values()]
TypeError: 'NoneType' object is not iterable
```

Evidence: `default_roles.py filter_roles` iterates a `None` roles value read from the guild config document.

### Group 54 - PERSISTENCE - document shape / legacy document
`TypeError: Error in on_member_join: TypeError: 'NoneType' object is not iterable @ app/services/default_roles.py:filter_roles`

```
File "/app/app/services/default_roles.py", line 31, in set_on_member_join
    await set_default_roles(cogs, member.guild, [member])
File "/app/app/services/default_roles.py", line 95, in set_default_roles
    constants.DEFAULT_ROLES_BOT_KEY: filter_roles(
File "/app/app/services/default_roles.py", line 129, in filter_roles
    return [role for role in roles if role in available_roles.values()]
TypeError: 'NoneType' object is not iterable
```

Evidence: `default_roles.py filter_roles` iterates a `None` roles value read from the guild config document.

### Group 57 - FORM_ENGINE - locale handling
`KeyError: GreetingsView error: KeyError: 'en-gb' @ app/views/form.py:_set_titles_and_descriptions`

```
File "/app/app/views/form.py", line 82, in _get_steps
    self._set_titles_and_descriptions(steps)
File "/app/app/views/form.py", line 329, in _set_titles_and_descriptions
    self.title_and_desc = {
File "/app/app/views/form.py", line 330, in <dictcomp>
    step["title"][self.locale]: step["description"][self.locale]
KeyError: 'en-gb'
```

Evidence: `form.py:330 _set_titles_and_descriptions -> step["title"][self.locale]`: the YAML has only en-us/pt-br and the user locale `en-gb` is used as a dict key without fallback. Same day, `utils.py:306 parse_command_event_description -> command.extras[interaction.locale.value]` with `en-US`.

### Group 58 - FORM_ENGINE - locale handling
`KeyError: SetupView error: KeyError: 'en-gb' @ app/views/form.py:_set_titles_and_descriptions`

```
File "/app/app/views/form.py", line 82, in _get_steps
    self._set_titles_and_descriptions(steps)
File "/app/app/views/form.py", line 329, in _set_titles_and_descriptions
    self.title_and_desc = {
File "/app/app/views/form.py", line 330, in <dictcomp>
    step["title"][self.locale]: step["description"][self.locale]
KeyError: 'en-gb'
```

Evidence: `form.py:330 _set_titles_and_descriptions -> step["title"][self.locale]`: the YAML has only en-us/pt-br and the user locale `en-gb` is used as a dict key without fallback. Same day, `utils.py:306 parse_command_event_description -> command.extras[interaction.locale.value]` with `en-US`.

### Group 61 - PERSISTENCE - other (Mongo Atlas unreachable; sync find_one on the event loop)
`pymongo.errors.ServerSelectionTimeoutError: Error in on_message: ServerSelectionTimeoutError: cluster<n>-shard-<n>-<n>.d<n>h<n>r.mongodb.net:<n>: timed out (con`

```
File "/app/app/services/block_links.py", line 39, in check_message
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY)
File "/app/app/services/cache.py", line 38, in get_cog_data_or_populate
    data = cogs_data.find_cog_by_guild_id(str(guild_id), key)
File "/app/app/data/cogs.py", line 8, in find_cog_by_guild_id
    return mongo_client.guild[cog].find_one({"guild_id": str(guild_id)})
pymongo.errors.ServerSelectionTimeoutError: cluster0-shard-00-01.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms),cluster0-shard-00-00.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms),cluster0-shard-00-02.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms), Timeout: 30s, Topology Description: <TopologyDescription id: 6a00172cd10ab1e263793400, topology_type: ReplicaSetNoPrimary, servers: [<ServerDescription ('cluster0-shard-00-00.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-00.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>, <ServerDescription ('cluster0-shard-00-01.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-01.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>, <ServerDescription ('cluster0-shard-00-02.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-02.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>]>
```

Evidence: `data/cogs.py:8 find_cog_by_guild_id -> mongo_client.guild[cog].find_one(...)` is a synchronous pymongo call executed inside `on_message`; the same frame is the one the heartbeat-blocked warnings point at (55 of 74 warnings with a frame). Mongo outage, but every outage also stalls the gateway because the call is sync.

### Group 62 - PERSISTENCE - other (Mongo Atlas unreachable; sync find_one on the event loop)
`Ignoring exception in on_message :: pymongo.errors.ServerSelectionTimeoutError: cluster<n>-shard-<n>-<n>.d<n>h<n>r.mongodb.net:<n>: timed out (configured ti @ a`

```
File "/app/app/services/block_links.py", line 39, in check_message
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY)
File "/app/app/services/cache.py", line 38, in get_cog_data_or_populate
    data = cogs_data.find_cog_by_guild_id(str(guild_id), key)
File "/app/app/data/cogs.py", line 8, in find_cog_by_guild_id
    return mongo_client.guild[cog].find_one({"guild_id": str(guild_id)})
pymongo.errors.ServerSelectionTimeoutError: cluster0-shard-00-01.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms),cluster0-shard-00-00.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms),cluster0-shard-00-02.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms), Timeout: 30s, Topology Description: <TopologyDescription id: 6a00172cd10ab1e263793400, topology_type: ReplicaSetNoPrimary, servers: [<ServerDescription ('cluster0-shard-00-00.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-00.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>, <ServerDescription ('cluster0-shard-00-01.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-01.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>, <ServerDescription ('cluster0-shard-00-02.d7h6r.mongodb.net', 27017) server_type: Unknown, rtt: None, error=NetworkTimeout('cluster0-shard-00-02.d7h6r.mongodb.net:27017: timed out (configured timeouts: socketTimeoutMS: 20000.0ms, connectTimeoutMS: 20000.0ms)')>]>
```

Evidence: `data/cogs.py:8 find_cog_by_guild_id -> mongo_client.guild[cog].find_one(...)` is a synchronous pymongo call executed inside `on_message`; the same frame is the one the heartbeat-blocked warnings point at (55 of 74 warnings with a frame). Mongo outage, but every outage also stalls the gateway because the call is sync.

### Group 74 - FORM_ENGINE - message transition (edit with content on a Components-V2 message; on_error signature bug hid it)
`Task exception was never retrieved :: TypeError: DesignSelectView.on_error() takes <n> positional arguments but <n> were given @ app/components/select_views.py:`

```
File "/app/app/components/select_views.py", line 257, in interaction_check
    await interaction.response.edit_message(
TypeError: DesignSelectView.on_error() takes 3 positional arguments but 4 were given
```

Evidence: `select_views.py:257 interaction_check -> interaction.response.edit_message(content=...)` on a message sent with `MessageFlags.IS_COMPONENTS_V2` (Discord: "content field cannot be used"), then `on_error()` has the wrong arity so the real error surfaces as a TypeError.

### Group 77 - PERSISTENCE - other (Mongo Atlas unreachable; sync find_one on the event loop)
`pymongo.errors._OperationCancelled: Error in on_message: _OperationCancelled: operation cancelled @ app/data/cogs.py:find_cog_by_guild_id`

```
File "/app/app/services/block_links.py", line 39, in check_message
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY)
File "/app/app/services/cache.py", line 38, in get_cog_data_or_populate
    data = cogs_data.find_cog_by_guild_id(str(guild_id), key)
File "/app/app/data/cogs.py", line 8, in find_cog_by_guild_id
    return mongo_client.guild[cog].find_one({"guild_id": str(guild_id)})
pymongo.errors._OperationCancelled: operation cancelled
```

Same sync `find_one` frame as the ServerSelectionTimeout group.

### Group 81 - FORM_ENGINE - locale handling
`KeyError: Form error: KeyError: 'en-US' @ app/services/utils.py:parse_command_event_description`

```
File "/app/app/views/form.py", line 599, in _finish
    embed.description = parse_command_event_description(
File "/app/app/services/utils.py", line 306, in parse_command_event_description
    command_name = command.extras[interaction.locale.value].get("locale_qualified_name")
KeyError: 'en-US'
```

### Group 92 - FORM_ENGINE - state mutation / stale in-memory state
`IndexError: EditCommand error: IndexError: list index out of range @ app/views/form.py:_handle_subscription`

```
File "/app/app/views/manager.py", line 85, in update_command
    await self.edited_form_view.pre_finish_step(interaction)
File "/app/app/views/form.py", line 654, in pre_finish_step
    sub["handler"](interaction, sub["subscribe"], sub["unsubscribe"], sub["key"])
File "/app/app/views/form.py", line 666, in _handle_subscription
    new_entry = self.responses[0]["value"][index]
IndexError: list index out of range
```

Evidence: `manager.py:85 update_command -> form.py:654 pre_finish_step -> form.py:666 _handle_subscription: self.responses[0]["value"][index]` IndexError: the edit flow (`edit.py -> manager.update_command`) re-enters the form callback chain and the subscription handler indexes `self.responses` with an index computed for a different (previous) list - in-memory state on the FormView shared between the edit path and the finish path.

### Group 128 - FORM_ENGINE - double or stale interaction
`Form error: NotFound: <n> Not Found (error code: 10062): Unknown interaction @ app/views/manager.py:add_item_callback`

```
File "/app/app/views/form.py", line 166, in update_counter
    return await self._after_callback(args)
File "/app/app/views/form.py", line 299, in _after_callback
    return await self.after_callback(interaction)
File "/app/app/views/composition.py", line 84, in finish
    await self.parent_callback(interaction)
File "/app/app/views/form.py", line 166, in update_counter
    return await self._after_callback(args)
File "/app/app/views/form.py", line 299, in _after_callback
    return await self.after_callback(interaction)
File "/app/app/views/manager.py", line 351, in add_item_callback
    await interaction.response.defer(ephemeral=True)
```

Evidence: the chain `form.update_counter -> _after_callback -> composition.finish -> parent_callback -> form.update_counter -> _after_callback -> manager.add_item_callback -> interaction.response.defer()` runs two nested after-callbacks on one interaction before deferring; by then the interaction token is unknown (already responded to, or the 3 s window passed). Same guild/user had the two "unknown view" warnings 5 minutes earlier.

### Group 129 - FORM_ENGINE - message transition (delete/edit/send ordering)
`FileUploadModal error: NotFound: <n> Not Found (error code: 10008): Unknown Message @ app/views/panel_transitions.py:transition_to_embed`

```
File "/app/app/components/select_views.py", line 430, in on_submit
    await self.custom_callback(interaction)
File "/app/app/views/form.py", line 1381, in _callback
    return await self.get_action_by_type(action, interaction)
File "/app/app/views/form.py", line 979, in show_buttons
    await self._transition_from_layout_view(interaction, self.step_embed, self)
File "/app/app/views/form.py", line 1347, in _transition_from_layout_view
    await transition_to_embed(
File "/app/app/views/panel_transitions.py", line 36, in transition_to_embed
    await interaction.response.edit_message(embed=embed, view=view)
discord.errors.NotFound: 404 Not Found (error code: 10008): Unknown Message
```

Evidence: `form.py:979 show_buttons -> _transition_from_layout_view -> panel_transitions.py:36 transition_to_embed -> interaction.response.edit_message(...)` returned 10008 Unknown Message: the layout message being edited had already been deleted (the flow deletes/replaces the layout message and then edits it). 5 s later the same user hit the FileUploadModal 50035 error, 100 s after the ChannelSelectView 50035 - one broken session on 2026-09-09.

### Group 130 - FORM_ENGINE - other (layout view sent with a component type Discord rejects for that message flavour)
`ChannelSelectView error: HTTPException: <n> Bad Request (error code: 50035): Invalid Form Body (In components.0 type) @ app/views/form.py:_send_layout_view`

```
File "/app/app/components/select_views.py", line 34, in callback
    await self.custom_callback(interaction)
File "/app/app/views/form.py", line 1381, in _callback
    return await self.get_action_by_type(action, interaction)
File "/app/app/views/form.py", line 1028, in show_design_select
    await self._send_layout_view(interaction)
File "/app/app/views/form.py", line 1370, in _send_layout_view
    await interaction.followup.send(view=self.view, ephemeral=True)
discord.errors.HTTPException: 400 Bad Request (error code: 50035): Invalid Form Body
In components.0: Value of field "type" must be one of (1, 9, 10, 12, 13, 14, 17).
```

Evidence: `form.py:1028 show_design_select -> _send_layout_view -> interaction.followup.send(view=self.view, ephemeral=True)` rejected with `In components.0: type must be one of (1,9,10,12,13,14,17)`: the layout view mixes a legacy component (ActionRow-less select) into a Components-V2 payload.

## Warnings that matter

Top 10 WARNING signatures (A count / B count):

| # | warning | A | B | note |
|---|---|---:|---:|---|
| 1 | We are being rate limited. POST <url> responded with <n>. Retrying in <n>.<n> seconds. | 448 | 1871 | Both channel ids (1246843815432552523, 1270222753030471733) are the same in A and B and match the logger log-channel sends; 1790 of the 1871 B hits are one channel on 2026-08-30/31 and 09-04..06 - the logger amplifies the port-scanner bursts (each werkzeug ERROR becomes an embed POST). |
| 2 | We are being rate limited. GET <url> responded with <n>. Retrying in <n>.<n> seconds. | 20 | 324 | 324 in B vs 20 in A: message fetches (`channels/<id>/messages/<id>`) across ~8 channels - new pattern since 2026-08-22; no traceback, caller unknown (pattern matches per-message fetch loops). |
| 3 | Shard ID None heartbeat blocked for more than <n> seconds. (A: app frame data/cogs.py:find_cog_by_guild_id 55, | 74 | 55 | In A, 74 of the 676 warnings carry a "Loop thread traceback" whose top app frame is a sync call on the event loop: pymongo find_one (55), redis get (9), requests image fetch (6), twitch wait_for_stream_info (4). On 2026-08-31 04:44-05:01 (B) the loop was blocked for 350 s continuously. |
| 4 | PyNaCl is not installed, voice will NOT be supported | 83 | 4 | One per process start: A 83 restarts in 2 years, B 4 restarts in 24 days. |
| 5 | davey is not installed, voice will NOT be supported | 12 | 4 | Same as PyNaCl (newer discord.py). |
| 6 | Can't keep up, shard ID None websocket is <n>.<n>s behind. | 8 | 2 |  |
| 7 | Somehow timed out waiting for chunks for guild ID <id>. | 7 | 0 |  |
| 8 | Streamer jwayfps already subscribed | 4 | 0 |  |
| 9 | Streamer vanessay_ already subscribed | 4 | 0 |  |
| 10 | View interaction referencing unknown view for item <EditButton style=<ButtonStyle.secondary: <n>> url=None dis | 2 | 2 | FORM_ENGINE: manager-panel buttons clicked after their View died. |

Other warnings: `Streamer <name> already subscribed` / `has more than one subscription` (A: 25 total, twitch subscription bookkeeping), `Somehow timed out waiting for chunks` (A: 8).

## Timeline

Source A - ERROR records per month (WARNING in parentheses):

| month | ERROR | (WARNING) | dominant group |
|---|---:|---:|---|
| 2024-07 | 0 | (2) |  |
| 2024-08 | 30 | (7) |  |
| 2024-09 | 2 | (2) |  |
| 2024-10 | 1 | (19) |  |
| 2024-11 | 60 | (9) |  |
| 2024-12 | 746 | (11) | 714x ClientConnectorError burst on 2024-12-23 (DNS/gateway), 20x logger _MissingSentinel |
| 2025-01 | 7 | (19) |  |
| 2025-02 | 8 | (1) |  |
| 2025-03 | 8 | (2) |  |
| 2025-04 | 11 | (1) |  |
| 2025-05 | 0 | (3) |  |
| 2025-06 | 202 | (1) | 141x embed description > 4096 (2025-06-13/14), 90+89 twitch get_channel None starts |
| 2025-07 | 75 | (1) |  |
| 2025-08 | 21 | (1) |  |
| 2025-09 | 43 | (1) |  |
| 2025-10 | 13 | (0) |  |
| 2025-11 | 5 | (3) |  |
| 2025-12 | 7 | (0) |  |
| 2026-01 | 36 | (1) |  |
| 2026-02 | 41 | (6) |  |
| 2026-03 | 72 | (7) |  |
| 2026-04 | 120 | (17) | DiscordServerError 503 bursts, welcome-image failures, form locale KeyErrors (04-05) |
| 2026-05 | 177 | (320) | KeyError allowed_chats starts 05-18; heartbeat-blocked warnings 05-13.. |
| 2026-06 | 555 | (65) | werkzeug port scans (04-07 onward, peak 06-13..06-30) + allowed_chats + Mongo timeouts 06-25 |
| 2026-07 | 92 | (100) | werkzeug + allowed_chats until 07-12; FileUploadModal 50035 on 07-14 |
| 2026-08 | 23 | (77) |  |

Source B - per day, 2026-08-21 .. 2026-09-13 (days with zero records omitted):

| day | ERROR | WARNING | note |
|---|---:|---:|---|
| 2026-08-21 | 0 | 2 |  |
| 2026-08-22 | 2 | 11 |  |
| 2026-08-23 | 0 | 2 |  |
| 2026-08-27 | 3 | 3 |  |
| 2026-08-28 | 2 | 0 |  |
| 2026-08-29 | 0 | 16 |  |
| 2026-08-30 | 1 | 186 | 186 = 429 POST to log channel |
| 2026-08-31 | 1 | 139 | heartbeat blocked 350 s, 429s |
| 2026-09-01 | 0 | 11 |  |
| 2026-09-03 | 4 | 1 |  |
| 2026-09-04 | 390 | 747 | 390 werkzeug in 20 s from one IP + 429 storm on the log channel |
| 2026-09-05 | 467 | 870 | 454 werkzeug in 20 s from one IP + 429 storm |
| 2026-09-06 | 106 | 175 | 93 werkzeug + 429 |
| 2026-09-07 | 1 | 1 |  |
| 2026-09-08 | 0 | 3 |  |
| 2026-09-09 | 4 | 0 | 3 FORM_ENGINE errors, one user session 08:35-08:37 |
| 2026-09-10 | 7 | 79 | FORM_ENGINE 10062 + 2 unknown-view warnings 12:14-12:19; 79 = 429 GET |
| 2026-09-11 | 0 | 2 |  |
| 2026-09-12 | 0 | 13 |  |
| 2026-09-13 | 0 | 2 |  |

## New in the last 30 days

Groups present in B and absent from A (A ends 2026-08-20):

- **Form error 10062 Unknown interaction @ manager.py:add_item_callback** (2026-09-10) - double or stale interaction through the nested composition -> manager after-callback chain. A has zero 10062 records in two years (FTS `10062`: 0), so this is new to the current manager/composition code.
- **FileUploadModal 10008 Unknown Message @ panel_transitions.py:transition_to_embed** (2026-09-09) - edit of a deleted layout message. A has 22 `10008` hits but all are twitch notification fetch/edit and the clear_chat error handler; none from a form view.
- **ChannelSelectView 50035 In components.0 type @ form.py:_send_layout_view** (2026-09-09) - layout view payload rejected. Not in A (`_send_layout_view`/`show_design_select` frames never appear there; the closest A ancestor is the 2026-03-09 DesignSelectView Components-V2 error).
- **429 on GET message fetch** at 324/24 days vs 20 in the last week of A - a new steady-state load pattern.
- Still present in both: FileUploadModal 50035 `In type` (A 2026-07-14 x4, B 2026-09-09), unknown-view warnings on manager buttons (A 2, B 2), heartbeat blocked, log-channel 429s, port-scanner noise.
- Gone in B: `KeyError allowed_chats` (last 2026-07-12), twitch `get_channel None` (last 2026-03-27), Mongo timeouts (last 2026-06-25), locale KeyErrors (2026-04-05 only).

## Limitations

- **Archive gaps (A):** 755 calendar days in range, 722 with at least one entry, **33 days with zero entries** (longest gap 3 days: 2024-07-28..30; the rest are 1-2 day gaps). 745 synced files hold 18054 entries. Days with no file may be days the bot did not run or days whose log file was never uploaded - cannot distinguish.
- **No guild ids in A:** guild_id is empty for all 3031 ERROR/WARNING rows, so "distinct guilds" is 0 for every A group. In B only the 6 form-engine records carry guild_id/user_id (2 guilds).
- **Tracebacks:** A has a traceback on 1656/2355 ERROR and 74/676 WARNING rows; 943 of the 1191 "Task exception was never retrieved" rows have no `app/` frame at all (discord.py internal tasks: ClientConnectorError, HTTP 5xx, embed-size), so their origin is inferred from the exception text only. B has a traceback on 4/988 ERROR rows; the other 984 are werkzeug lines with no stack.
- **Double logging in A:** the same incident is often logged twice - once by the app (`Error in on_message: X` / `Failed to handle twitch ...`) and once by discord.py (`Ignoring exception in on_message` / `Task exception was never retrieved`). Pairs: allowed_chats 105+105, ServerSelectionTimeout 23+21, twitch get_channel 90+89 and 16+16, welcome-image 3 per incident. Group counts are record counts, not incident counts.
- **Signature drift:** line numbers in `app/views/form.py` changed between A (show_modal at line 456) and B (line 703), so matching across sources used file:function, not line.
- **INFO-level "errors":** 83 INFO rows in A match error/exception keywords; all are the "Cogs ... errors, events ... loaded" startup line, not failures. B has no INFO rows that look like failures beyond the same startup line.
- **Mongo `guild.logs` starts 2026-08-21**, so B cannot be used to check whether A groups recurred between 2026-08-20 and 2026-08-21; the two sources do not overlap.
- Classification of rate-limit warnings: POST 429s were assigned to LOGGER_INFRA because both channel ids match the logger log-channel sends seen in A (same two ids since 2026-04); this is inferred from the URL, not from a stack frame. GET/DELETE 429s carry no caller and were assigned to INTEGRATION_WEBHOOK by the rate-limit rule.
