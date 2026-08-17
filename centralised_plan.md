# Review Bot — Centralised Build Plan (central-repo secrets; Infisical later)

## 1. What we are building

A **centralised** review bot: app repositories fire a shared central workflow, which fetches
customer reviews from the **Apple App Store** and **Google Play**, posts each new review to the
app's **Slack channel**, and sends developer replies typed in the Slack thread back to the store.
It runs on **GitHub Actions**, keeps **per-app state committed to the repo**, and needs no server
or database.

Design:
- **Centralised architecture.** An **app-repo trigger** fires the central workflow via
  `repository_dispatch`, passing the app slug in the payload (plus manual `workflow_dispatch`). The
  central workflow runs App Store + Google Play as a matrix and writes per-app state under
  `state/<app>/`. All logic — pagination, dedup, bounded polling/pruning, provider guards — is
  provider- and app-neutral.
- **Secrets from the central repo's GitHub Actions secrets** (`prapanch-lascade/Review-Bot-Scheduler`).
  Infisical is not used for now. One app (**airlines70**) is onboarded; its credentials are the
  central repo's secrets. Adding more apps later changes only secret provisioning (§11), not the
  logic.
- **One shared internal Slack bot** (created in the Lascade workspace → exempt from Slack's
  distributed-app rate cuts; keeps ~50 req/min, 1000 msgs/request).
- **Full correctness/scale protection:** pagination, a persistent dedup set, and bounded polling
  with dynamic pruning.

---

## 2. Architecture

```
App repo (airline70-flutter)   .github/workflows/review-sync-trigger.yml
   cron */5  →  peter-evans/repository-dispatch@v4
   token: CENTRAL_DISPATCH_TOKEN   (allowed to dispatch to the central repo)
   repository: prapanch-lascade/Review-Bot-Scheduler
   event-type: review-sync
   client-payload: { "app": "airlines70" }
        │
        ▼
Central repo — .github/workflows/review-sync.yml
   on: repository_dispatch [review-sync]  +  workflow_dispatch (manual)
        │
        ├── secrets: read the 8 provider/Slack secrets from the central repo's GitHub secrets
        │
        ├── matrix [appstore, playstore]   (a provider self-skips if its secrets are absent)
        │     PAGINATED fetch → select-new via posted_ids → post new reviews oldest→newest
        │     poll ACTIVE threads → send newest human reply → record replied_at
        │     upload artifact  airlines70-<provider>-state
        │
        └── commit-state → download artifacts → merge_state → PRUNE inactive → commit → push (retry x4)
```

State is written to `state/airlines70/appstore.json` and `state/airlines70/playstore.json`.

> The app-repo trigger is the entry point (matching the centralised app-repo pattern) and extends
> to more apps by adding one trigger per app. `workflow_dispatch` on the central workflow is kept
> for manual runs.

---

## 3. Secrets & configuration

**On the central repo** (`prapanch-lascade/Review-Bot-Scheduler` → Settings → Secrets and variables →
Actions → New repository secret):

| Secret | Value |
|---|---|
| `APPSTORE_API_KEY_ID` | App Store Connect key ID |
| `APPSTORE_API_PRIVATE_KEY` | full `.p8` private-key contents (multiline is fine) |
| `APPSTORE_ISSUER_ID` | issuer UUID |
| `APPSTORE_APP_ID` | numeric Apple app ID (not the bundle ID) |
| `GOOGLE_PLAY_PACKAGE_NAME` | e.g. `com.lascade.airlines70` |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | raw service-account JSON (not base64) |
| `SLACK_CHANNEL_ID` | the app's Slack channel ID |
| `SLACK_BOT_TOKEN` | the internal Slack bot token (`xoxb-…`) |

**On the app repo** (`airline70-flutter`):
- `CENTRAL_DISPATCH_TOKEN` — a token allowed to POST `repository_dispatch` to the central repo.

**Slack:** create the channel, invite the internal bot, and put the channel ID in `SLACK_CHANNEL_ID`.
The Apple key must be able to read reviews and manage responses; the Google service account must
have Play Console access to view and reply to reviews.

A platform whose secrets are absent is skipped by the provider guard, so an iOS-only or Android-only
app works by simply not setting that platform's secrets.

