# FRIDAY — UI migration verification record

Scope: replacement of the previous Python-drawn interface with the `ui/` package
plus the Electron + React + WebGL2 renderer in `ui/app/`. Backend untouched:
`main.py`, `actions/`, `agent/`, `vision/`, `memory/`, `safety.py`, `api_keys.py`,
`google_oauth.py`, `logger.py`, `telegram_bot.py`, `core/prompt.txt`.

Verification environment for the numbers below: Linux (Debian bookworm, Python
3.11.2, Node 22.22.3, npm 10.9.8), repository at the tip of the migration branch.
The Windows-only checks that remain are listed in §5.

## 1. Gate results

### Renderer (in `ui/app`)

| Command | Result |
|---|---|
| `npx tsc -p tsconfig.json --noEmit` | clean (strict, `noUnusedLocals`, `noUnusedParameters`) |
| `npx tsc -p tsconfig.electron.json --noEmit` | clean |
| `npm test` (Vitest 5, jsdom) | **32 passed**, 4 files |
| `npm run build` | `dist/index.html` + `dist/assets/*` + `dist/electron/{main,preload}.js` |

Bundle: 437 kB JS (140 kB gzip) + 36 kB CSS (7 kB gzip). `base: './'` so the
`file://` loader resolves assets, `emptyOutDir: false` so Vite cannot delete the
`tsc`-compiled Electron entrypoints.

### Python (`tests/`)

| Command | Result |
|---|---|
| `python -m pytest tests/ -q` | **345 passed, 7 failed** |
| `python -m pytest tests/test_ui_electron.py -q` | **75 passed** |
| `python scripts/verify_windows_ui.py --headless` | **8 passed, 0 failed** |

The 7 failures are all in `tests/test_production_resilience.py` and are
**pre-existing and environment-only**: the same 7 fail on the migration's base
commit `437bc73` in this sandbox (verified by `git archive 437bc73 | tar -x` and
running the file there). They need Windows:

| Test | Root cause on Linux |
|---|---|
| `test_desktop_control_exec_free` | no `~/Desktop` directory |
| `test_listen_audio_does_not_crash_when_sounddevice_is_none` | `actions/game_updater.py` imports `winreg` |
| `test_play_audio_does_not_crash_when_sounddevice_is_none` | same |
| `test_confirm_*` (3 tests) | same backend import chain |
| `test_telegram_loop_exits_cleanly_if_chat_id_is_none` | same |

No UI test fails. The old GUI-toolkit test file is gone;
`tests/test_ui_electron.py` replaces it with 75 tests covering protocol, auth,
frames, dismissal, shutdown and the Phase-3 asset tree.

## 2. Phase gates

**Phase 1 — consolidation.** ✓
The previous UI package was renamed to `ui/`; the root facade module, its QML
directory, the image provider and the old toolkit test were deleted. `import ui`
loads only stdlib + `ui.themes`
(asserted by the verifier's Qt scan and by
`tests/test_ui_contract.py::test_import_is_pure`).

**Phase 2 — protocol / bridge / controller.** ✓
`ui/protocol.py`, `ui/bridge.py` (`UiDispatcher` + `CardsModel`/`ActivityModel`/
`TranscriptModel` + `UiBridge`), `ui/controller.py` (`WsServer`, `FramePump`,
`UiController`). 60 Hz amplitude cap, latest-frame-wins pump, 4001 auth, JPEG q78
at max side 960, idempotent shutdown, direct `electron.exe` spawn.

**Phase 3 — renderer.** ✓
Full `ui/app` tree: `package.json` (`main: dist/electron/main.js`), twin tsconfigs,
`vite.config.ts`, `index.html`, `electron/{main,preload}.ts`, `src/**` with the
GLSL 300 es orb, 10 glass cards, stage, inspector, dev bench, hooks and types.
`tsc`, `vitest` and `vite build` are all green.

**Phase 4 — full suite + cleanup.** ✓ (with the documented environment exception)
345 passed; zero references to the legacy UI package, its web sibling, the Qt
binding or QML anywhere in tracked Python, TypeScript, JSON, TOML or Markdown
(`git grep` below); `scripts/verify_windows_ui.py`
rewritten for Electron; `setup.py` builds the renderer; `.gitignore` covers
`ui/app/node_modules` and `ui/app/dist`; docs rewritten.

## 3. Contract checks worth re-running by hand

```powershell
# import purity + full public surface
python -c "import ui, sys; print(ui.get_base_dir()); print(sorted(k for k in ('FridayUI','make_ui','THEMES','SYSTEM_NAME') if hasattr(ui,k)))"

# zero legacy references
git grep -nE "ui_(siri|web)|PySide[0-9]|\.qm[l]" -- . ; echo "exit=$LASTEXITCODE"

# renderer gates
cd ui\app
npx tsc -p tsconfig.json --noEmit
npx tsc -p tsconfig.electron.json --noEmit
npm test
npm run build
```

## 4. Known environment note

The audit sandbox cannot reach Electron's binary CDN, so
`node_modules/electron/dist/electron.exe` is absent here and the live window
cannot be launched in this environment. Everything else — the npm package, its
typings, `tsc`, Vitest and the Vite build — runs, and the Python side is verified
against a real WebSocket (`--headless`). On a Windows machine `npm install`
downloads the binary and `python scripts\verify_windows_ui.py` exercises the real
window, summon, dismissal and orphan check.

## 5. Windows-only checks to run on the target machine

```powershell
python setup.py                      # pip + npm install + npm run build
python scripts\verify_windows_ui.py  # 9 checks incl. Win11 22H2 capabilities
```

Expected: toolchain (node/npm/electron.exe/renderer), Win11 build ≥ 22621
capabilities (`capture_exclusion`, `layered_windows`, `backdrop`,
`rounded_corners`, `dnd`), import purity, public API, card mapper, live controller
(snapshot → ping/pong → card → amplitude → invoke → quit) and **no orphaned
`electron.exe`**.

## 6. Rollback

The migration is a single branch. `git revert` of the merge restores the previous
interface only if the old package and the Qt dependency are restored with it;
both are deleted here deliberately — there is no supported half-state.
