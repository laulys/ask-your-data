.PHONY: help setup data run cli test lint format check mysql clean

help:
	@echo "setup   install the package and its dev/UI extras into .venv"
	@echo "data    download the Chinook sample database"
	@echo "run     start the Streamlit web app"
	@echo "cli     ask one question: make cli Q='how many albums?'"
	@echo "test    run the test suite with coverage"
	@echo "lint    ruff check + format check"
	@echo "format  reformat the code"
	@echo "check   lint and test, what CI runs"
	@echo "mysql   start MySQL in Docker and load Chinook into it"

.venv:
	python3 -m venv .venv

setup: .venv
	./.venv/bin/pip install --upgrade pip
	./.venv/bin/pip install -e ".[ui,dev]"
	@echo "Done. Next: make data"

data:
	./.venv/bin/python scripts/setup_data.py

run:
	./.venv/bin/streamlit run app/streamlit_app.py

Q ?= How many albums are there in the database?
cli:
	./.venv/bin/nlsql "$(Q)"

test:
	./.venv/bin/pytest --cov=nlsql --cov-report=term-missing

lint:
	./.venv/bin/ruff check .
	./.venv/bin/ruff format --check .

format:
	./.venv/bin/ruff format .
	./.venv/bin/ruff check --fix .

check: lint test

mysql:
	./.venv/bin/python scripts/setup_data.py --mysql
	docker compose up -d

clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov **/__pycache__