---

## 4. State schema

`state/airlines70/<provider>.json`:

```json
{
  "state_version": 2,
  "last_review_id": "…",              // newest review seen (incremental boundary + pagination stop hint)
  "last_checked": "…",
  "posted_ids": ["id1", "id2", …],    // every review ever posted — the dedup source, NEVER pruned
  "reviews": {                         // ACTIVE pollable set only (aged out by the prune step)
    "id1": {
      "slack_ts": "…",                 // the Slack message/thread this review lives in
      "posted_at": "2026-08-04T10:00:00Z",  // when posted to Slack (open-poll window)
      "last_reply_ts": null,           // newest Slack reply already handled
      "last_sent_reply_hash": null,    // sha256 of the reply already sent (skip re-send)
      "replied_at": null,              // when we last sent a reply (edit window)
      "<provider>_reply_sent": false,
      "slack_thread_disabled": false
    }
  }
}
```

Tunable constants:
- `OPEN_POLL_WINDOW_DAYS = 30` — keep polling an **un-replied** review this long, then stop.
- `REPLY_EDIT_WINDOW_DAYS = 2` — after a reply is sent, keep polling this long (to allow the
  developer to edit/replace it), then stop.

**Migration (v1 → v2), in `load_state`:** if `posted_ids` is missing, set it to
`list(reviews.keys())`; if an entry lacks `posted_at`, backfill from `last_checked` (or now). This
deduplicates existing reviews and never re-posts them.

**Why `posted_ids` is separate from `reviews`:** dedup ("have we ever posted this?") must be
permanent, but polling ("should we still check this thread?") must be bounded. Keeping the full ID
list forever (a few KB) lets us prune the heavy `reviews` entries without ever re-posting an old or
edited review. `last_review_id` is a top-level string and is **never pruned**, so the incremental
boundary always survives.

---

## 5. Code — modules and behavior

### 5.1 Entry & config
- `scripts/main.py` — `main.py <provider>`; logs `Review sync starting: app=<APP_SLUG> provider=<provider>`; dispatches to `run_appstore()` / `run_playstore()`. `APP_SLUG` (state-folder name) comes from the workflow env.
- `scripts/common/jwt_generator.py` — builds the App Store Connect ES256 JWT (20-min expiry) from `APPSTORE_*` env vars.
- Google auth lives in `playstore._credentials()` — loads `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`, gets an OAuth token via `google-auth`.

