# Potato OS Workflow Guide

This is the single source of truth for how we plan, execute, and close work.

## Project Board

- Board: <https://github.com/users/slomin/projects/8>
- Default status flow:
  - `Todo`
  - `In Progress`
  - `In Review`
  - `Done`

## Labels

- Type: `type:feature`, `type:bug`, `type:chore`
- Area: `area:ui`, `area:backend`, `area:pi-image`, `area:ops`
- State: `blocked` (use when waiting on an external dependency)

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
8. Move to `In Review` when PR is ready.
9. Merge PR (squash preferred).
10. Close issue and set project item to `Done`.

## Ticket Quality Standard (Required)

Every implementation ticket must include:
- clear Summary
- explicit Scope
- measurable Acceptance criteria
- explicit Non-goals
- TDD-first requirement
- Test expectations by layer (unit/API/UI as applicable)

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

## PR Readiness Checklist

Before moving `In Progress` -> `In Review`, PR description must include:
- `Closes #<issue-id>` (or equivalent linked issue statement)
- status/risk notes and rollback guidance
- exact commands run
- summarized test output for unit/API/UI layers touched
- any workflow/runbook changes made from lessons learned

## Real Pi Validation (Required For Pi-Impacting Work)

If a ticket changes runtime behavior on device (API behavior, model orchestration, install scripts, nginx/systemd, or UI behavior tied to live backend), PRs must include at least one real-device E2E run.

- Run E2E against device host with `PI_HOST` (prefer mDNS such as `potato.local`; avoid hardcoding personal IPs in docs/PR text).
- Include command + result summary in PR:
  - `./tests/e2e/smoke_pi.sh`
  - and ticket-specific E2E script (example: `./tests/e2e/seed_mode_pi.sh` for seed mode)
- Do not mark `In Review` until real-device evidence is attached, unless explicitly labeled `blocked`.

## Post-Merge Closeout

- verify the project item moved to `Done`
- verify issue is closed by merge
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

## Helpful CLI snippets

```bash
# Create issue
gh issue create -R slomin/potato-os --title "<title>" --body-file <file.md> --label "type:feature" --label "area:backend"

# Add issue to project
gh project item-add 8 --owner slomin --url https://github.com/slomin/potato-os/issues/<id>

# Move item status (Todo option id currently: 0ae661da)
gh project item-edit --id <item-id> --project-id PVT_kwHOABrb5c4BP912 --field-id PVTSSF_lAHOABrb5c4BP912zg-OMzk --single-select-option-id <option-id>
```
