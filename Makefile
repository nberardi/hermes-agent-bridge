.PHONY: install lint format format-check typecheck test shellcheck ci

install:
	python3 -m pip install -r requirements-dev.txt
	python3 -m pip install -e .

lint:
	ruff check src tests

format:
	ruff format src tests

format-check:
	ruff format --check src tests

typecheck:
	mypy src

test:
	pytest -q

shellcheck:
	shellcheck install.sh

ci: lint format-check typecheck test shellcheck
