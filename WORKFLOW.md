# Potato OS Workflow Guide

This is the single source of truth for how we plan, execute, and close work.

## Project Board

- Board: <https://github.com/users/slomin/projects/8>
- Default status flow:
  - `Todo`
  - `In Progress`
  - `In Review`
  - `QA`
  - `Done`

## Labels

- Type: `type:feature`, `type:bug`, `type:chore`
- Area: `area:ui`, `area:backend`, `area:pi-image`, `area:ops`
- State: `blocked` (use when waiting on an external dependency)

## Work Intake (Required)

Before starting new implementation work, first check whether a relevant issue already exists in the GitHub issue tracker and `Potato OS` project.

- If a matching ticket already exists, continue work under that ticket instead of creating a duplicate.
- If no suitable ticket exists, create one before starting branch work.
- Use the existing ticket state and scope as the source of truth for what should be built next.

## Ticket Lifecycle

1. Create an issue (succinct and outcome-focused).
2. Add labels (`type:*` + `area:*`).
3. Add issue to the `Potato OS` project and set `Status=Todo`.
4. Assign issue owner before any branch work starts.
5. Create branch from `main` using:
   - `feat/issue-<id>-<short-slug>` for features
   - `fix/issue-<id>-<short-slug>` for bugs
6. Move issue to `In Progress` once branch is created.
7. Open PR linked to the issue.
8. Move to `In Review` when implementation is complete:
   - the PR is open,
   - required checks are green or expected to run.
9. After review is approved, move to `QA` for Pi validation (if Pi-impacting).
10. Merge PR (squash preferred) only after review and QA are both complete.
11. Let GitHub close the issue via the PR when possible, then set the project item to `Done`.
12. Delete branch locally/remotely and return local checkout to `main`.

## Branching Rules (Required)

- Never implement ticket work on `main`.
- `main` is merge-only; no direct commits for feature/bug/chore work.
- Before writing code, confirm branch: `git branch --show-current`.
- Required branch naming: `feat/issue-<id>-<short-slug>`, `fix/issue-<id>-<short-slug>`, `chore/issue-<id>-<short-slug>`.
- Start every ticket branch from latest `main`: `git checkout main && git pull --ff-only && git checkout -b <branch-name>`.

## GitHub PR Linkage Rules (Required)

- Every implementation PR must link to its primary issue in the PR body.
- Use `Closes #<id>` only for issues that the PR fully resolves and should auto-close on merge.
- Use `Refs #<id>` for related issues, partial work, follow-ups, or anything that should remain open after merge.
- If one PR fully resolves multiple issues, include multiple `Closes #<id>` lines.
- Do not manually close an issue while its closing PR is still open unless the issue is being abandoned or replaced; if that happens, leave a comment explaining why.
- Do not move a project item to `Done` while its PR is still open.
- Keep the issue open and the board item at `In Review` or `QA` until the PR is actually merged.
- If an issue is closed outside the normal PR merge flow, update the board item immediately so closed issues never remain in `Todo`, `In Review`, or `QA`.

## Ticket Quality Standard (Required)

Every implementation ticket must include:
- clear Summary
- explicit Scope
- measurable Acceptance criteria
- explicit Non-goals
- TDD-first requirement
- Test expectations by layer (unit/API/UI as applicable)
- general, readable language focused on the product outcome

## TDD Rule

All feature tickets are implemented TDD-first:
1. Write failing tests first.
2. Implement minimal code to pass.
3. Refactor with tests green.
4. Include test commands/output in PR description.
5. Keep commit history readable:
   - tests-first commit (or clearly isolated test diff),
   - implementation commit,
   - docs/runbook updates commit.

## Local Dev Environment Rule

For all local development work, always use `uv`.

- Use `uv` to create/sync/run the local development environment instead of ad hoc `.venv/bin/python ...` or direct `pip install ...` commands.
- Keep local development and test dependencies declared in repo-managed dependency files; do not rely on one-off manual installs outside tracked files.
- This rule is for local machine workflows only. Raspberry Pi/runtime packaging may follow different operational constraints and is not changed by this rule.

## Running Tests Locally (Required Before Push)

All tests **must** pass locally before pushing. Do not rely on CI to catch failures — run the same test commands CI uses.

### Python tests (unit + API)

```bash
uv run python -m pytest tests/unit tests/api -q -n auto
```

The `-m pytest` flag is required so Python adds the project root to `sys.path`, matching CI behavior. Uses `pytest-xdist` for parallel execution (~10s).

### Playwright UI tests

**Full suite (fast, parallel):**
```bash
npx playwright test --reporter=dot --timeout=15000 --workers=3 2>&1
```

**Single spec file (for iterating on changes):**
```bash
npx playwright test tests/ui/bootstrap.spec.js --timeout=15000 2>&1
```

**Single test by name:**
```bash
npx playwright test --grep "download prompt" --timeout=15000 2>&1
```

Requires Chromium installed (`npx playwright install chromium` on first run). The test server starts automatically via `playwright.config.js`.

### Important: test output and timeouts

- **Never pipe test output through `tail`, `grep`, or `head`** — it buffers and hides results until the command finishes, making it look like the tests are hanging.
- **Always use `2>&1`** at the end to capture stderr (where Playwright writes progress).
- **Use `--timeout=15000`** (15s per test) instead of the default 45s — most tests finish in <5s, so this catches hangs 3x faster.
- **Kill stale processes** before re-running if tests hang: `pkill -f "uvicorn|node|playwright" 2>/dev/null; sleep 2`
- **Run per-spec-file** when iterating — faster feedback than running all 56 tests.

