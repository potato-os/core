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
4. Move to `In Progress` when coding starts.
5. Open PR linked to the issue.
6. Move to `In Review` when PR is ready.
7. Merge PR (squash preferred).
8. Close issue and set project item to `Done`.

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
