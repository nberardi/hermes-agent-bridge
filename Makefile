.PHONY: lint format format-check typecheck test ci

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

ci: lint format-check typecheck test
