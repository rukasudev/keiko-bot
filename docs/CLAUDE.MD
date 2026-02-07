# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Keiko Bot is a Discord bot written in Python using discord.py 2.4.0. It provides server moderation, stream notifications (Twitch/YouTube), and various integrations (OpenAI, Notion, StreamElements).

## Common Commands

```bash
# Setup
make setup              # Create venv and install dependencies

# Development
make run                # Start bot with Docker services (Redis, MongoDB)
make docker-up          # Start only Redis and MongoDB containers
python __main__.py      # Run bot directly (requires services running)

# Cleanup
make clean              # Clean cache and venv
```

## Architecture

### Entry Point
`__main__.py` - Initializes config, loads cogs, sets up i18n, and starts the bot with Flask webhook API.

### Core Layers

- **`app/bot.py`** - Main DiscordBot class extending discord.ext.commands.Bot
- **`app/config.py`** - AppConfig handles dual environment: `.env` for dev, AWS SSM for production
- **`app/cogs/`** - Discord command modules (slash commands organized by feature)
- **`app/services/`** - Business logic layer (16 service modules)
- **`app/data/`** - MongoDB data access layer
- **`app/integrations/`** - Third-party API clients (Twitch, YouTube, OpenAI, Notion, StreamElements)
- **`app/webhooks/`** - Webhook handlers for external events
- **`app/api/`** - Flask REST API for health checks and webhooks

### Cog Organization

```
app/cogs/
├── base/           # Core commands (ping, help, translate, report)
├── admin/          # Admin commands (configs, cogs, subscriptions, sync)
├── moderations/    # Moderation (block links, welcome, roles)
├── notifications/  # Stream notifications (twitch, youtube)
└── integrations/   # External service commands (stream_elements)
```

### Internationalization

Language files in `app/languages/` (YAML format):
- `en-US.yaml` - English
- `pt-BR.yaml` - Portuguese

Uses python-i18n. Access translations via the i18n module.

### UI Components

- **`app/components/`** - Discord UI elements (buttons, modals, select menus, embeds)
- **`app/views/`** - Form handling, pagination, and option selection

## External Services

- **MongoDB**: Primary data store (mongodb://localhost:27017)
- **Redis**: Caching (redis://localhost:6379)
- **Twitch API**: Stream status and webhooks
- **YouTube API**: Video upload notifications
- **AWS SSM**: Production configuration secrets

## Deployment

Docker-based deployment via GitHub Actions (`.github/workflows/main.yaml`):
- Builds on push to main
- Pushes to Docker Hub
- Deploys to self-hosted EC2 runner

Ports: 5000 (Flask API), 8000 (Bot)