# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `app/`:
- `app/main.py`: FastAPI entrypoint and API routes.
- `app/repositories/`: backend abstraction (real llama.cpp and fake backend).

Operational scripts are in `bin/` (`install_dev.sh`, `run.sh`, `start_llama.sh`, `uninstall_dev.sh`). Service definitions are in `systemd/`. Tests are organized under `tests/`:
- `tests/api/` for HTTP/API behavior.
- `tests/unit/` for Python and shell-script logic.
- `tests/e2e/` for Pi smoke/uninstall flows over SSH.

Reference material and image artifacts are in `references/` and `raspberry_os_clean_image/`.

## Build, Test, and Development Commands
- `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`: create local dev env.
- `.venv/bin/python -m pytest -q`: run all tests.
- `POTATO_ENABLE_ORCHESTRATOR=0 .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 1983`: run API locally.
- `./bin/install_dev.sh`: install/update services on Pi (idempotent).
- `./bin/build_local_image.sh --variant lite`: build local Pi image bundle and generate Imager manifest.
  - Script prompts before deleting previous `output/images` artifacts (`ask|yes|no` via `--clean-artifacts`).
  - Each generated `local-test-*` folder includes `README.md` with Raspberry Pi Imager import steps.
- `./tests/e2e/smoke_pi.sh`: run remote smoke checks (`pi/raspberry` by default).

## Coding Style & Naming Conventions
Use Python 3.11+ style with 4-space indentation and type hints for new/changed code. Prefer small, testable functions and explicit error paths. Use `snake_case` for functions/files, `PascalCase` for classes, and clear env var names with `POTATO_` prefix (for example `POTATO_CHAT_BACKEND`).

Shell scripts should be POSIX/Bash-safe, include `set -euo pipefail` where appropriate, and keep side effects explicit.

## Testing Guidelines
Use `pytest` for all test layers. Name files `test_*.py` and keep scenario-focused names (`test_fake_backend.py`, `test_shell_scripts.py`). For new behavior:
- add/adjust unit tests first (TDD default),
- add API tests for contract changes,
- run E2E smoke only when Pi-impacting code changes.

## Commit & Pull Request Guidelines
Current history is minimal (`Initial commit`), so use concise imperative commits going forward, e.g.:
- `feat(api): add backend mode fallback`
- `test(scripts): cover uninstall guardrails`

PRs should include:
- what changed and why,
- risk/rollback notes (especially Pi-side effects),
- test evidence (`pytest` output and any `smoke_pi.sh` result),
- linked issue/task when available.

## Branching Strategy
Use a lightweight branch model (not GitFlow):
- `main` is always releasable; avoid direct pushes.
- Create short-lived branches from `main`:
  - `feat/<short-name>` for features
  - `fix/<short-name>` for bug fixes
  - `chore/<short-name>` for maintenance/docs/tooling
- Keep PRs focused and small; prefer squash merge into `main`.
- For urgent production fixes, create `fix/<urgent-name>` from latest `main`, validate quickly, and merge as priority.
- After merge, delete the branch locally/remotely to keep branch list clean.

## Security & Configuration Notes
Never commit secrets, SSH private keys, or device-specific credentials. Keep credentials in environment variables and confirm `.gitignore` excludes local-only artifacts.
For Raspberry Pi Imager, always use the generated `.rpi-imager-manifest` file (Pi 5-only tag `pi5-64bit`), not internal metadata files like `METADATA.json` or `potato-*-build-info.json`.
