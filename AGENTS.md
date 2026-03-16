# Repository Guidelines

## Project Structure & Module Organization

Core application code lives in `app/`:
- `app/main.py`: FastAPI entrypoint, API routes, orchestrator loop, lifespan.
- `app/model_state.py`: Model registry, settings persistence, projector helpers.
- `app/runtime_state.py`: Runtime config, dual-runtime slot discovery, system metrics, power calibration.
- `app/repositories/`: Backend abstraction (real llama.cpp proxy and fake backend).
- `app/constants/`: Model family detection, projector repo mapping.
- `app/assets/`: Frontend — `chat.html`, `chat.css`, `chat.js` (single-file UI), vendor libs.

Operational scripts are in `bin/`:
- `run.sh`: Main entrypoint (systemd calls this).
- `start_llama.sh`: Launches llama-server with correct flags, auto-downloads mmproj.
- `install_dev.sh`: Deploys to Pi (idempotent). Uses `POTATO_LLAMA_RUNTIME_FAMILY` for slot selection.
- `build_llama_runtime.sh`: Builds ik_llama or upstream llama.cpp on Pi from source.
- `prepare_imager_bundle.sh`: Packages SD card image payload.
- `ensure_model.sh`: Bootstrap model download helper.

Service definitions in `systemd/`. Nginx config in `nginx/`. Image build in `image/`.

Tests are organized under `tests/`:
- `tests/api/`: HTTP/API behavior tests (pytest).
- `tests/unit/`: Python and shell-script logic tests (pytest).
- `tests/ui/`: Playwright E2E tests — split into 8 feature-scoped spec files:
  - `settings.spec.js`, `chat.spec.js`, `image.spec.js`, `download.spec.js`
  - `runtime.spec.js`, `model-switcher.spec.js`, `sessions.spec.js`, `bootstrap.spec.js`
- `tests/ui/helpers.js`: Shared test utilities (`waitUntilReady`, `makeStatusPayload`, etc.).
- `tests/e2e/`: Pi smoke/uninstall flows over SSH.

## Build, Test, and Development Commands

### Local development
- `uv sync`: Create/sync local dev environment.
- `POTATO_ENABLE_ORCHESTRATOR=0 uv run uvicorn app.main:app --host 0.0.0.0 --port 1983`: Run API locally.

### Running tests (required before every push)
- Python (unit + API): `uv run python -m pytest tests/unit tests/api -q`
- Playwright (UI): `npx playwright test --reporter=dot --timeout=15000 --workers=3`
- Both together: `uv run python -m pytest tests/unit tests/api -q && npx playwright test --reporter=dot --timeout=15000 --workers=3`
- First-time Playwright setup: `npx playwright install chromium`

### Pi deployment
- Fast asset deploy: `sshpass -e rsync -az --delete --rsync-path="sudo rsync" -e "ssh -o StrictHostKeyChecking=accept-new" app/ pi@potato.local:/opt/potato/app/`
- Full install: `sshpass -e ssh pi@potato.local "cd /tmp/potato-os && sudo ./bin/install_dev.sh"`
- Restart service: `sshpass -e ssh pi@potato.local "sudo systemctl restart potato"`

### Image building
- `./bin/prepare_imager_bundle.sh`: Package SD card image payload.
- `./image/build-all.sh`: Full image build (requires pi-gen / Docker).

## Runtime Architecture

### Dual-runtime system
Potato OS supports two curated llama runtimes:
- **ik_llama** (default): IQK-optimized fork, faster on Pi 5.
- **llama_cpp**: Upstream llama.cpp, standard build.

Runtime slots live at `/opt/potato/runtimes/{ik_llama,llama_cpp}/` with `runtime.json` metadata. The active runtime is rsynced to `/opt/potato/llama/`. Switch via `POST /internal/llama-runtime/switch` with `{"family": "ik_llama"}`.

### Bootstrap auto-download
On fresh installs with no model, a 5-minute countdown triggers automatic download of `Qwen3.5-2B-Q4_K_M.gguf` (~1.8GB) + its F16 vision projector from Unsloth/HuggingFace. One-off: never runs again after completion or cancellation.

### Multi-chat sessions
Chat history persisted in browser IndexedDB (`potato_sessions` database). Sessions survive page reload. Image data URLs stripped before persistence; sanitized on restore so backend never receives `[stripped]` references.

## Real Pi QA

Use Chrome MCP for browser-based QA on `http://potato.local`. Test on real hardware before merging Pi-impacting changes.

Health check: `curl -s http://potato.local/status | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['state'])"`

## Coding Style & Naming Conventions

- Python 3.11+, 4-space indentation, type hints for new/changed code.
- Small testable functions, explicit error paths.
- `snake_case` for functions/files, `PascalCase` for classes.
- Env vars prefixed with `POTATO_` (e.g. `POTATO_LLAMA_RUNTIME_FAMILY`).
- Shell scripts: `set -euo pipefail`, POSIX/Bash-safe, explicit side effects.
- Frontend: Vanilla JS (no frameworks, no build step). Single `chat.js` loaded via `<script>` tag.

## Testing Guidelines

- TDD-first for all feature tickets.
- Playwright tests split by feature (8 spec files in `tests/ui/`).
- Python tests: `pytest` with `tests/unit/` and `tests/api/`.
- Name test files `test_*.py` (Python) or `*.spec.js` (Playwright).
- For Qwen3.5 models: only F16 projectors. Vision detection matches `*qwen*3.5*`.

## Commit & Pull Request Guidelines

Concise imperative commits:
- `feat(api): add backend mode fallback`
- `fix(chat): preserve multi-turn history`
- `chore(runtime): standardize bundle layout`
- `test(ui): add model switcher specs`

PRs must include:
- What changed and why.
- `Closes #<id>` or `Refs #<id>` for issue linkage.
- Test evidence (pytest + Playwright output).
- Pi QA results for Pi-impacting changes.
- No AI attribution lines.

## Branching & Workflow

- `main` is merge-only. No direct commits for feature/bug/chore work.
- Branch from `main`: `feat/issue-<id>-<slug>`, `fix/issue-<id>-<slug>`, `chore/issue-<id>-<slug>`.
- Board flow: Todo → In Progress → In Review → QA → Done.
- Squash merge preferred. Delete branch after merge.
- See `WORKFLOW.md` for full lifecycle details.

## Security & Configuration

Never commit secrets, SSH keys, or device-specific credentials. Keep credentials in environment variables. Confirm `.gitignore` excludes local-only artifacts (`references/`, `output/`, `.venv/`, `node_modules/`).
