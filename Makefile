.PHONY: install test lint run typecheck

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check

typecheck:
	uv run mypy --strict say_chunker

run:
	uv run tts --help
