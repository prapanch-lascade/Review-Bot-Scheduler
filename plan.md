# Multi-App Review Bot — Central Workflow (repository_dispatch) + Infisical `/reviews`

## Context

The review bot (Apple App Store + Google Play reviews → Slack, and Slack replies back to the
stores) is hardwired to **one app (Airlines70)**. Config enters via 8 scattered
`os.environ[...]` reads; state files are named only by provider
(`state/{provider}_reviews.json`); the workflow has two fixed per-provider jobs with hardcoded
GitHub secrets.

**Goal:** make it a centralized, multi-app service using the **`Lascade-Co/actions` pattern**:
each app's own repo holds a tiny **trigger** workflow that fires on a schedule and sends a
`repository_dispatch` to this repo, passing only the **non-secret Infisical `project_slug`**.
The **central** workflow (here) pulls that app's secrets from a **`/reviews` folder** in its
existing Infisical project and runs the sync. **No secrets ever travel through GitHub payloads.**

**Decisions locked in**
- **Transport:** `repository_dispatch`; triggers live in each **app repo** (like `Lascade-Co/actions`).
- **Central home:** central workflow + Python code + committed state all live in **this repo**.
- **Secrets:** Infisical `/reviews` folder per app project, injected as **individual env vars**
  (`export-type: env`) whose names already match what the Python reads. Shared `SLACK_BOT_TOKEN`
  is a central-repo secret; per-app `SLACK_CHANNEL_ID` lives in `/reviews`.

**Why this is the right model (connector vs actions)**

| | connector | **actions (chosen)** |
|---|---|---|
| Transport | reusable workflow (`workflow_call`) | `repository_dispatch` + `client-payload` |
| Scope | one self-contained repo | shared central repo consumed by many app repos |
| Secrets | base64 GitHub secrets, duplicated per app | **Infisical**; caller sends only non-secret `project_slug` |
| Add an app | new trigger + new GitHub secret in one repo | copy a trigger into the app repo; add `/reviews` in its Infisical project |
| Secret sprawl | grows per app | none (single source of truth) |
| Cross-repo | no | yes |

`actions` wins here: apps already have Infisical projects, secrets stay out of payloads, and
adding an app is config-only.

---

## Architecture

```
App repo (e.g. Lascade-Co/airlines70)     .github/workflows/review-sync-trigger.yml
   cron */5  →  peter-evans/repository-dispatch@v4
   token: CENTRAL_DISPATCH_TOKEN (app-repo secret)
   repository: Lascade-Co/review-bot
   event-type: review-sync
   client-payload: { "app": "airlines70", "project_slug": "<infisical slug>", "env_slug": "prod" }
        │
        ▼
This repo — central .github/workflows/review-sync.yml   (on: repository_dispatch [review-sync])
        │
        ├── Infisical/secrets-action  (method: universal, secret-path: /reviews, export-type: env)
        │     → injects APPSTORE_*, GOOGLE_PLAY_*, SLACK_CHANNEL_ID as env vars
        │
        ├── matrix: [appstore, playstore]
        │     APP_SLUG=<app>, SLACK_BOT_TOKEN=<central secret>
        │     python scripts/main.py <provider>
        │     (provider whose keys are absent = logs "not configured" and exits 0)
        │     upload artifact  <app>-<provider>-state
        │
        └── commit-state job → download artifacts → merge_state.py → commit state/<app>/* → push (retry x4)
```

Infisical machine-identity creds (`INFISICAL_CLIENT_ID/SECRET/DOMAIN`) live **only in this
central repo** and must have read access to each app project's `/reviews` path. The app repo
holds only `CENTRAL_DISPATCH_TOKEN` (permission to dispatch here) — no bot secrets.

---

## Infisical `/reviews` folder (per app project, env `prod`)

Individual keys — the names already match the Python's `os.environ[...]` reads, so Infisical's
`export-type: env` wires them up with zero translation:

| Key | Consumed by | Required for |
|---|---|---|
| `APPSTORE_API_KEY_ID` | `jwt_generator.py:11` | App Store |
| `APPSTORE_API_PRIVATE_KEY` | `jwt_generator.py:13` | App Store |
| `APPSTORE_ISSUER_ID` | `jwt_generator.py:12` | App Store |
| `APPSTORE_APP_ID` | `appstore.py:77` | App Store |
| `GOOGLE_PLAY_PACKAGE_NAME` | `playstore.py:27` | Google Play |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | `playstore.py:34` (raw JSON string) | Google Play |
| `SLACK_CHANNEL_ID` | `slack_client.py:30` | both |

