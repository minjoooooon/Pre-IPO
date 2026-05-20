# Autonomous Audit Log

자리 비운 사이 점검 진행 상황. 슬라이스별 self-review (Claude sub-agent) + codex exec 병렬 → 합쳐서 픽스 → 검증 → 커밋.

날짜: 2026-05-19 (KST)

## Slices

| # | Scope | Self-review | Codex | Synthesis | Fixes | Verify | Commit |
|---|---|---|---|---|---|---|---|
| 1 | adapters + supervisor + aggregator + normalizer + store | ✅ | ✅ | ✅ | ✅ | ✅ ruff+compile+5 smoke | ✅ `1d488f2` |
| 2 | registry + admin + calibrate + data files + models | ✅ | ✅ | ✅ | ✅ | ✅ ruff+compile+11 smoke | ✅ `48daec8` |
| 3 | rest/ws/health api + main + config + start/stop.bat | ✅ | ✅ | ✅ | ✅ | ✅ ruff+compile+33 smoke | ✅ |
| 4 | data-live.js + app.jsx + HTML | ✅ | ✅ | ✅ | ✅ | ✅ babel-parse+node-sim+47 smoke | ✅ |
| 5 | gate/bingx never-audited + post-slice drift (FE additions, start.sh, status broadcast, OI/funding units) | ✅ | ✅ | ✅ | ✅ | ✅ ruff+compile+33 BE smoke+131 FE smoke+/readyz live | ✅ |

## Slice 1 — data ingestion path

**Self-review** (Claude sub-agent): 5 CRITICAL, 15 IMPORTANT, 14 MINOR new issues. Verdict: `acceptable-with-fixes`.
**Codex review**: 1 CRITICAL, 9 IMPORTANT, 3 MINOR. Verdict: `unsafe`.

### Overlap (both flagged)
- OKX heartbeat silent exit → reader stuck on dead socket (Claude IMPORTANT okx.py:232, codex CRITICAL okx.py:226)
- `cleanup_older_than` is dead code → unbounded DB growth (both CRITICAL)
- normalizer divide-by-zero on `share_ratio == 0.0` (both CRITICAL)
- `INSERT OR IGNORE` silently drops duplicate-ts rows (both IMPORTANT)
- `_pending_flush` unbounded if SQLite keeps failing (both IMPORTANT)
- OKX bid/ask flap to None on partial tickers frames (both IMPORTANT)
- OKX `last or state.last_price` masks legitimate 0.0 (both IMPORTANT/MINOR)

### Applied fixes
| ID | File | Change |
|---|---|---|
| F1 | okx.py | clean WS close → backoff (not hot-spin reconnect) |
| F2 | supervisor.py | reset reconnect backoff on healthy tick |
| F3 | aggregator.py + config.py | schedule `cleanup_older_than` every 6h via `_retention_loop` |
| F4 | normalizer.py | guard `share_ratio` falsy / non-finite |
| F5 | okx.py | heartbeat send failure closes WS (forces reconnect) |
| F6 | store.py | `asyncio.Lock` around all writes; cursor-based rowcount; coerce db_path to str |
| F7 | aggregator.py + config.py | bound `_pending_flush` with `pending_flush_max` (drop-oldest on overflow) |
| F8 | okx.py | bid/ask only overwrite when parsed value is not None |
| F9 | okx.py | `last` only overwrite when not None (drops `or`-falsy bug) |
| F10 | okx.py | reset `_ChannelState` per `_connect_once` |
| F11 | okx.py | `_to_float` rejects NaN/Inf |
| F12 | okx.py | raise on `event=="error"` (subscribe rejection) instead of silently idling |
| F13 | normalizer.py | replace hardcoded 60s with `settings.stale_threshold_seconds` |
| F14 | supervisor.py | preserve `last_error` across status resets |
| F15 | supervisor.py | track `connected_at`; never-ticked adapter can go stale |
| F16 | aggregator.py + config.py | first flush after `tick_batch_first_flush_seconds` (5s) instead of full 60s |
| F17 | supervisor.py | clean stream-return path: backoff + state="reconnecting", separate log event |
| F18 | okx.py | use `settings.okx_heartbeat_interval_seconds` (20s per OKX spec) |
| F19 | normalizer.py | future-skewed exchange ts → stale (rather than masquerading as fresh) |

### Verification
- `ruff check app` → All checks passed
- `python -m compileall -q app` → clean
- 5 smoke assertions (normalize share_ratio=0/NaN/future-ts/fresh + `_to_float('nan')==None`) → all pass

### Deferred (MINOR — recorded for later)
- bitget hardcoded `POLL_INTERVAL_SECONDS` → settings (slice 2 candidate)
- bitget no `stats` property like OKX (observability slice)
- `Aggregator._broadcast` declared async but never awaits
- `_stale_loop` uses wall clock (`time.time()`) — NTP jumps could defer stale by N seconds
- `snapshot_message` blocks event loop if 50 clients reconnect simultaneously
- OKX `log.warning("okx_parse_error" ...)` not rate-limited under schema-drift flood
- `websockets.connect(..., max_queue=...)` not set explicitly

## Slice 2 — calibration & admin

**Self-review** (Claude sub-agent): 5 CRITICAL, 14 IMPORTANT, 12 MINOR. Verdict: `acceptable-with-fixes`.
**Codex review**: 2 CRITICAL, 11 IMPORTANT, 3 MINOR. Verdict: `unsafe`.

