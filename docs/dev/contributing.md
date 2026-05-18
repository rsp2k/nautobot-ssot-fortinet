# Contributing to the App

## Where to start

- **Bug reports**: open an issue at https://github.com/rsp2k/nautobot-ssot-fortinet/issues. Include the FortiOS version, the Nautobot version, and (if relevant) the redacted output of the failing Job.
- **Feature requests**: same place. Easier for me to evaluate if you describe the operational scenario rather than just the requested feature.
- **Pull requests**: see below.

## Pull request workflow

1. Fork the repo + create a feature branch (`git checkout -b feat/your-feature`).
2. Set up the dev environment per [`dev_environment.md`](dev_environment.md).
3. Make your changes; add tests for any new behavior. The bar is "the existing test suite + the new tests still pass on `pytest -q` in under 1 second."
4. Run `ruff check src/ tests/` and `ruff format src/ tests/` — both must come up clean.
5. Update the relevant docs page (`docs/admin/*` or `docs/user/*` or `docs/dev/*`) for any operator- or developer-facing change.
6. Commit with a descriptive message. Format follows the existing repo style — no `Co-authored-by` lines for AI assistants.
7. Push your branch and open a **draft PR** initially. Once you've self-reviewed, mark it ready for review.

## Code style

- Python 3.10+ type hints throughout.
- Module docstrings explain *what the file is for* and *what design choices it reflects*, not just *what it does*. The audience is a future maintainer (possibly future-you).
- Function docstrings include doctest examples where the input/output relationship is non-obvious. Doctest output is verified by reading; we don't currently run them.
- Pure functions in `utils/`; I/O in `clients/` and `diffsync/adapters/`; data shapes in `diffsync/models/`.

## Adding a new FortiOS mapping rule

When you discover a FortiOS field that we don't yet handle correctly:

1. Add a unit test in `tests/test_utils_fortios.py` that reproduces the failing translation. The test should fail on `main`.
2. Add the mapping helper or extend the existing one in `src/nautobot_ssot_fortinet/utils/fortios.py`.
3. Update the relevant table in `docs/user/external_interactions.md`. **This is required** — that doc is the operational reference for what FortiOS shapes the integration handles.
4. Run the live e2e harnesses if you have a FortiGate to test against, otherwise note in the PR that fixture coverage only.

## Adding push support for a new object kind

See [`extending.md`](extending.md). The push direction has more edge cases than pull (every transformation needs a clean inverse, every FortiOS payload field needs to be specified exactly right). Always test with `dry_run=True` first.

## Release process

Maintainer-only:

1. Bump the version in `pyproject.toml` to today's date (`YYYY.MM.DD`).
2. Add a new section to `CHANGELOG.md` summarizing the changes.
3. Add a corresponding file under `docs/admin/release_notes/version_<YYYY.MM.DD>.md`.
4. Run the two-stage PII audit per the repo's CLAUDE.md instructions.
5. `git commit -am "Release vYYYY.MM.DD"` + `git tag vYYYY.MM.DD` + `git push --follow-tags`.
6. `rm -rf dist/ && uv build && uv publish --token $PYPI_TOKEN`.
7. Create a GitHub Release pointing at the tag, linking to the changelog section.

## Code of conduct

Standard professional norms. Be kind. Assume good faith. If something is unclear, ask before being snippy about it.
