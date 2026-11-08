# Contributing

Thanks for helping. Issues and pull requests are welcome.

## Setup

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

You do not need a camera or API keys for the tests: hardware and every HTTP service are faked. To try a change for real, `uv run bbeye run --show` opens a preview window.

## Before you open a pull request

* Add or update tests for the behavior you changed.
* Keep the privacy defaults: no stored images, no frames sent anywhere unless a setting says so, no analytics.
* Keep each module to the job listed in [docs/architecture.md](docs/architecture.md), and update that file if ownership moves.
* New dependencies need a reason in the pull request and a license compatible with AGPL-3.0.
* New model files need a pinned URL and a SHA-256 hash in `vision/models.py`.

By contributing you agree that your contribution is licensed under the AGPL-3.0-or-later.