### Overlap (both flagged)
- Admin endpoints unauthenticated + `CORS allow_origins=*` → browser-tab can corrupt FDV (both CRITICAL)
- `_atomic_write` lacks `fsync` + Windows-share-violation retry (both CRITICAL/MINOR)
- calibrate.py note-append idempotency broken — markets.json already showed duplicates (both CRITICAL)
- Pydantic `model_copy(update=...)` bypasses validation → bogus `ratio_source` lands on disk (both IMPORTANT)
- `allow_inf_nan=False` missing on admin float fields (both IMPORTANT)
- `ratio_source` body is free-form str instead of `RatioSource` literal (both IMPORTANT)
- Asset/Market silently drop unknown on-disk fields on round-trip (both)
- `registry.reload()` not all-or-nothing (codex IMPORTANT, addressed)
- No FK validation Market.asset_id → Asset.id (codex IMPORTANT)

### Applied fixes (slice 2)
| ID | File | Change |
|---|---|---|
| F20 | config.py | `admin_token` (env or auto-generated) + `cors_allow_origins` allow-list |
| F21 | main.py | CORS locked to allow-list; method allow-list narrowed; admin token logged at startup |
| F22 | registry.py | `safe_write_json` helper: `flush+fsync+os.replace` w/ Windows-retry backoff |
| F23 | calibrate.py | strip prior `[calibrated …]`/`[discover …]` segments before re-append; use `safe_write_json` |
| F24 | models.py | `model_config = ConfigDict(extra="allow")` on Asset and Market |
| F25 | registry.py | `update_asset_shares`/`update_market_ratio` re-validate via `model_validate`, not `model_copy` |
| F26 | registry.py | `shares_last_updated` only advances when `fully_diluted_shares` actually changes; accept optional `source_url` |
| F27 | registry.py | `reload()` all-or-nothing snapshot validation + FK check + duplicate-id check |
| F28 | admin.py | `allow_inf_nan=False` on `fully_diluted_shares` / `share_ratio` / `ratio_reference_fdv` |
| F29 | admin.py | `ratio_source` body typed as `RatioSource` literal (rejects "garbage" at parse time) |
| F30 | admin.py | `Path` regex on `asset_id` / `exchange` (log-injection / path-traversal defence) |
| F31 | admin.py | `max_length` on `source` (1024), `source_url` (2048) |
| F32 | admin.py | `require_admin_token` dependency on router; 401 without `X-Admin-Token` |
| F33 | admin.py | `broadcast_config_update` failure logged at warning (was silently `pass`) |
| F34 | registry.py | `update_market_ratio` writes tz-aware UTC `datetime` (was naive) |
| F35 | registry.py | `get_registry` lazy-init guarded by `threading.Lock` |
| F36 | registry.py | use `model_dump(mode="json")` (no double round-trip via `model_dump_json`+`json.loads`) |
| F37 | markets.json | one-shot cleanup of three rows with duplicated `[calibrated …]` segments |

### Verification
- `ruff check app scripts/calibrate.py` → All checks passed
- `python -m compileall -q app scripts` → clean
- 11 smoke assertions:
  1. registry reload works (3 assets, 6 markets)
  2. FK rejection — `asset_id=NONEXISTENT` raises
  3. duplicate `market_id` raises
  4. Asset preserves unknown `custom_field` through `model_validate` + `model_dump`
  5–7. `UpdateRatioBody` rejects `Infinity`/`-Infinity`/`NaN` for `share_ratio`
  8. `UpdateRatioBody` rejects `ratio_source="garbage"`
  9. `UpdateSharesBody` rejects negative `fully_diluted_shares`
  10. `safe_write_json` fsync + replace round-trip
  11. `shares_last_updated` stays put when shares unchanged
  12. `shares_last_updated` advances when shares change
  13. `update_market_ratio` rejects bogus `ratio_source` at registry layer

### Lessons (avoid in future slices)
- **Smoke tests must not write to tracked data files.** The slice-2 smoke
  called `update_asset_shares` against the real `assets.json` and partially
  corrupted it (shares_last_updated jump + float reformatting). I restored
  it via `git checkout`, but the smoke should have used a temp dir copy.
  Pattern for slice 3/4 smokes: copy `data/*.json` to a `TemporaryDirectory`
  and point a Registry at the copy.

### Deferred (MINOR — recorded for later, do in slice 3+)
- Supervisor doesn't re-evaluate adapter enablement after admin writes —
  changing a market from `uncalibrated` → calibrated requires a process
  restart to start the adapter. Document or add `supervisor.reconfigure()`.
- Audit log: structured JSONL row per admin write (before/after, actor,
  request-id) — currently only `ratio_calibrated_at` survives.
- `LastRound.date` is `str` with no regex; allows malformed "2025"-only.
- `Asset.reference_share_price` is stored on disk so an admin shares-edit
  leaves it stale.

## Slice 3 — API surface + process boot

**Date:** 2026-05-20 (KST).
**Status:** review + fixes complete. F38–F51 applied on the chore branch
2026-05-20 in a second pass (same Windows host — VPS migration deferred);
verified by 33 smoke assertions + ruff + compileall before the commit. The
sections below are kept as-written for historical context.

This branch already carries an unrelated step-3 boot-logging fix (`main.py` +
`start.bat`) that landed BEFORE the slice-3 review — the audit covers it, and
two of its own weaknesses (logging file-handle leak + bootstrap.err dir
assumption) are part of the fix queue below.

### Reviewer verdicts
- **Self-review** (Claude general-purpose sub-agent): 2 CRITICAL, 7 IMPORTANT, 11 MINOR. Verdict: `acceptable-with-fixes`.
- **Codex review**: 0 CRITICAL, 7 IMPORTANT, 3 MINOR. Verdict: `acceptable-with-fixes`.

Both reviewers converge on the same operational risk pattern: "the process
looks healthy while degraded." `/health` returns 200 regardless of store /
supervisor / aggregator state; WS clients can sit silently stale after a
slow-subscriber drop; startup partial-failure does not fully unwind; admin
tokens persist in the rotating log; the `start.bat` hidden window hides
exactly the early failures the new logging fix was designed to surface.