- **iOS-only app:** include only the `APPSTORE_*` keys + `SLACK_CHANNEL_ID`; omit `GOOGLE_PLAY_*`.
  The Play cell detects the missing keys and no-ops. **Android-only:** the reverse.
- `SLACK_BOT_TOKEN` is **not** in `/reviews`; it is a shared central-repo secret set as env on the sync step.

---

## Part 1 — Python changes (minimal: Infisical injects the exact env vars already used)

No config-loader/bridge is needed — the previous `app_config.py` idea is dropped. The only
changes are state scoping and a provider-presence guard.

### 1a. `scripts/common/state_manager.py` — app-scope state into a per-app folder

Read `APP_SLUG` only inside `_state_file`; no call site changes. Each app gets its own folder
holding one file per provider (`state/<app>/appstore.json`, `state/<app>/playstore.json`); only
the providers an app uses are ever written. Legacy fallback when `APP_SLUG` is unset keeps the
old flat names and existing tests green. **Required** so apps don't share state. `os` already
imported (L3). `save_state` also co-locates its atomic temp file in `file.parent` so the folder
is created and the rename stays on one filesystem.

```python
def _state_file(provider: str) -> Path:
    app_slug = os.environ.get("APP_SLUG", "").strip()
    if app_slug:
        app_dir = STATE_DIR / app_slug
        app_dir.mkdir(parents=True, exist_ok=True)
        return app_dir / f"{provider}.json"          # state/airlines70/appstore.json
    STATE_DIR.mkdir(exist_ok=True)
    return STATE_DIR / f"{provider}_reviews.json"    # legacy fallback (APP_SLUG unset)
```

### 1b. Provider-presence guard (supports iOS-only / Android-only apps)

`os` is already imported in both providers. Add an early return so an app missing a platform's
`/reviews` keys skips that provider instead of crashing.

`scripts/providers/appstore.py` — top of `run_appstore()` (before `generate_token()`):
```python
REQUIRED = ("APPSTORE_API_KEY_ID", "APPSTORE_ISSUER_ID", "APPSTORE_API_PRIVATE_KEY", "APPSTORE_APP_ID")
if not all(os.environ.get(k) for k in REQUIRED):
    LOG.info("App Store not configured for this app; skipping")
    return
```

`scripts/providers/playstore.py` — top of `run_playstore()`:
```python
if not (os.environ.get("GOOGLE_PLAY_PACKAGE_NAME") and os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON")):
    LOG.info("Google Play not configured for this app; skipping")
    return
```

### 1c. `scripts/main.py` — unchanged

Still `main.py <provider>`. Config comes from the Infisical-injected env vars; `APP_SLUG` is set
by the workflow. No app argument needed. (Optional nicety: accept an app arg and
`os.environ.setdefault("APP_SLUG", ...)` for local runs — not required.)

### 1d. Migrate existing state (one-time)

```
mkdir -p state/airlines70
git mv state/appstore_reviews.json  state/airlines70/appstore.json
git mv state/playstore_reviews.json state/airlines70/playstore.json
```

Keeps Airlines70 on the incremental path instead of re-posting the latest 5. A genuinely new app
has no per-app file → clean initial sync (latest 5, no reply polling), per ticket §9.

### 1e. Tests — `tests/test_state_manager.py`

- `setUp`: `os.environ.pop("APP_SLUG", None)` (via `patch.dict`) so the literal-name asserts at
  L22/L27/L37 are deterministic regardless of runner env.
- Add `test_app_slug_scopes_state_into_app_folder`: with `APP_SLUG=airlines70`, assert
  `state/airlines70/appstore.json` is created (and no flat name, no leftover `.tmp`).

`test_appstore.py`, `test_playstore.py`, `test_merge_state.py` are unaffected.

---

## Part 2 — Workflows

### 2a. Central `.github/workflows/review-sync.yml` (this repo)

