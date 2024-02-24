VENV = venv
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

run: docker-up
	$(ACTIVATE_VENV) && $(PYTHON) __main__.py

docker-up:
	docker ps | grep -q "redis-server" || docker-compose up -d redis
	docker ps | grep -q "mongo-server" || docker-compose up -d mongodb

clean:
	rm -rf __pycache__
	rm -rf $(VENV)