### Overlap findings (both flagged — fix priority)
- `backend/app/api/rest.py:37-44` — `/api/history/{asset_id}?seconds=…` has no upper bound. Large positive `seconds` serializes hundreds of thousands of rows into a single JSON response. Negative values silently return empty (because `cutoff > now`). (both IMPORTANT)
- `backend/app/api/ws.py:35-47` + `backend/app/aggregator.py:175-177` — slow-subscriber drop leaves `_sender` parked on a discarded queue forever; `_receiver` parked on `receive_text()`. WS connection stays "open" with zero data flowing until the client itself drops. (both IMPORTANT)
- `backend/app/main.py:30-42` — `_configure_logging()` constructs `RotatingFileHandler(...)` BEFORE checking whether one already exists. The second call (from lifespan, after the new `__main__`-block call) leaks an open file handle to `app.log`. On Windows this can lock the file, breaking log rotation and conflicting with the new `Start-Process -RedirectStandardOutput` paths. Bug introduced by this session's step-3 change. (Claude IMPORTANT, codex MINOR)
- `backend/app/api/health.py:6-11` — `/health` is a constant `{"status":"ok"}`, ignores DB, supervisor, aggregator. Same endpoint that monitoring will scrape. Sibling `/api/status` silently returns `[]` when `app.state.supervisor is None`, so neither endpoint actually reports startup failure. (both IMPORTANT)
- `backend/app/main.py:117-128` — lifespan startup runs `aggregator.start()` then `supervisor.start()` then `ws_api.attach()` then `app.state.aggregator = …`. An exception between these leaves already-started components without cleanup; the `finally` block then tries to stop components that were never assigned. (both IMPORTANT)
- `backend/app/main.py:85-86` — admin token logged at INFO into the rotating `app.log` with the raw token value. Documented as the recovery channel for the auto-generated token, but the same path also persists env-provided tokens. Log files rotate to backups, OneDrive/Dropbox sync, etc. (both IMPORTANT)
- `start.bat:32` — `-RedirectStandardError '%~dp0backend\logs\bootstrap.err'` requires the parent dir to exist; `Start-Process` will fail silently if it does not. On a fresh checkout (or first deploy) the dir does not exist yet — `_configure_logging` creates it but only AFTER python starts, which it cannot if redirection fails. Bug introduced by this session's step-3 change. (Claude MINOR, codex IMPORTANT)
- `backend/app/api/rest.py:17,25` — `asset_id` accepted as arbitrary unbounded string. Admin endpoints use `Path(pattern=...)`/`_ASSET_ID_REGEX` for the same identifier. Inconsistency. (both MINOR)
- `stop.bat:6` — PID-detection race: between `netstat` snapshot and `taskkill /F`, the PID could be reused by an unrelated process. Hard-coded `:8000` couples to one port. (both MINOR)

### Claude-unique findings
- 🔴 **CRITICAL — `backend/app/main.py:114-115`** `supervisor.disable(market.exchange, ...)` calls into `Supervisor.disable()` (supervisor.py:52-60) which iterates ALL `_SupervisedAdapter` rows with matching `adapter.exchange_id`. With three OKX markets (SPACEX/OPENAI/ANTHROPIC) all sharing `exchange_id="okx"`, a single uncalibrated row at startup blackholes the entire OKX feed for the two correctly-calibrated assets. Same hazard for `bitget`, `gate`. **Compounded second bug found by manual verification:** `HyperliquidAdapter.exchange_id = "hyperliquid"` (adapters/hyperliquid.py:89) but the markets register under `exchange="hl-xyz"` and `exchange="hl-vntl"` — so `supervisor.disable("hl-xyz", ...)` matches no adapter and is a NO-OP. An uncalibrated hyperliquid market is NOT disabled, instead runs with `share_ratio=None` and either errors per tick or emits broken normalizations. Both bugs share one fix (F38-F39 below).
- 🔴 **CRITICAL — `backend/app/api/ws.py:32-46`** `queue = await aggregator.subscribe()` raises (e.g., a `model_dump()` validation error inside `snapshot_message()`, or during aggregator shutdown) → control jumps to `finally:` at line 45 → `aggregator.unsubscribe(queue)` with `queue` unbound → `UnboundLocalError`. The original exception is masked, the client's WS is left half-open.
- IMPORTANT — `backend/app/api/admin.py:52` admin token compared with `!=` rather than `hmac.compare_digest`. Timing oracle is theoretical on loopback but real-world once the service is exposed (VPS).
- MINOR — `backend/app/main.py:163-181` static-file routes call `FileResponse(p)` without `p.resolve()` symlink-guard. An attacker who can write to project root could plant `app.jsx` as a symlink to `~/.ssh/id_rsa` etc.
- MINOR — `backend/app/config.py:18` `os.environ.get("ADMIN_TOKEN") or secrets.token_urlsafe(24)` — falsy strings (`""`, `"0"`, `"false"`) fall through to random regeneration silently.
- MINOR — `backend/app/main.py:209-210` `log_config=None` works on uvicorn 0.30-0.32 but the wider `>=0.30` constraint in `pyproject.toml` could break silently if upstream renames the `uvicorn.error` logger.

### Codex-unique findings
- 🟡 **IMPORTANT — `backend/app/api/ws.py:32`** `/ws` bypasses CORS entirely (CORS middleware does not apply to WebSocket upgrade). Any browser origin can connect, drain snapshots/status/history, and hold server resources. **Especially relevant for VPS** where the server is potentially reachable from the public internet rather than loopback only.
- IMPORTANT — `backend/app/api/ws.py:64` inbound WebSocket frames are decoded with no app-level size/rate policy. A malicious local page can send arbitrarily large text frames. Set uvicorn `ws_max_size` explicitly.
- MINOR — `backend/app/config.py:8` `Settings(BaseModel)` does not read env vars. Comments imply operator-adjustable CORS/host/port/paths, but only `ADMIN_TOKEN` is wired via the `default_factory` hack. Either rename to `pydantic-settings.BaseSettings` or document the limitation.

