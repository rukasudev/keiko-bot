VENV = .venv
PYTHON = $(VENV)/bin/python3
PIP = $(VENV)/bin/pip

define ACTIVATE_VENV
	. $(VENV)/bin/activate
endef
export ACTIVATE_VENV

setup: requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install -r requirements.txt
	$(ACTIVATE_VENV) && echo "Virtual environment activated"

run: docker-up migrate
	$(ACTIVATE_VENV) && $(PYTHON) __main__.py

migrate: docker-up
	$(ACTIVATE_VENV) && $(PYTHON) -m app.migrations migrate

DOCKER_COMPOSE := $(shell command -v docker-compose 2>/dev/null || echo "docker compose")

docker-up:
	docker ps | grep -q "redis-server" || $(DOCKER_COMPOSE) up -d redis
	docker ps | grep -q "mongo-server" || $(DOCKER_COMPOSE) up -d --wait mongodb

test:
	$(ACTIVATE_VENV) && python -m pytest tests/ app/ -x -q

.PHONY: logs repair-birthdays soak command docs yaml-docs

logs:
	$(ACTIVATE_VENV) && python -m scripts.keiko logs $(ARGS)

repair-birthdays:
	$(ACTIVATE_VENV) && python -m scripts.keiko birthdays repair $(ARGS)

soak:
	$(ACTIVATE_VENV) && python -m scripts.keiko soak $(SOAK_ARGS)

command:
	$(ACTIVATE_VENV) && python -m scripts.keiko.command $(name) $(if $(filter yes,$(form)),--form)

docs:
	$(ACTIVATE_VENV) && python -m scripts.keiko docs $(ARGS)

yaml-docs: docs

lint:
	$(ACTIVATE_VENV) && ruff check app/data app/forms app/core/calendar.py app/core/commands.py app/core/conditions.py app/core/facts.py app/core/tokens.py app/core/dispatch.py app/core/i18n.py app/core/copy_schema.py app/core/groups.py app/core/mentions.py app/commands app/decorators.py app/types/cogs.py app/ui/pagination.py app/ui/embed.py app/ui/records.py app/migrations tests/forms tests/core && ruff check --select F401,F821 app && ruff format --check app/data app/forms app/core/calendar.py app/core/commands.py app/core/conditions.py app/core/facts.py app/core/tokens.py app/core/dispatch.py app/core/i18n.py app/core/copy_schema.py app/core/groups.py app/core/mentions.py app/commands app/decorators.py app/types/cogs.py app/ui/pagination.py app/ui/embed.py app/ui/records.py app/migrations tests/forms tests/core && mypy

clean:
	rm -rf __pycache__
	rm -rf $(VENV)
