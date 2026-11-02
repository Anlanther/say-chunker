.PHONY: install test lint typecheck format check run fixtures

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy --strict say_chunker tests

format:
	uv run ruff format .

check: lint typecheck test

run:
	uv run tts --help

fixtures:
	uv run python tests/fixtures/build_fixtures.py