### 5.2 Shared helpers
- `scripts/common/utils.py` — IST time helpers, `stars(rating)`, and `request_with_retries` (retries network/429/5xx with backoff; **retries disabled for writes**, since a timed-out write may have already succeeded).
- `scripts/common/slack_client.py` — Slack Web API wrapper: `post_review` (returns thread `ts`), `replies` (paginated), `identify_bot`, and `is_human_message` (drops the bot's own parent, other bots, and any message with a `subtype` = system/edited/workflow). Typed errors: permission (fatal), thread-not-found (disable thread), rate-limited.

### 5.3 State — `scripts/common/state_manager.py`
- `_state_file(provider)` → `state/<APP_SLUG>/<provider>.json` (folder auto-created), legacy flat name when `APP_SLUG` unset (keeps tests working).
- `load_state` — v2 migration/backfill (§4); logs the resolved path and fresh-vs-incremental.
- `save_state` — atomic write (temp file in the target folder → `fsync` → `os.replace`).
- `upsert_review`, `save_if_changed` (no-op when nothing meaningful changed → no git churn).

### 5.4 Provider-neutral logic — `scripts/common/review_sync.py`
- `reply_hash(text)` — sha256 of the normalized reply.
- `select_new_reviews(...)` — **dedup against `posted_ids`** (not the prunable `reviews` map). Initial run posts newest 5; incremental walks newest→oldest stopping at `last_review_id`, collecting anything not in `posted_ids`.
- `post_new_reviews(...)` — posts new reviews oldest→newest; per post: `slack.post_review`, add id to `posted_ids`, add a `reviews` entry with `posted_at = now`, save. Then advance `last_review_id`.
- `reply_candidates(...)` — unique, newer-than-`last_reply_ts`, human, non-empty messages, sorted.
- `sync_slack_replies(...)` — for each **active** thread, take the newest human reply; if its hash equals `last_sent_reply_hash`, skip the store call and advance `last_reply_ts`; else send to the store, then set `last_reply_ts`, `last_sent_reply_hash`, `replied_at = now`, and the reply flag. Failures leave state unchanged (retried next run). Optionally skip polling entries already inactive by the window rule.

### 5.5 Providers — `appstore.py`, `playstore.py`
- **Guards:** `run_appstore`/`run_playstore` log "not configured" and return when their platform's env vars are absent.
- **Pagination in `fetch_reviews`:**
  - Apple — follow the JSON:API `data["links"]["next"]` URL in a loop; stop on no `next`, when `last_review_id` appears (createdDate order is stable → safe early stop), or at a page cap (e.g. 25).
  - Google — loop passing `token=<nextPageToken>` until `tokenPagination.nextPageToken` is absent, a page yields no id outside `posted_ids`, or the page cap. `LOG.warning` if the cap is hit (no silent truncation).
- Formatting, escaping, validation, reply-to-store (writes with retries disabled), Google 350-char truncation — as in the existing providers.

### 5.6 Merge — `scripts/merge_state.py`
Reconciles a remote state snapshot with the local one in **JSON space** (used by the commit job):
unions `reviews`, keeps the newest `last_reply_ts` with its matching hash, ORs the reply/disabled
flags, picks the most-recent `last_review_id`/`last_checked`, and **unions `posted_ids`**
(`sorted(set(remote) | set(local))`) so no id is ever lost.

### 5.7 Prune — `scripts/prune_state.py` (new)
```python
# usage: prune_state.py STATE_FILE   (no-op if the file doesn't exist)
OPEN_POLL_WINDOW_DAYS = 30
REPLY_EDIT_WINDOW_DAYS = 2

def prune_inactive(state: dict, now: datetime) -> dict:
    posted = set(state.get("posted_ids", []))
    kept = {}
    for review_id, e in state.get("reviews", {}).items():
        posted.add(review_id)                       # never lose the id (dedup)
        if e.get("slack_thread_disabled"):
            continue                                # dead thread → stop polling
        if e.get("last_reply_ts"):
            if _age_days(e.get("replied_at"), now) <= REPLY_EDIT_WINDOW_DAYS:
                kept[review_id] = e                 # replied recently → keep for edits
        else:
            if _age_days(e.get("posted_at"), now) <= OPEN_POLL_WINDOW_DAYS:
                kept[review_id] = e                 # open & recent → keep polling
    state["reviews"] = kept
    state["posted_ids"] = sorted(posted)
    return state
# missing/unparseable posted_at/replied_at → treat as "now" (keep, don't accidentally drop)
```
The prune runs **in the commit job, after the merge, before `git add`** (running it before the merge
would be undone by the union). Applied on every commit, the committed state converges to the pruned
set and stays small, so the next run polls only active threads.

---

## 6. Workflows

### 6.1 Central — `.github/workflows/review-sync.yml`
```yaml
name: Review Sync (Central)
run-name: "review-sync · ${{ github.event.client_payload.app || inputs.app }}"

on:
  repository_dispatch:
    types: [review-sync]
  workflow_dispatch:
    inputs:
      app:
        description: "App slug (state-folder name)"
        required: true
        type: string
        default: "airlines70"

concurrency:
  group: review-sync-${{ github.event.client_payload.app || inputs.app }}
  cancel-in-progress: false

jobs:
  sync:
    name: Sync ${{ matrix.provider }}
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
        run: PYTHONPATH=scripts python -m unittest discover -s tests -v   # APP_SLUG unset → clean env
      - name: Sync Reviews and Slack Replies
        env:
          APP_SLUG: ${{ github.event.client_payload.app || inputs.app }}
          APPSTORE_API_KEY_ID: ${{ secrets.APPSTORE_API_KEY_ID }}
          APPSTORE_API_PRIVATE_KEY: ${{ secrets.APPSTORE_API_PRIVATE_KEY }}
          APPSTORE_ISSUER_ID: ${{ secrets.APPSTORE_ISSUER_ID }}
          APPSTORE_APP_ID: ${{ secrets.APPSTORE_APP_ID }}
          GOOGLE_PLAY_PACKAGE_NAME: ${{ secrets.GOOGLE_PLAY_PACKAGE_NAME }}
          GOOGLE_PLAY_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON }}
          SLACK_CHANNEL_ID: ${{ secrets.SLACK_CHANNEL_ID }}
          SLACK_BOT_TOKEN: ${{ secrets.SLACK_BOT_TOKEN }}
        run: python scripts/main.py ${{ matrix.provider }}
      - name: Upload State
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: ${{ github.event.client_payload.app || inputs.app }}-${{ matrix.provider }}-state
          path: state/${{ github.event.client_payload.app || inputs.app }}/${{ matrix.provider }}.json
          if-no-files-found: warn

  commit-state:
    name: Commit Updated State
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

          git add "${APP_DIR}" 2>/dev/null || true      # whole folder → single-platform apps stage cleanly
          if git diff --cached --quiet; then echo "No state changes."; exit 0; fi

          merge_remote() {
            f="${APP_DIR}/$1.json"; r="/tmp/remote_${APP}_$1.json"
            [ -f "$f" ] || return 0
            if git show "origin/${GITHUB_REF_NAME}:${f}" > "$r" 2>/dev/null; then
              python scripts/merge_state.py "$r" "$f"
            fi
          }

          # Re-sync with the latest remote, reconcile (JSON-space, not git rebase), prune inactive,
          # commit, and push — retry a rejected push up to 4 times.
          for attempt in 1 2 3 4; do
            git fetch origin "${GITHUB_REF_NAME}"
            merge_remote appstore
            merge_remote playstore
            python scripts/prune_state.py "${APP_DIR}/appstore.json"
            python scripts/prune_state.py "${APP_DIR}/playstore.json"
            git reset --mixed "origin/${GITHUB_REF_NAME}"
            git add "${APP_DIR}" 2>/dev/null || true
            if git diff --cached --quiet; then echo "Remote already current."; exit 0; fi
            git commit -m "Update review state (${APP})"
            if git push; then echo "Pushed on attempt ${attempt}/4."; exit 0; fi
            echo "Push rejected; retry ${attempt}/4."
          done
          echo "Unable to push state after 4 attempts."; exit 1
```

Notes: tests run before `APP_SLUG` is set (legacy-name state tests stay green). The prune runs after
the merge so pruning is never resurrected by the union. `git add "state/${APP}"` (folder) is used
because `git add fileA fileB` with a missing file is fatal and stages nothing — which would break
single-platform apps.

### 6.2 App-repo trigger — `.github/workflows/review-sync-trigger.yml` (in `airline70-flutter`)
```yaml
name: Trigger Review Sync
on:
  workflow_dispatch:
  schedule:
    - cron: "*/5 * * * *"     # runs only on the repo's DEFAULT branch
permissions:
  contents: read
jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - uses: peter-evans/repository-dispatch@v4
        with:
          token: ${{ secrets.CENTRAL_DISPATCH_TOKEN }}
          repository: prapanch-lascade/Review-Bot-Scheduler
          event-type: review-sync
          client-payload: >-
            { "app": "airlines70" }
```

---

## 7. Slack rate limits (why one internal bot is enough)
- Limits are per **app × workspace × method** ([docs](https://docs.slack.dev/apis/web-api/rate-limits/)).
- The May-2025 cut (1 req/min, 15 msgs) hits **only commercially distributed apps**; **internal
  apps are exempt** and keep **~50 req/min, 1000 msgs/request**
  ([changelog](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps/)).
  This bot is internal → 50/min.
- The rate-limit cost is the reply-polling (`conversations.replies`, one call per active thread per
  run). Bounded polling (§5.7) keeps that small. Client-side throttling only smooths bursts; fewer
  calls is the real lever, and `request_with_retries` already honors `Retry-After`.
- Free workspaces cap installed apps at 10 and hide messages older than 90 days
  ([usage limits](https://slack.com/help/articles/115002422943-Usage-limits-for-free-workspaces)) — the
  latter is the only case where a very-late reply becomes unreadable.

---

## 8. Worst-case → fix

| Concern | Fix |
|---|---|
| Reviews missed when a burst arrives between runs | pagination (§5.5) |
| Slack rate limit as review history grows | bounded polling + prune (§5.7) + internal-bot 50/min |
| Pruning silently undone by the merge union | prune **after** merge, in the commit job (§5.7, §6.1) |
| Edited old Google review re-posted after prune | `posted_ids` persistent dedup (§4, §5.4) |
| Concurrent pushes rejected | reconcile-before-commit + 4-attempt retry (§6.1) |
| JSON state can't be git-rebased/merged | JSON-space merge (`merge_state.py`) + `git reset --mixed` |
| Single-platform app never persists state | `git add "state/<app>/"` folder, not `git add a b` |
| iOS-only / Android-only app | provider-presence guards skip the absent platform |
| Late reply after days | supported up to `OPEN_POLL_WINDOW_DAYS` (tunable) |

---

## 9. Verification
1. **Unit tests** (no `APP_SLUG`): existing suite green; add tests for `select_new_reviews` deduping via `posted_ids`, `prune_inactive` (keeps open<30d, replied<2d, drops the rest, preserves ids), pagination (multi-page fetch + stop-at-boundary), and `merge_states` unioning `posted_ids`.
2. **Prune-after-merge converges**: remote has an old inactive R1 + R2; run merge → prune → commit; assert R1 is gone and stays gone on a second cycle.
3. **Re-post safety**: after pruning R1, feed a fetch that re-includes R1 (edited/bumped) and assert it is NOT re-posted (it's in `posted_ids`).
4. **Concurrent push**: two runs racing; the rejected one retries and both apps'/providers' state survive.
5. **End-to-end**: add the 8 central-repo secrets; run the central workflow (`workflow_dispatch` with `app=airlines70`, or the app-repo trigger). Confirm both provider cells run (or self-skip), artifacts upload, prune+commit+push happen, Slack posts land in the channel, and a thread reply round-trips to the store. Confirm a second run polls only the small active set (check the "Polling …" log count).

---

## 10. Files to create / modify

| File | Action |
|---|---|
| `scripts/main.py` | startup debug log |
| `scripts/common/state_manager.py` | v2 load/migration (`posted_ids`, `posted_at`); `STATE_VERSION=2`; folder state; path logs |
| `scripts/common/review_sync.py` | dedup via `posted_ids`; append to `posted_ids` + set `posted_at` on post; set `replied_at` on reply |
| `scripts/providers/appstore.py` | pagination; provider guard; `posted_ids`/`posted_at` on post |
| `scripts/providers/playstore.py` | pagination; provider guard; `replied_at` bookkeeping |
| `scripts/merge_state.py` | union `posted_ids` |
| `scripts/prune_state.py` | **new** — `prune_inactive` + CLI (no-op if file missing) |
| `.github/workflows/review-sync.yml` | central workflow (§6.1), incl. central-repo secrets + prune calls |
| `tests/…` | new tests for dedup, pagination, prune, merge-union |
| `state/airlines70/{appstore,playstore}.json` | the app's committed state |
| `review-sync-trigger.yml` (in `airline70-flutter`) | app-repo trigger (§6.2) |

`slack_client.py`, `slack_notifier.py`, `jwt_generator.py`, `utils.py` unchanged.

---

## 11. Future — multiple apps
The architecture is already multi-app (per-app triggers, `app` in the payload, per-app `state/<app>/`
folders). The **only** thing that changes when more apps are added is **secret provisioning** — the
fixed-name central-repo secrets hold one app's credentials. To scale, move per-app secrets into
**Infisical** (a `/reviews` folder per app project),
replace the central workflow's env block with the `Infisical/secrets-action` step keyed by a
`project_slug` from the trigger payload, and provision `INFISICAL_CLIENT_ID/SECRET/DOMAIN` +
`CENTRAL_DISPATCH_TOKEN` as GitHub **org secrets** so every Lascade-Co repo inherits them. Nothing
in §4–§5 (state schema, pagination, dedup, pruning) changes.
