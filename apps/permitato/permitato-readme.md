# Permitato Milestone Plan

Productize Permitato from a working pilot into a durable daily-driver attention guard.
Epic: #219 | Milestone: [Permitato](https://github.com/slomin/potato-os/milestone/8)

## Execution Phases

### Phase 1: Foundation

These two tickets fix the ground everything else builds on. Sequential — #222 uses the hardened persistence from #221.

| Order | Ticket | Summary | Key files |
|-------|--------|---------|-----------|
| 1 | **#221** | Harden Pi-hole recovery, persistence, exception consistency | `state.py`, `exceptions.py`, `audit.py`, `lifecycle.py`, `pihole_adapter.py` |
| 2 | **#222** | Install and controlled-client onboarding flow | `routes.py`, `state.py`, `assets/*`, new onboarding UI |

**#221 in detail** (first ticket):
- Atomic writes for `state.json` and `exceptions.json` (write-tmp + `os.replace`)
- Pi-hole reconnection loop in `lifecycle.py` (60s interval, re-seeds groups on recovery)
- Exception-to-Pi-hole compensation after reconnection (needs new `get_domain_rules()` on adapter)
- Audit log rotation in `audit.py` (keep last N lines, atomic rewrite)
- Hardened domain validation in `exceptions.py`
- `degraded_since` timestamp on `PermitState` surfaced in `/status`

**#222 in detail:**
- Client discovery endpoint using existing `adapter.get_clients()`
- First-run onboarding modal when `client_id` is empty
- Selected-client validation on startup (check it still exists in Pi-hole)
- Recovery flow when saved client disappears

### Phase 2: UX Polish

These two are independent — can run in parallel or either order.

| Order | Ticket | Summary | Key files |
|-------|--------|---------|-----------|
| 3 | **#223** | Active exceptions panel with TTL and revoke controls | `assets/permitato.html`, `assets/permitato.js`, `assets/permitato.css` |
| 4 | **#226** | User-defined domain lists per mode | `modes.py`, `state.py`, `routes.py`, new custom-lists UI |

### Phase 3: Intelligence

Both read from `audit.jsonl` — independent of each other but benefit from #221's rotation.

| Order | Ticket | Summary | Key files |
|-------|--------|---------|-----------|
| 5 | **#224** | Smart unblock negotiation with recent audit context | `system_prompt.py`, `audit.py` (backend only) |
| 6 | **#225** | Attention stats and streaks from audit history | New stats module, `routes.py`, `assets/*` |

### Phase 4: Orchestration

Largest feature — touches state, UI, persistence, and background tasks. Goes last so everything it integrates with is stable.

| Order | Ticket | Summary | Key files |
|-------|--------|---------|-----------|
| 7 | **#220** | Scheduled modes and manual override | New `schedule.py`, `lifecycle.py`, `state.py`, `routes.py`, schedule UI |

## Dependency Graph

```
#221 (harden)
  ├──> #222 (onboarding)
  │      ├──> #223 (exceptions panel)
  │      └──> #226 (custom domain lists)
  ├──> #224 (smart context)
  ├──> #225 (stats/streaks)
  └──────────────────────────> #220 (scheduling) ──> #219 epic done
                                                      (also needs #166 LAN auth)
```

## Recurring Patterns

- **Atomic writes**: Extract `atomic_write(path, data)` helper in #221, reuse in every ticket that persists state
- **Pi-hole adapter**: #221 adds `get_domain_rules()`, #226 reuses it for custom list management
- **UI panels**: Each feature adds a `<section>` to `permitato.html` — exceptions panel, stats panel, custom lists, schedule editor, onboarding modal
- **Audit reads**: #224 and #225 both build summaries from `audit.jsonl` — coordinate the read helpers
- **Test infra**: No API/routes tests or Playwright tests exist yet — build the fixtures in #221, reuse everywhere

## Notes

- **#166 (LAN auth)** is in Inbox, not started. It's a soft dependency for epic completion (security gate) but doesn't block any individual ticket.
- **#227** was a duplicate of #226 and has been closed.
- All tickets follow TDD-first per WORKFLOW.md.
- Each ticket gets its own branch: `feat/issue-<id>-<slug>` or `fix/issue-<id>-<slug>`.

---

> **Cleanup**: Delete this file once the Permitato milestone is complete. It's a coordination artifact, not documentation.