```yaml
name: Review Sync (Central)
run-name: "review-sync · ${{ github.event.client_payload.app || inputs.app }}"

on:
  repository_dispatch:
    types: [review-sync]
  workflow_dispatch:             # manual single-app run
    inputs:
      app:          { description: "App slug (state prefix)", required: true, type: string }
      project_slug: { description: "Infisical project slug",  required: true, type: string }
      env_slug:     { description: "Infisical env slug",      required: false, type: string, default: "prod" }

concurrency:
  group: review-sync-${{ github.event.client_payload.app || inputs.app }}
  cancel-in-progress: false      # per-app; push-retry handles cross-app git races

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: read
    strategy:
      fail-fast: false
      matrix:
        provider: [appstore, playstore]
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt
      - name: Run Tests
        run: PYTHONPATH=scripts python -m unittest discover -s tests -v   # APP_SLUG unset here → legacy-name tests pass
      - name: Import /reviews secrets from Infisical
        uses: Infisical/secrets-action@v1.0.16
        with:
          method: universal
          client-id: ${{ secrets.INFISICAL_CLIENT_ID }}
          client-secret: ${{ secrets.INFISICAL_CLIENT_SECRET }}
          domain: ${{ secrets.INFISICAL_DOMAIN }}
          project-slug: ${{ github.event.client_payload.project_slug || inputs.project_slug }}
          env-slug: ${{ github.event.client_payload.env_slug || inputs.env_slug || 'prod' }}
          secret-path: /reviews
          export-type: env
      - name: Sync ${{ matrix.provider }}
        env:
          APP_SLUG: ${{ github.event.client_payload.app || inputs.app }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
        run: python scripts/main.py ${{ matrix.provider }}
      - name: Upload state
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: ${{ github.event.client_payload.app || inputs.app }}-${{ matrix.provider }}-state
          path: state/${{ github.event.client_payload.app || inputs.app }}/${{ matrix.provider }}.json
          if-no-files-found: warn

  commit-state:
    needs: [sync]
    if: always()
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write
    env:
      APP: ${{ github.event.client_payload.app || inputs.app }}
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/download-artifact@v4
        with: { name: ${{ env.APP }}-appstore-state, path: state/${{ env.APP }} }
        continue-on-error: true
      - uses: actions/download-artifact@v4
        with: { name: ${{ env.APP }}-playstore-state, path: state/${{ env.APP }} }
        continue-on-error: true
      - name: Commit Updated State
        run: |
          set -euo pipefail
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          APP_DIR="state/${APP}"
          # Add the whole per-app folder so single-platform apps (only one
          # provider file present) still stage cleanly — `git add a b` where b
          # is missing is fatal and stages nothing.
          git add "${APP_DIR}" 2>/dev/null || true
          if git diff --cached --quiet; then echo "No state changes."; exit 0; fi
          # JSON-space reconcile (NOT git pull --rebase, which would conflict on
          # these files): merge the app's remote state into the just-synced local.
          merge_remote() {
            f="${APP_DIR}/$1.json"; r="/tmp/remote_${APP}_$1.json"
            [ -f "$f" ] || return 0
            if git show "origin/${GITHUB_REF_NAME}:${f}" > "$r" 2>/dev/null; then python scripts/merge_state.py "$r" "$f"; fi
          }
          # Re-sync with the latest remote before every commit attempt; retry a
          # rejected push up to 4 times (concurrent runs advance the branch).
          for attempt in 1 2 3 4; do
            git fetch origin "${GITHUB_REF_NAME}"
            merge_remote appstore; merge_remote playstore
            git reset --mixed "origin/${GITHUB_REF_NAME}"
            git add "${APP_DIR}" 2>/dev/null || true
            if git diff --cached --quiet; then echo "Remote already current."; exit 0; fi
            git commit -m "Update review state (${APP})"
            if git push; then echo "Pushed on attempt ${attempt}/4."; exit 0; fi
            echo "Push rejected; retry ${attempt}/4."
          done
          echo "Unable to push state after 4 attempts."; exit 1
```

Notes: Run Tests runs **before** the Infisical step and `APP_SLUG` is set only on the Sync step
(step-scoped), so the test step sees a clean env and the legacy-name tests pass. `merge_state.py`
invocation is unchanged. This adapts the current `commit-state` job to app-scoped filenames.

### 2b. Trigger `.github/workflows/review-sync-trigger.yml` (in each APP repo)

```yaml
name: Trigger Review Sync
on:
  workflow_dispatch:
  schedule:
    - cron: "*/5 * * * *"
permissions:
  contents: read
jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - name: Dispatch to central review-bot
        uses: peter-evans/repository-dispatch@v4
        with:
          token: ${{ secrets.CENTRAL_DISPATCH_TOKEN }}     # app-repo secret; may dispatch to review-bot
          repository: Lascade-Co/review-bot                 # <- the central repo
          event-type: review-sync
          client-payload: >-
            {
              "app": "airlines70",
              "project_slug": "the-infisical-project-slug",
              "env_slug": "prod"
            }
```