### Running both before push

```bash
uv run python -m pytest tests/unit tests/api -q -n auto && npx playwright test --reporter=dot --timeout=15000 --workers=3 2>&1
```

If either suite fails, fix the issue before pushing. Include summarized test output in the PR description.

## PR Readiness Checklist

Before moving to `In Review`, PR description must include:
- `Closes #<issue-id>` (or equivalent linked issue statement)
- `Refs #<issue-id>` for any related issues that are not meant to auto-close
- status/risk notes and rollback guidance
- exact commands run
- summarized test output for unit/API/UI layers touched
- any workflow/runbook changes made from lessons learned

PRs must also satisfy these GitHub process rules before `In Review`:
- the PR must exist and target the correct base branch
- required GitHub checks must be running or already green
- the linked project item must not be moved to `Done` yet

## Real Pi QA (Required For Pi-Impacting Work)

If a ticket changes runtime behavior on device (API behavior, model orchestration, install scripts, nginx/systemd, or UI behavior tied to live backend), PRs must include QA on a real Pi.

- QA is the gate: do not merge until QA is completed, unless explicitly labeled `blocked`.
- PR description must include:
  - who performed QA,
  - device/host used (prefer `potato.local`; avoid personal IPs in docs/PR text),
  - short scenario list and pass/fail result.
- Automated Pi scripts are supporting evidence only (optional but recommended), for example:
  - `./tests/e2e/smoke_pi.sh`
  - `./tests/e2e/seed_mode_pi.sh`

## Post-Merge Closeout

- verify the PR is actually `MERGED`
- verify the project item moved to `Done`
- verify the issue is closed by merge, or close it manually with a note if the PR used `Refs` instead of `Closes`
- verify closed issues are not still sitting in `Todo`, `In Review`, or `QA`
- verify the remote branch is deleted
- verify local repo is back on `main` and synced with `origin/main`
- capture any process improvements in `WORKFLOW.md` in the same change set (when applicable)

## Starter Issue Template

Use this structure for new implementation tickets:

```md
### Summary
<1-2 lines>

### Implementation requirement
This ticket must be developed **TDD-first**.

### Scope
- ...

### Acceptance criteria
- ...

### Test expectations (required)
- Unit:
- API:
- UI/e2e:

### Non-goals
- ...
```

Keep ticket text general and readable. Capture the outcome and boundaries clearly, but avoid turning the issue body into a temporary debugging log or implementation transcript.

## Building ik_llama.cpp on Raspberry Pi 5

When the bundled `llama-server` needs updating (new ik_llama features, bug fixes), rebuild on the Pi:

1. **Update ik_llama source** on your dev machine:
   ```bash
   cd references/ik_llama.cpp
   git fetch origin main && git checkout <target-commit>
   ```

2. **Sync source to Pi** (exclude .git to save bandwidth):
   ```bash
   sshpass -e rsync -az --delete --exclude '.git' \
     references/ik_llama.cpp/ pi@potato.local:/tmp/ik_llama_cpp/
   ```

3. **Build on Pi** with the known-working cmake flags:
   ```bash
   ssh pi@potato.local "sudo bash -c '
   rm -rf /tmp/potato-llama-build-manual &&
   mkdir -p /tmp/potato-llama-build-manual &&
   cmake -S /tmp/ik_llama_cpp -B /tmp/potato-llama-build-manual \
     -DCMAKE_BUILD_TYPE=Release \
     -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS \
     -DGGML_OPENMP=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TOOLS=ON \
     -DGGML_VULKAN=OFF -DGGML_NATIVE=ON -DGGML_CPU_KLEIDIAI=OFF \
     -DGGML_LTO=ON -DGGML_IQK_FA_ALL_QUANTS=ON \
     -DCMAKE_C_FLAGS=\"-fno-strict-aliasing -mcpu=native\" \
     -DCMAKE_CXX_FLAGS=\"-fno-strict-aliasing -mcpu=native\" &&
   cmake --build /tmp/potato-llama-build-manual --config Release -j4
   '"
   ```

   Build takes ~15 minutes on Pi 5. Key flags:
   - `GGML_CPU_KLEIDIAI=OFF` — required for GCC 14 on Pi
   - `GGML_IQK_FA_ALL_QUANTS=ON` — enables all IQK flash-attention quant types
   - `-fno-strict-aliasing` — prevents aliasing-related miscompilation

4. **Package as bundle** using `bin/build_llama_bundle_pi5.sh` or manually copy `bin/llama-server`, `bin/llama-bench`, and `lib/*.so` into a `llama_server_bundle_<timestamp>_<profile>/` directory under `references/old_reference_design/llama_cpp_binary/`.

5. **Deploy** via `install_dev.sh` with `POTATO_LLAMA_BUNDLE_SRC=<bundle-path>`.

## Helpful CLI snippets

```bash
# Create issue
gh issue create -R slomin/potato-os --title "<title>" --body-file <file.md> --label "type:feature" --label "area:backend"

# Add issue to project
gh project item-add 8 --owner slomin --url https://github.com/slomin/potato-os/issues/<id>

# Show current status option IDs before editing item status
gh project field-list 8 --owner slomin --format json | jq -r '.fields[] | select(.name=="Status") | .options[] | "\(.name): \(.id)"'

# Move item status using the current option ID (Todo/In Progress/QA/In Review/Done)
gh project item-edit --id <item-id> --project-id PVT_kwHOABrb5c4BP912 --field-id PVTSSF_lAHOABrb5c4BP912zg-OMzk --single-select-option-id <current-option-id>
```
