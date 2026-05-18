# Top-level convenience wrapper. Real targets live in development/Makefile.
.PHONY: help build up down restart logs ps shell nbshell seed clean test lint

help:
	@$(MAKE) -C development help

build up down restart logs ps shell nbshell seed clean:
	$(MAKE) -C development $@

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