### 2c. Remove `.github/workflows/review-monitor.yml`

Superseded. Airlines70's schedule moves to its app repo's trigger.

---

## Part 3 — Setup (manual, out of code)

**Central `review-bot` repo secrets**
- `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_DOMAIN` — machine identity with **read access to each app project's `/reviews` path**.
- `SLACK_BOT_TOKEN` — shared bot token.

**Each app repo secret**
- `CENTRAL_DISPATCH_TOKEN` — PAT or GitHub App token allowed to POST `repository_dispatch` to `Lascade-Co/review-bot`.

**Infisical (per app project)**
- Add a **`/reviews` folder** in env `prod` with the keys in the table above.
- Grant the central machine identity read on that path.

**Slack**
- Create the app's channel, invite the shared bot, put its ID in `/reviews` → `SLACK_CHANNEL_ID`.

---

## Adding a new app (config only — no code)

1. Create the Slack channel; invite the bot; note the channel ID.
2. In the app's Infisical project, create a `/reviews` folder (env `prod`) with the App Store
   and/or Google Play keys + `SLACK_CHANNEL_ID`.
3. In the app's repo: add secret `CENTRAL_DISPATCH_TOKEN` and copy
   `review-sync-trigger.yml`, setting `app` + `project_slug`.
4. First run performs an initial sync (latest 5, no reply polling); state is committed here as
   `state/<app>_*_reviews.json`.

---

## Security notes

- **No secrets in the payload.** `client_payload` carries only `app`, `project_slug`, `env_slug`
  — all non-secret. This is the whole reason to switch to Infisical.
- **Machine-identity creds are central-only**; app repos never hold bot secrets, just a dispatch token.
- **Least privilege:** the central machine identity should have read on `/reviews` paths only.
- Google Play service-account JSON and the Apple PEM are stored as ordinary Infisical secret
  values and injected as env vars — no base64/shell-multiline handling, and the action masks them.

---

## Verification

1. **Unit tests (no APP_SLUG):** `PYTHONPATH=scripts python3 -m unittest discover -s tests -v` — all green incl. new `test_app_slug_scopes_state_filename`; then `python3 -m compileall -q scripts tests`.
2. **State scoping:** with `APP_SLUG=airlines70` set, run a provider against a temp state dir and confirm it writes `state/airlines70/appstore.json` (folder per app), not the legacy flat name.
3. **Provider guard:** run `run_playstore()` with `GOOGLE_PLAY_*` unset → logs "not configured" and returns without error (and the appstore path still works).
4. **End-to-end (staging):** set the central repo's Infisical + Slack secrets; populate a test app's `/reviews`; add `CENTRAL_DISPATCH_TOKEN` + the trigger in the app repo; run the trigger (or `workflow_dispatch` the central with `app`/`project_slug`). Confirm: Infisical fetch succeeds, both provider cells run (or no-op), artifacts named `<app>-appstore-state`/`<app>-playstore-state`, commit-state commits `state/<app>_*` and pushes. Verify Slack posts land in the app channel and a thread reply round-trips to the store.
5. **Second-app isolation:** onboard a throwaway second app to a test channel; run both triggers close together and confirm no git push corruption (retry loop resolves races) and per-app state files stay isolated.

---

## Files to create / modify

| File | Action |
|---|---|
| `scripts/common/state_manager.py` | modify `_state_file` → `state/<app>/<provider>.json`; `save_state` temp file co-located in `file.parent` |
| `scripts/providers/appstore.py` | add provider-presence guard at top of `run_appstore()` |
| `scripts/providers/playstore.py` | add provider-presence guard at top of `run_playstore()` |
| `tests/test_state_manager.py` | clear `APP_SLUG` in setUp; add app-scope test |
| `state/appstore_reviews.json`, `state/playstore_reviews.json` | `git mv` → `state/airlines70/appstore.json`, `state/airlines70/playstore.json` |
| `.github/workflows/review-sync.yml` | **new** — central (repository_dispatch + Infisical `/reviews`) |
| `.github/workflows/review-monitor.yml` | **delete** — superseded |
| `review-sync-trigger.yml` (in each app repo) | **new** — cron → `repository_dispatch` |

`scripts/main.py`, `review_sync.py`, `slack_client.py`, `jwt_generator.py`, `merge_state.py`,
and the provider fetch/reply internals are **unchanged**.