### Applied fixes (2026-05-20)

| ID | File(s) | Severity | Source | Change |
|---|---|---|---|---|
| F38 | backend/app/supervisor.py | CRITICAL | Claude | `disable(exchange, reason)` → `disable(market_id, reason)`; matches on `sa.market.market_id` so only the targeted market is disabled. Unknown id logs `supervisor_disable_no_match`. |
| F39 | backend/app/main.py | CRITICAL | Claude | Lifespan now calls `supervisor.disable(market.market_id, …)`. Fixes both the OKX over-disable (siblings stayed up) and the hyperliquid no-op-disable (`exchange_id="hyperliquid"` vs `market.exchange="hl-xyz"`). |
| F40 | backend/app/api/ws.py | CRITICAL | Claude | `queue` initialized to None before the try; subscribe wrapped in its own try (closes ws with 1011 on failure); finally guards `if queue is not None`. UnboundLocalError path is gone. |
| F41 | backend/app/aggregator.py + ws.py | IMPORTANT | both | `_broadcast` pushes `None` sentinel into dropped slow-subscriber queue; `_sender` treats `payload is None` as "close ws with 1013 and exit". No more zombie sockets. Subscriber type widened to `Queue[dict \| None]`. |
| F42 | backend/app/api/ws.py | IMPORTANT | codex | `_origin_allowed` checked before `ws.accept()`; rejects with `close(1008)` if origin not in `settings.cors_allow_origins`. `None` origin allowed only when "null" is listed (file:// / non-browser opt-in). |
| F43 | backend/app/config.py + main.py | IMPORTANT | codex | New `Settings.ws_max_size_bytes=1<<20`; passed to `uvicorn.run(ws_max_size=...)`. |
| F44 | backend/app/main.py | IMPORTANT | both | `_configure_logging` checks `getattr(h, "baseFilename", None) == resolve(app.log)` BEFORE constructing the handler. Second call from lifespan no longer leaks a file handle. |
| F45 | backend/app/api/rest.py + store.py + config.py | IMPORTANT | both | `seconds` clamped to `[1, history_replay_seconds]`; negative → 400. `replay_recent` accepts optional `limit`; rest handler passes `settings.history_max_rows=100_000` (newest-first then re-ordered ASC). |
| F46 | backend/app/api/rest.py | MINOR | both | `_ASSET_ID_PATTERN = "^[A-Z0-9_-]{1,32}$"` applied as `Path`/`Query` pattern on `/api/assets/{asset_id}`, `/api/markets?asset_id`, `/api/history/{asset_id}`. |
| F47 | backend/app/api/health.py | IMPORTANT | both | Added `/livez` (const 200) and `/readyz` (503 unless aggregator + supervisor exist AND at least one adapter ticked within `stale_threshold_seconds`). `/health` kept as `/livez` alias for back-compat. |
| F48 | backend/app/main.py | IMPORTANT | both | Lifespan startup wrapped in try/except; on failure each component started so far is stopped via `contextlib.suppress(Exception)` in reverse order, then exception re-raised. Shutdown path also suppresses per-component to avoid one stop error skipping siblings. |
| F49 | backend/app/main.py | IMPORTANT | both | New `_persist_admin_token()` writes the token to `backend/logs/admin_token.txt` (0o600 on POSIX; Windows ACL hardening tracked as follow-up). Startup logs only `source=env\|generated` and the path — never the raw token. |
| F50 | backend/app/api/admin.py | IMPORTANT | Claude | `require_admin_token` uses `hmac.compare_digest`; short-circuits on empty header to avoid the constant-time op on a falsy operand. |
| F51 | start.bat | IMPORTANT | codex | `if not exist "%~dp0backend\logs" mkdir "%~dp0backend\logs"` added BEFORE the `powershell ... Start-Process` line so `-RedirectStandardError`/`-Output` don't fail silently on a fresh checkout. |

**Verification (2026-05-20):**
- `python -m ruff check app` → All checks passed
- `python -m compileall -q app` → clean
- `python smoke_slice3.py` → 33 PASS assertions, ALL SMOKE TESTS PASSED. Smoke script lives at `backend/smoke_slice3.py`; runs against a `TemporaryDirectory` copy of `backend/data/` and `tempfile.TemporaryDirectory(ignore_cleanup_errors=True)` so the locked `app.log` (opened by F44 test) doesn't block teardown on Windows. Per slice-2 lesson, no tracked data files are touched.

**Deferred to slice 4 or later (MINOR — record but do not apply):**
- `rest.py:20,42` — reflected `asset_id` in `HTTPException(detail=...)` (CR/LF could land in structured logs; minor).
- `main.py:163-181` — `Path.resolve()` symlink rejection for static files (defence-in-depth; local-fs-write attacker has already won).
- `main.py:148-151` — CORS `allow_methods` allow-list narrow (documentational only).
- `main.py:209-210` — pin uvicorn range in `backend/pyproject.toml` (currently `>=0.30` — open-ended) to insulate from logger renames.
- `config.py:18` — reject empty `ADMIN_TOKEN=""` rather than silently regenerating.
- `config.py:8` — migrate `Settings` to `pydantic-settings.BaseSettings` so host/port/paths are actually env-configurable. Larger refactor — schedule after slice 4.
- `admin.py` — wrap `pydantic.ValidationError` raised inside `registry.update_*` with `HTTPException(422)` to avoid leaking Pydantic class names through the default 500 handler.
- `stop.bat` — replace netstat lookup with PID file written by `start.bat`. Race is real but low-probability on a single-operator host.
- Linux service scripts (`start.sh`, `stop.sh` or a `systemd` unit) — REQUIRED on VPS before this service can run there. Track as a follow-up alongside slice-4 work.

### Step-3 boot-logging change (included in this commit, pre-audit)

This change is BEFORE the slice-3 audit fixes; it predates them and the audit
caught two issues with it (F44 + F51). It is shipped here because the audit
needed to review it, and because the bind-failure diagnosis it enables is
genuinely useful on VPS too.

- `backend/app/main.py` —
  - `_configure_logging()` moved to the top of the `if __name__ == "__main__":` block, before `uvicorn.run`. Previously it ran inside `lifespan`, which fires AFTER uvicorn binds — so EADDRINUSE on :8000 left zero trace in `app.log`.
  - The lifespan still calls `_configure_logging()` as an idempotent fallback for non-`__main__` entry points (e.g., `uvicorn app.main:app` directly). **The idempotency guard is broken — see F44.**
  - `uvicorn.run(log_config=None, access_log=False)` so `uvicorn`, `uvicorn.error`, `uvicorn.access` loggers propagate to root, where the `RotatingFileHandler` catches them. `access_log=False` suppresses per-request lines (would flood app.log with FE polling traffic).
- `start.bat` —
  - `Start-Process` now also passes `-RedirectStandardError '%~dp0backend\logs\bootstrap.err'` and `-RedirectStandardOutput '%~dp0backend\logs\bootstrap.out'`. These capture pre-logging-configured failures (import errors, the brief window before `_configure_logging` runs in `__main__`). **Assumes the dir exists — see F51.**
- **Verification:** triggered EADDRINUSE on :8000 with the existing backend (PID 2752 at the time) still running. A second `python -m app.main` exited cleanly and `ERROR uvicorn.error [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)` was captured in `app.log`. Before the change, zero lines.

### How to resume on VPS (next session)

The full repo state needed to continue is on this branch
(`chore/spacex-market-notes-2026-05-19`). Memory files are Windows-local and
do not travel — this AUDIT_LOG section + the commit message are the
authoritative resume pointer.

1. **Pull**: `git checkout chore/spacex-market-notes-2026-05-19 && git pull`.
2. **Install**: re-create venv from `backend/pyproject.toml` (Python 3.12; deps: `fastapi`, `uvicorn`, `structlog`, `httpx`, `websockets`, `pydantic`, `aiosqlite`). Note: `pyproject.toml` constrains uvicorn `>=0.30` which is wide-open; pin if you hit a regression.
3. **Boot scripts**: `start.bat`/`stop.bat` are Windows-only and WILL NOT work on Linux. For now run directly: `cd backend && python -m app.main`. Add `start.sh` + `stop.sh` (or a `systemd` unit) as part of slice-3 fix application. The `_configure_logging` + `uvicorn log_config=None` change is platform-agnostic, so app.log capture works on Linux out of the box.
4. **Configurability**: `Settings.host = "127.0.0.1"` hard-coded. To listen on a non-loopback interface on the VPS, edit `backend/app/config.py` (the env-var path doesn't currently work — `BaseModel`, not `BaseSettings`; see F at the end of MINOR deferred list). Default `ADMIN_TOKEN` regenerates per boot — pin via environment.
5. **Apply F38–F51**: in ID order. After each fix, change its row's status in the table above from "PENDING" to applied (or replace the table with a per-row applied/deferred column). Verification: `ruff check app scripts/<files>`, `python -m compileall -q app scripts`, then a smoke-test script asserting each fix's behavior. **Per the lessons-learned from slice 2: smoke tests must NOT write to tracked data files — copy `backend/data/*.json` to a `TemporaryDirectory` before exercising the registry.**
6. **Commit**: a single "slice 3 audit: harden API + boot path" commit covering F38–F51. Push to origin.
7. **Then proceed to slice 4** (`data-live.js`, `app.jsx`, `Pre-IPO Normalizer.html`, lightly `styles.css`). Same parallel-reviewer pattern. Slice 4 should explicitly target the BE↔FE enum drift problem (Pydantic `Literal` additions need matching `STRUCT_META`/`SRC_META` keys in the FE or the whole table blanks out).

### Artifacts from this slice (not committed; regenerable)

The raw reviewer reports live in the repo root but are gitignored:
`slice_3_prompt.txt`, `claude_slice_3.md`, `codex_slice_3.md`. To re-run on
VPS: `codex exec --skip-git-repo-check -s read-only --cd "$(pwd)" -o codex_slice_3.md "$(cat slice_3_prompt.txt)"`. The Claude-side review was a
general-purpose sub-agent invocation from the parent session.

## Slice 4 — frontend live wiring

**Date:** 2026-05-20 (KST).

### Reviewer verdicts
- **Self-review** (Claude general-purpose sub-agent): 1 CRITICAL, 4 IMPORTANT, 5 MINOR. Verdict: `acceptable-with-fixes`.
- **Codex review**: 3 CRITICAL, 3 IMPORTANT, 2 MINOR. Verdict: `unsafe`.

Both reviewers independently flagged the admin-write FE as the most
operationally broken thing: every FDV / share_ratio edit issued from the
dashboard 401s because no `X-Admin-Token` header is sent (slice-3 hardened
the BE for this exact case). Codex additionally caught two failure modes
the Claude reviewer missed — `ratioSourceFromMarket` silently relabelling
new BE `RatioSource` literals to "market-fitted" while NOT removing the
stale row from LISTINGS on a config-update flip to uncalibrated, and a
chain of empty-listing dereferences (`norm.max.ex`, `anchor.ex`) that
crash the whole dashboard when no calibrated venue exists yet. Claude
caught the inverse: ~10 unguarded `STRUCT_META[r.structure]` sites that
TypeError out of NormTable/PremiumBars/etc the moment a future BE
`Structure` literal ships before the FE has a matching meta entry, and a
chain of unguarded divide-by-`refFdv` / `norm.consensus` math paths.

### Overlap findings (both flagged)
- `app.jsx:621-644` — `postShares` / `postRatio` omit `X-Admin-Token`. Admin
  UI is dead in the field. (Claude CRITICAL, codex IMPORTANT)
- `data-live.js:64-72` — `ratioSourceFromMarket` falls through to
  "market-fitted" for unknown values. (Claude IMPORTANT, codex CRITICAL)
- `app.jsx:520` (now: F60 site) — `pts[95]` hardcoded index; warm-up pts
  arrays shorter than 96 emit NaN coordinates. (both MINOR)
- `Pre-IPO Normalizer.html:14-18` — Babel-standalone + React dev UMD in
  production. (Claude MINOR, codex IMPORTANT — deferred)
- `data-live.js:272-289` — WS handlers don't validate payload shape
  before dereference. (both IMPORTANT)

### Claude-unique findings
- 🟡 IMPORTANT — ~10 sites in app.jsx access `STRUCT_META[r.structure].{tone,sym,label}` with no fallback. Per MEMORY's "BE/FE enum lockstep" entry, any future `Structure` literal added BE-first blanks every component that iterates rows (NormTable, PremiumBars, StructureBreakdown, FundingPanel, HedgeBook, ValuationChart legend).
- 🟡 IMPORTANT — `((cons - refFdv) / refFdv) * 100` math in AssetRail / AssetHeader / KPIStrip / NormTable propagates Infinity into `fmtPremium` when reference data goes zero/null.
- 🟡 IMPORTANT — `data-live.js:304` `r.tokenPrice / r.shareRatio` not guarded against `shareRatio === 0`. A bad calibrate write to 0 contaminates `asset.consensusFDV` with Infinity.
- MINOR — `connect()` does not detach old `ws.{onopen,onmessage,onerror,onclose}` before reassigning.

### Codex-unique findings
- 🔴 CRITICAL — `data-live.js:322-360` handleConfigUpdate patches existing rows when `share_ratio` exists but does NOT remove rows whose `ratio_source` became `uncalibrated`. Combined with the relabelling bug above, a venue can stay in LISTINGS with a stale ratio displayed as "Market-fitted" even after BE marks it uncalibrated.
- 🔴 CRITICAL — `app.jsx:1521,368-398,1192-1216` empty live listings crash KPIStrip (`norm.max.ex`) and DerivationPanel (`anchor.ex` where anchor is undefined). Reachable when all markets are uncalibrated/disabled, at startup before ticks, or after a config update removes valid rows.
- 🔴 CRITICAL — `data-live.js:386-388,440-441` `loadAlerts` only catches JSON.parse failures. A localStorage entry like `{}` or `[null]` is valid JSON but breaks `filter is not a function` in `evaluateAlerts`, which runs BEFORE the rest of `handleTick` — one corrupted entry freezes live updates on every tick.
- IMPORTANT — `app.jsx:190-192` TopBar always renders the green `LIVE` pulse, even when the footer says `WS RECONNECTING`.

### Applied fixes (2026-05-20)

| ID | File(s) | Severity | Source | Change |
|---|---|---|---|---|
| F52 | app.jsx | CRITICAL | both | `getAdminToken()` helper backed by `localStorage["preIpoAdminToken"]` + `window.prompt`; `adminFetch()` wraps both writes with `X-Admin-Token`, clears cache + re-prompts once on 401. Operator now retrieves the token from `backend/logs/admin_token.txt` (slice 3 F49). |
| F53 | data-live.js + app.jsx | CRITICAL | both | `ratioSourceFromMarket` returns `m.ratio_source` verbatim; `SRC_META` gained an `"uncalibrated"` entry; new `isLiveMarket()` predicate (share_ratio>0, finite, ratio_source!=="uncalibrated"); `handleConfigUpdate` now splices rows out of LISTINGS when `isLiveMarket` flips false; snapshot seeding uses the same gate. Stale "market-fitted" relabel is gone. |
| F54 | app.jsx (multiple) | CRITICAL | codex | KPIStrip computes `hasRows` and feeds `max/min/spread` through fallback values; DerivationPanel returns an explicit "Waiting for live calibrated venue…" panel when `anchor` is undefined; PremiumBars + ValuationChart render placeholder panels when their inputs are empty. No more `(undefined).ex` crashes. |
| F55 | data-live.js | CRITICAL | codex | `loadAlerts` parses JSON, falls back to `[]` on non-array, filters entries to `{assetId:string, kind:string, value:finite-number}`; self-heals localStorage by re-writing the validated subset. `evaluateAlerts` can no longer dereference null/garbage. |
| F56 | app.jsx | IMPORTANT | Claude | New `getStructMeta(s)` + `getSrcMeta(s)` helpers return `{sym, tone, label}` fallback objects when an enum value isn't in the meta map. Replaced ~10 unguarded `STRUCT_META[r.structure].{tone,sym,label}` sites + 3 `SRC_META[r.ratioSource]` sites. Future BE `Structure` / `RatioSource` literals now degrade to a neutral "?" pill instead of TypeError-ing out of the table. |
| F57 | app.jsx | IMPORTANT | Claude | New `safePct(num, denom)` + `safeRatioPct(num, denom)` helpers that return `null` when `denom <= 0` or non-finite; `fmtPct` now also renders "—" for null. AssetRail / AssetHeader / KPIStrip / NormTable / VenueCards / PremiumBars routed through these. No more `+Infinity%` / `NaNx` rendered text. |
| F58 | data-live.js | IMPORTANT | Claude | `handleTick` rejects `quote.token_price` NaN/Inf + uses `isLiveMarket(market)` instead of bare `share_ratio == null`. Zero / negative ratios are treated identically to null, so a bad calibrate write can no longer pump Infinity into `asset.consensusFDV`. |
| F59 | data-live.js | IMPORTANT | codex | Each WS-handler entry guards: `msg` is object, `assets`/`markets`/`statuses` are arrays, asset/market entries have string `id`/`asset_id`/`exchange`. Dispatcher now also validates `msg.type` is a string and wraps each handler call in try/catch so a thrown handler can't bork the subscription. |
| F60 | app.jsx | MINOR | both | ValuationChart latest-value dot uses `pts.length - 1` (renamed `lastIdx`) instead of hardcoded `pts[95]`. Warm-up dots no longer emit `cy="NaN"`. |
| F61 | app.jsx | MINOR | Claude | ValuationChart early-returns a "Waiting for tick history…" panel when `allVals` is empty (was `Math.min(...[])===Infinity` propagating into NaN coordinates). |
| F62 | app.jsx | MINOR | codex | New `ConnStatusCell` component drives the top-bar dot + label off `window.PRE_IPO_LIVE.connected`, refreshed by a 1s interval (matches the existing FootBar logic). "LIVE" and "WS RECONNECTING" can no longer appear simultaneously. |
| F63 | data-live.js | MINOR | Claude | `onclose` nulls `ws.{onopen,onmessage,onerror,onclose}` on the dying instance before scheduling reconnect; eliminates stale-closure risk on in-flight frames delivered between `onclose` and GC. |

**Deferred (record only):**
- Pre-IPO Normalizer.html:14-18 — replace React-dev UMD + Babel-standalone with a prebuilt `app.js` (esbuild/Vite, one-line npm). codex IMPORTANT, Claude MINOR. Larger build-tooling change; out of scope for the audit cycle.
- `Pre-IPO Normalizer.html:6` `<meta viewport content="width=1440">` forces desktop layout on mobile — not in audit scope, document only.
- `r.calibrationStatus="stale"` quotes look identical to "calibrated" in the table. TickAge badge partially compensates.
- `addPremiumAlert` parses `0x` as `value = -100` (premium-below); probably intended given the "consensus only" semantics.
- `r.spread` rounded to int in `data-live.js` — sub-bp precision loss if BE ever emits tight spot venues.
- The "scheduleReconnect could double-fire" risk Claude flagged turned out not to apply: `onclose` only runs once per ws instance, and the construction-throw path uses a fresh instance. Recorded as a non-defect.
- StructureBreakdown + HedgeBook were also checked for empty-rows safety; they degrade cleanly (empty arr → returns null) without changes.

### Verification (2026-05-20)
- `node --check data-live.js` → clean
- `node .check/check_jsx.cjs app.jsx` (@babel/preset-env + preset-react) → OK lines=1742 bytes=103444
- `node .check/smoke_slice4.cjs` → 47 PASS assertions, ALL SLICE-4 SMOKE TESTS PASSED. Mix of static-pattern checks against both files and a JSDOM-free functional sim that rewrites the IIFE wrapper to expose `handleSnapshot` / `handleTick` / `handleConfigUpdate` / `ratioSourceFromMarket` / `isLiveMarket` / `loadAlerts` for direct invocation. Asserts:
  - F53 sim: uncalibrated passes through (was "market-fitted")
  - F58 sim: `isLiveMarket` rejects 0 / negative / null / uncalibrated
  - F59 sim: handleSnapshot / handleTick tolerate null / non-object / empty
  - F53 sim: handleConfigUpdate that flips okx→uncalibrated drops the LISTINGS row
  - F55 sim: loadAlerts skips malformed entries AND self-heals localStorage
  - F55 sim: non-array storage returns `[]`
- Backend slice-3 smoke re-run after slice-4 edits → still ALL SMOKE TESTS PASSED (no regression).

### Artifacts from this slice (regenerable)
- Gitignored: `slice_4_prompt.txt`, `claude_slice_4.md`, `codex_slice_4.md`
- Committed: `.check/smoke_slice4.cjs` + `.check/check_jsx.cjs` + `.check/package.json` (needs `npm install` inside `.check/` to pull `@babel/standalone`; `node_modules/` stays gitignored).

## Slice 5 — never-audited + post-slice drift

**Date:** 2026-05-20 (KST).

### Reviewer verdicts
- **Self-review** (Claude general-purpose sub-agent): 3 CRITICAL, 8 IMPORTANT, 8 MINOR. Verdict: `acceptable-with-fixes`.
- **Codex review**: 2 CRITICAL, 3 IMPORTANT, 1 MINOR. Verdict: `unsafe`.

Both converged on **cross-adapter unit drift** (OI denomination + funding cadence inconsistent across OKX/BingX/HL) and **status-broadcast plumbing existing but never called** (FE saw stale adapter health until snapshot/config-update).

### Overlap findings (both flagged)
- Funding APR formula `rate*3*365` is OKX-8h-specific; HL is per-hour → 8× APR understatement
- AdapterStatus `exchange` collapse: 3 OKX adapters all reported `exchange="okx"`, FE merged → an unhealthy peer greys out healthy siblings
- NormTable "vs CONS." under filter compared against full-set consensus

### Claude-unique
- 🔴 HL OI in base units (`hyperliquid.py:194`); probe `openInterest=2089` base × $2164 markPx = $4.5M real but FE rendered $2089 (~1000× understated)
- 🔴 BingX OI squared (`bingx.py:185-186`); `openInterest=980327` (already USDT) × $2129.7 = $2.09B notional on a $1.2M-vol venue (1700× OI/vol — physically impossible)
- I7 BingX `closeTime` may be data-cadence-aligned not poll-aligned → `INSERT OR IGNORE` could collapse polls into one row
- I8 NormTable disabled rows bypass getStructMeta fallback contract
- I1 Gate/Bitget back-solved `ratio_calibrated_at` set to "today" — defeats DRIFT chip

### Codex-unique
- 🔴 `gate.py _to_float` accepts NaN/Inf (every sibling rejects)
- 🟡 `broadcast_status()` never called from supervisor — FE only sees status on snapshot/config-update
- 🟡 `start.sh` declares success before verifying the child survived

### Applied fixes (2026-05-20)

| ID | File(s) | Severity | Source | Change |
|---|---|---|---|---|
| F64 | hyperliquid.py | CRITICAL | Claude | OI multiplied by markPx before yielding (HL publishes `openInterest` in base units, unlike OKX/BingX). Adds `funding_interval_hours=1`. |
| F65 | models.py + okx.py + bingx.py + hyperliquid.py + normalizer.py + aggregator.py + data-live.js + app.jsx | CRITICAL | both | New `funding_interval_hours` threaded Tick → NormalizedQuote → FE row. OKX/BingX set 8; HL sets 1. `fundingAPR(rate, intervalHours)` annualizes via `(24/hours)*365`. HedgeBook + FundingPanel use per-leg cadence. Default 8 for back-compat. |
| F66 | bingx.py | CRITICAL | Claude | `_fetch_open_interest` returns BingX's `openInterest` directly (already USDT). Prior `oi*lastPrice` squared the value. |
| F67 | gate.py | CRITICAL | codex | `_to_float` now rejects NaN/Inf (mirrors okx/bingx). |
| F68 | aggregator.py + config.py | IMPORTANT | codex | New `_status_loop()` task broadcasts `supervisor.statuses()` every `status_broadcast_interval_seconds` (default 30s). FE FeedsCell + DISABLED refresh without waiting for snapshot. |
| F69 | models.py + supervisor.py + data-live.js | IMPORTANT | both | `AdapterStatus.market_id` required; supervisor sets it in all 3 status-construction sites. FE `rebuildDisabled` keys by `market_id` instead of `exchange`. Live `/api/status` returns 13 distinct entries (was collapsed). |
| F70 | app.jsx | IMPORTANT | both | NormTable computes filter-scoped consensus when `structFilter !== "all"`; column header shows `vs CONS. (spot)` / `(perp)`. KPIStrip stays full-set. |
| F71 | start.sh | IMPORTANT | codex | After `nohup ... &` + PID write, sleep 2s and `kill -0`; if dead, tail `bootstrap.err` + exit non-zero, no stale PID. |
| F72 | bingx.py | IMPORTANT | Claude | Tick timestamp uses `time.time()` instead of `closeTime` (data-cadence-aligned → `INSERT OR IGNORE` collapse risk). |
| F73 | app.jsx | MINOR | Claude | NormTable disabled rows route through `getStructMeta(d.structure)` for symmetry with live rows (F56 contract). |
| F74 | data/markets.json | MINOR | Claude | Bitget/Gate/BingX-PreStocks `ratio_calibrated_at` reset to anchor-subscription dates (2026-04-15 / 2026-04-20) so DRIFT chip fires correctly on the 30d/90d boundaries. |
| F75 | stop.sh | MINOR | Claude | `matches_app()` checks `/proc/<pid>/cmdline` (Linux) or `ps -o args=` (macOS) before killing — PID-reuse safety. |
| F76 | start.sh | MINOR | Claude | Venv discovery prefers `backend/.venv` (where pyproject.toml lives) over root `.venv` (tooling venv). |
| F77 | main.py | MINOR | Claude | `_allowed_root_files` drops `data.js` (replaced by `data-live.js`). |

### Verification (2026-05-20)
- `ruff check app` → clean; `compileall -q app` → clean
- `smoke_slice3.py` → 33 PASS (smoke updated for `market_id` in fake AdapterStatus)
- `smoke_slice4.cjs` → 131 PASS (12 new SLICE-5 assertions: F65 cadence, F69 market_id keying, F70 scoped consensus + scope label, F73 getStructMeta)
- Live `/readyz` → `{"adapters":13,"fresh":13}` after warm-up
- Live `/api/status` → 13 distinct (market_id, exchange) pairs (F69 verified)
- Live `/api/history/SPACEX` sanity (F64+F66 verified):
  - BingX OI ~$962K (was $2B pre-F66 — ~2200× reduction, matches $1.2M vol pattern)
  - HL OI $4.5M vntl / $33.8M hl-xyz (was ~$2K/$33K pre-F64 — now USD-denominated)
  - Consensus weights now proportional across all 13 markets

### Deferred (recorded, do not apply in this slice)
- M1: redundant `"uncalibrated"` fallback at two layers in data-live.js — both correct today, tripwire only
- M2: `getDriftDays` null-returns on future-dated calibrations — intentional clock-skew tolerance
- React-dev UMD + Babel-standalone in production HTML — deferred from slice 4
- Admin token in localStorage — acceptable on loopback, needs CSP for VPS
- Supervisor doesn't re-evaluate adapter enablement after admin writes — deferred since slice 2
- `*.ps1 text eol=crlf` not pinned in `.gitattributes` (no current .ps1 file)
- `funding_interval_hours` not persisted in SQLite — slice-6 schema migration candidate

### Artifacts from this slice (regenerable)
- Gitignored: `slice_5_prompt.txt`, `claude_slice_5.md`, `codex_slice_5.md`
