# Review Synchronization Platform

## 1. Overview

This project synchronizes customer reviews from:

- Apple App Store Connect
- Google Play Developer API

Reviews are posted to Slack. A developer can reply inside the Slack thread, and the bot sends that reply back to the corresponding store.

The platform is multi-application and configuration-driven. A small trigger workflow in each application's own repository fires on a schedule and sends a `repository_dispatch` event to this central repository. The central workflow reads that application's secrets from a `/reviews` folder in its Infisical project and runs the synchronization. Each application has its own Slack channel and its own state folder.

The system uses Slack Web API methods only.

The current platform supports:

- Multiple applications served by one centralized workflow
- Per-application configuration and secrets sourced from Infisical
- Per-application Slack channels
- Automatic per-platform enablement based on which secrets are present
- App Store review polling
- Google Play review polling
- Initial synchronization
- Incremental synchronization
- Slack thread creation
- Slack thread polling
- Human-reply detection
- Duplicate reply protection
- Apple developer responses
- Google Play developer replies
- Per-application state folders (one JSON file per provider)
- Atomic state writes
- State artifacts between workflow jobs
- Automatic state commits to Git

## 2. Current Architecture

```text
App repository (one per application)
  Trigger workflow: schedule every 5 minutes or manual dispatch
                    │
                    │ repository_dispatch: review-sync
                    │ client-payload: { app, project_slug, env_slug }
                    ▼
Central repository: Review Sync workflow
                    │
                    ▼
       Import /reviews secrets from Infisical
                    │
                    ▼
        ┌──────────────────────────┐
        │ App Store job             │
        │ Google Play job           │
        │ Run in parallel (matrix)  │
        └─────────────┬────────────┘
                      │
                      ▼
     Skip a provider when its secrets are absent
                      │
                      ▼
              Provider API fetch
                      │
                      ▼
          Initial or incremental sync
                      │
                      ▼
                Slack Web API
                      │
                      ▼
            Slack thread polling
                      │
                      ▼
          Store developer response
                      │
                      ▼
             Provider state update
                      │
                      ▼
              Upload state artifact
                      │
                      ▼
              Final commit-state job
                      │
                      ▼
                Push state to Git
```

The payload carries only non-secret identifiers. The actual credentials never travel through GitHub; they are read from the application's Infisical project inside the central workflow. Each application has its own state folder; the App Store and Google Play jobs use separate state files inside that folder and post to the same per-application Slack channel.

## 3. Repository Structure

```text
review-bot/                          (central repository)
│
├── .github/
│   └── workflows/
│       └── review-sync.yml          (central workflow; repository_dispatch + manual)
│
├── triggers/
│   └── review-sync-trigger.yml      (template to copy into each application repository)
│
├── scripts/
│   ├── main.py
│   ├── merge_state.py
│   │
│   ├── providers/
│   │   ├── appstore.py
│   │   └── playstore.py
│   │
│   └── common/
│       ├── jwt_generator.py
│       ├── review_sync.py
│       ├── slack_client.py
│       ├── slack_notifier.py
│       ├── state_manager.py
│       └── utils.py
│
├── state/
│   └── airlines70/                  (one folder per application)
│       ├── appstore.json
│       └── playstore.json
│
├── tests/
│   ├── test_appstore.py
│   ├── test_playstore.py
│   ├── test_slack_client.py
│   ├── test_state_manager.py
│   └── test_merge_state.py
│
├── requirements.txt
├── plan.md
└── ticket.md
```

## 4. GitHub Actions Workflow

### 4.1 Central Workflow

The central workflow is located at:

```text
.github/workflows/review-sync.yml
```

It runs through:

- `repository_dispatch` with the event type `review-sync`, sent by an application's trigger workflow.
- `workflow_dispatch` for manual execution of a single application (inputs: `app`, `project_slug`, `env_slug`).

The workflow uses a per-application concurrency group so runs for the same application do not process and commit state simultaneously, while different applications still run in parallel.

```yaml
concurrency:
  group: review-sync-${{ github.event.client_payload.app || inputs.app }}
  cancel-in-progress: false
```

Cross-application Git races are handled by the commit-state job's push-retry loop.

### 4.2 Trigger Workflow

Each application repository holds a small trigger workflow, copied from:

```text
triggers/review-sync-trigger.yml
```

It runs on a five-minute schedule (and manual dispatch) and sends a `repository_dispatch` to the central repository:

```yaml
client-payload: >-
  {
    "app": "airlines70",
    "project_slug": "the-infisical-project-slug",
    "env_slug": "prod"
  }
```

The application repository needs one secret, `CENTRAL_DISPATCH_TOKEN`, authorized to dispatch to the central repository. The payload carries only non-secret identifiers and no application credentials.

## 5. Workflow Jobs

### 5.1 Secret Import

Before a provider runs, the central workflow imports the application's secrets from Infisical using the official Infisical action with universal machine-identity authentication. It reads the `/reviews` folder of the project named by the payload's `project_slug`, in the environment named by `env_slug`, and exports each secret as an environment variable whose name matches what the code expects.

### 5.2 App Store Reviews Job

The App Store job:

1. Checks out the repository.
2. Installs Python 3.12.
3. Installs dependencies.
4. Runs the test suite.
5. Imports `/reviews` secrets from Infisical.
6. Skips immediately when the App Store secrets are not present for this application.
7. Generates an App Store Connect JWT.
8. Fetches App Store reviews.
9. Performs initial or incremental synchronization.
10. Polls Slack threads when appropriate.
11. Sends Slack replies to App Store Connect.
12. Uploads `state/<app>/appstore.json` as an artifact.

### 5.3 Google Play Reviews Job

The Google Play job:

1. Checks out the repository.
2. Installs Python 3.12.
3. Installs dependencies.
4. Runs the test suite.
5. Imports `/reviews` secrets from Infisical.
6. Skips immediately when the Google Play secrets are not present for this application.
7. Generates an OAuth access token using the official Google authentication library.
8. Fetches Google Play reviews.
9. Performs initial or incremental synchronization.
10. Polls Slack threads when appropriate.
11. Sends Slack replies to Google Play.
12. Uploads `state/<app>/playstore.json` as an artifact.

The App Store and Google Play jobs run as a matrix within one workflow run. An application that provides only one platform's secrets runs only that platform; the other job logs that the provider is not configured and exits successfully.

### 5.4 Commit State Job

The commit job waits for both provider jobs.

It:

1. Checks out the repository with write permission.
2. Downloads the App Store state artifact.
3. Downloads the Google Play state artifact.
4. Compares the application's state files with the current branch.
5. Merges remote state with local state when necessary.
6. Commits only if state changed.
7. Pushes the updated state to the repository.
8. Retries Git push operations when the remote branch changes concurrently.

Only this job requires:

```yaml
permissions:
  contents: write
```

The provider jobs use read-only repository permissions.

## 6. App Store Authentication

The App Store provider uses an App Store Connect API key.

The following keys are read from the application's Infisical `/reviews` folder and injected as environment variables by the central workflow:

```text
APPSTORE_API_KEY_ID
APPSTORE_API_PRIVATE_KEY
APPSTORE_ISSUER_ID
APPSTORE_APP_ID
```

When these keys are absent for an application (for example, an Android-only application), the App Store job logs that the provider is not configured and exits without doing any work.

The existing JWT generator creates a short-lived ES256 JWT. The token is sent using:

```http
Authorization: Bearer <token>
```

The token is reused during the provider execution rather than regenerated for every request.

## 7. Google Play Authentication

Google Play uses a complete service-account JSON document, read from the application's Infisical `/reviews` folder:

```text
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON
```

The package name is a separate key in the same folder:

```text
GOOGLE_PLAY_PACKAGE_NAME
```

When these keys are absent for an application (for example, an iOS-only application), the Google Play job logs that the provider is not configured and exits without doing any work.

The service account is loaded with the official Google authentication library and the scope:

```text
https://www.googleapis.com/auth/androidpublisher
```

The library obtains and refreshes the OAuth access token. The project does not manually implement Google OAuth or manually create Google OAuth JWT assertions.

## 8. Slack Authentication and Configuration

Slack uses one shared bot token, stored as a secret on the central repository and set as an environment variable for the sync step:

```text
SLACK_BOT_TOKEN
```

Each application posts to its own Slack channel. The channel is configured per application as a key in that application's Infisical `/reviews` folder:

```text
SLACK_CHANNEL_ID
```

Slack API methods used by the system are:

```text
chat.postMessage
conversations.replies
auth.test
```

The bot token should have the minimum required scopes:

```text
chat:write
channels:history
groups:history
```

The bot must be a member of private channels.

The system uses Slack channel IDs and thread timestamps. It does not depend on Slack usernames or channel names for routing.

## 9. Initial Synchronization

Initial synchronization occurs when the provider state does not contain a `last_review_id`.

### App Store Initial Sync

The App Store provider:

1. Fetches the newest reviews.
2. Sorts them newest-first.
3. Selects a maximum of five reviews.
4. Removes any review already present in state.
5. Reverses the selected list.
6. Posts reviews oldest-to-newest to Slack.
7. Stores each review ID and Slack thread timestamp.
8. Sets `last_review_id` to the newest fetched review.
9. Saves state.
10. Returns immediately.

Slack replies are not processed during initial synchronization.

### Google Play Initial Sync

The Google Play provider follows the same behavior:

1. Fetches reviews.
2. Normalizes and sorts them newest-first using the documented user-comment timestamp.
3. Selects a maximum of five reviews.
4. Removes any review already present in state.
5. Posts them oldest-to-newest.
6. Saves Slack thread mappings.
7. Sets `last_review_id` to the newest review.
8. Saves state.
9. Returns without polling Slack replies.

This behavior prevents a fresh installation from processing old Slack messages as store replies.

## 10. Incremental Review Synchronization

Incremental synchronization uses two protections:

1. The review ID mapping in state.
2. The `last_review_id` boundary.

Example state:

```json
{
  "last_review_id": "review-105",
  "reviews": {
    "review-105": {
      "slack_ts": "123.456"
    },
    "review-104": {
      "slack_ts": "123.400"
    }
  }
}
```

Suppose the provider returns:

```text
review-108
review-107
review-106
review-105
review-104
```

The provider processes:

```text
review-108
review-107
review-106
```

When `review-105` is reached, it is treated as the previous synchronization boundary and scanning stops.

The system then removes any IDs already present in the `reviews` mapping and posts only genuinely new reviews.

New reviews are posted oldest-to-newest so Slack displays them in chronological order.

## 11. Slack Review Message Flow

For every new review:

1. The provider converts its API response into the shared review message format.
2. The formatter escapes user-controlled Slack markup characters.
3. `chat.postMessage` sends the message to the configured channel.
4. Slack returns a message timestamp called `ts`.
5. The system stores that timestamp against the provider review ID.

Example mapping:

```text
Google review ID
        │
        ▼
Slack channel ID + thread timestamp
```

The Slack timestamp is the link required to poll replies later.

## 12. Slack Thread Polling

On normal incremental runs:

1. The bot identifies itself using `auth.test`.
2. The state manager returns every review with a Slack timestamp.
3. The Slack client calls `conversations.replies` using the stored channel and thread timestamp.
4. Slack returns the parent message and any replies.
5. The parent bot message is ignored.
6. Bot messages are ignored.
7. Slack workflow and system messages are ignored.
8. Empty messages are ignored.
9. Deleted or unavailable threads are marked disabled.
10. Duplicate timestamps are ignored.
11. Replies at or before `last_reply_ts` are ignored.
12. Only newer ordinary human replies are candidates for provider replies.

Slack always returns the parent message even when there are no thread replies. Therefore, a response containing one message does not mean a human replied.

Empty polling results are logged at DEBUG level. Actual human replies are logged at INFO level.

The number of messages in a thread is never used to decide whether to send a store response. It is only useful diagnostic information. For example, a thread with four messages may contain the bot parent and three human replies; the newest eligible human reply is the desired response.

## 13. Latest Human Reply Selection and Replacement

Both stores allow one public developer response for one customer review:

- Apple creates or replaces the existing `customerReviewResponse`.
- Google Play `reviews.reply` creates or updates the existing developer reply.

The system therefore treats the newest eligible human Slack message as the desired public response, rather than treating the first reply as final.

Example Slack thread:

```text
10:00  Review Bot: Customer review parent message
10:05  Developer: Thank you for your feedback.
10:10  Developer: We will improve this in the next release.
10:15  Developer: The improvement is planned for the next release.
```

During that polling run, the bot selects only the 10:15 message. It does not send the earlier two replies. On a later run, if another newer human message appears, the provider's existing response is replaced with that new text.

The shared selection logic is:

1. Read all Slack messages returned for the stored thread timestamp.
2. Ignore the bot parent, bots, workflow/system messages, empty messages, duplicate timestamps, and messages at or before `last_reply_ts`.
3. Sort remaining human messages by Slack `ts`.
4. Select the newest message.
5. Normalize the text and calculate a SHA-256 `last_sent_reply_hash`.
6. If that hash equals the stored hash, do not call Apple or Google. Advance `last_reply_ts` so the identical new Slack message is not reconsidered.
7. If the hash differs, call the provider update endpoint.
8. Update state only after the provider accepts the response.

If Apple or Google fails, no reply timestamp or hash is stored for that failed message. The same newest reply remains eligible for the next workflow run.

## 14. App Store Reply Flow

When a new human Slack reply is found:

1. The shared Slack logic selects the newest changed human reply.
2. The Apple provider sends the text to the App Store Connect create-or-update response endpoint:

```http
POST /v1/customerReviewResponses
```

3. The payload relates the response to the exact Apple `customerReviews` ID.
4. Apple creates the first response or replaces the existing response for that review.
5. The Apple response API response is validated.
6. `last_reply_ts` and `last_sent_reply_hash` are updated.
7. `apple_reply_sent` is set to `true` as a successful-send status.
8. State is saved atomically.

The `apple_reply_sent` flag does not prevent a later Slack reply from being processed. The timestamp and hash decide whether an update is required.

## 15. Google Play Reply Flow

When a new human Slack reply is found:

1. The shared Slack logic selects the newest changed human reply.
2. Slack reply text is trimmed.
3. Empty text is rejected.
4. Text longer than 350 characters is safely truncated.
5. The system sends:

```http
POST /androidpublisher/v3/applications/{packageName}/reviews/{reviewId}:reply
```

with:

```json
{
  "replyText": "Developer response"
}
```

6. The API response is validated.
7. Google creates the first reply or updates its existing developer reply.
8. `last_reply_ts` and `last_sent_reply_hash` are updated.
9. `google_reply_sent` is set to `true` as a successful-send status.
10. State is saved atomically.

Google review retrieval can include an existing `developerComment`. It is never displayed as customer review text. It is detected for operational visibility, but it does not block a newer eligible Slack reply from intentionally updating the provider response.

## 16. Duplicate Protection

Duplicate review protection is based on the actual provider review ID.

Duplicate reply protection is based on:

```text
last_reply_ts
last_sent_reply_hash
```

A reply is processed only when:

```text
reply_timestamp > last_reply_ts
```

The timestamp avoids reprocessing already handled Slack messages. The hash avoids an unnecessary Apple or Google API call when a newer Slack message has identical content to the public response already sent.

`apple_reply_sent` and `google_reply_sent` are retained as status fields for compatibility and diagnostics. They do not block later response updates.

## 17. State Files

State is organized as one folder per application, with one JSON file per provider inside it:

```text
state/<app>/appstore.json
state/<app>/playstore.json
```

For example:

```text
state/airlines70/appstore.json
state/airlines70/playstore.json
```

The application name comes from the `app` field in the dispatch payload and is passed to the code as the `APP_SLUG` environment variable, which the state manager uses to build the folder path. Only the providers an application actually uses are ever created (an Android-only application only has `playstore.json`). When `APP_SLUG` is unset the state manager falls back to the legacy single-application names (`state/appstore_reviews.json`, `state/playstore_reviews.json`), which keeps local runs and the test suite working.

An empty state file is valid:

```json
{}
```

The state manager adds default fields when loading state:

```json
{
  "state_version": 1,
  "last_review_id": null,
  "last_checked": null,
  "reviews": {}
}
```

Typical App Store entry:

```json
{
  "slack_ts": "1785314502.003999",
  "last_reply_ts": null,
  "last_sent_reply_hash": null,
  "apple_reply_sent": false,
  "slack_thread_disabled": false
}
```

Typical Google Play entry:

```json
{
  "slack_ts": "1785404443.414699",
  "last_reply_ts": null,
  "last_sent_reply_hash": null,
  "google_reply_sent": false
}
```

State writes are atomic:

1. A temporary file is created in the state directory.
2. JSON is written and flushed.
3. The file is synchronized to disk.
4. `os.replace` atomically replaces the old state file.

## 18. State Commit Flow

Provider jobs do not push directly to Git. They upload state artifacts.

The final commit job runs a loop of up to four attempts. On every attempt it:

1. Fetches the latest remote branch.
2. Reads the application's remote state from the branch.
3. Merges remote and local review mappings.
4. Preserves the newest reply timestamp and the matching reply hash from the same state snapshot.
5. Preserves any successful reply flag.
6. Preserves disabled-thread status.
7. Resets the working branch to the latest remote branch.
8. Reapplies the merged state.
9. Commits only changed files.
10. Pushes; if the push is rejected because another run advanced the branch, it repeats the attempt (up to four times total).

The reconcile-before-commit is performed on every attempt, so a run always builds its commit on top of the newest remote state and never overwrites a concurrent run's update. A plain `git pull --rebase` is intentionally not used: rebasing the JSON state files would create merge conflicts that a line-based merge cannot resolve. Instead the reconciliation happens in JSON space (via `scripts/merge_state.py`), and the commit is rebuilt on the latest remote with `git reset --mixed`.

The commit job operates only on the current application's folder (`state/<app>/`). This prevents the App Store and Google Play jobs from simultaneously pushing conflicting commits, and the four-attempt push-retry loop resolves races between different applications committing to the repository at the same time.

## 19. Failure Handling

Network requests use shared retry logic.

Transient handling includes:

- Network exceptions.
- HTTP 429 responses.
- HTTP 5xx responses.
- Slack `Retry-After` headers.

Write requests to stores are not blindly retried because a remote request may have succeeded even when the response was lost.

Provider failures include provider and review context where available.

Examples:

```text
provider=Google Play
review_id=abc123
endpoint=...
http_status=400
error=...
```

## 20. Missing or Invalid Data

The Google Play provider safely handles missing optional fields:

- Author name defaults to `Anonymous`.
- Language defaults to `Unknown`.
- Version defaults to `Unknown`.
- Missing title defaults to `No Title`.
- Missing review text defaults to `No review text provided.`.
- Missing timestamps display as `Unknown`.
- Invalid review objects are logged and skipped.
- Invalid ratings are not allowed to crash the complete fetch operation.

Only `userComment` is used as review content. `developerComment` is never used as the customer review body.

## 21. API Limitations

Google Play review retrieval is intentionally limited to the first API page in the current implementation.

Google’s API also exposes only reviews created or modified within a recent period. A long workflow outage or a very high review volume can result in reviews not being returned.

Pagination should be added before operating at high review volume.

Slack thread polling is also subject to Slack API rate limits. Each application adds more thread polling calls, so matrix parallelism must be controlled as the number of applications grows.

## 22. Testing

The test suite covers:

- App Store initial synchronization.
- App Store incremental synchronization.
- Slack bot-message filtering.
- Slack rate-limit errors.
- Slack GET thread polling.
- Google review formatting.
- Google title/body parsing.
- Missing optional fields.
- Empty reviews.
- Developer comments.
- Invalid ratings.
- Invalid review schemas.
- Reply truncation.
- Review ID mapping.
- Duplicate reply protection.
- Newest human reply selection.
- Identical reply skipping using the response hash.
- Bot and Slack system-message filtering.
- Failed provider updates leaving reply state unchanged.
- State file isolation.
- Per-application state folder scoping.
- State merge behavior.
- Timestamp-and-hash merge consistency.
- Google reply status preservation.

Run tests locally with:

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -v
```

Compile Python files with:

```bash
python3 -m compileall -q scripts tests
```

## 23. Manual Setup

### 23.1 Central repository secrets

Set these once on the central repository:

```text
INFISICAL_CLIENT_ID
INFISICAL_CLIENT_SECRET
INFISICAL_DOMAIN
SLACK_BOT_TOKEN
```

The Infisical machine identity must have read access to the `/reviews` path of every application project. `SLACK_BOT_TOKEN` is the shared bot used for all applications.

### 23.2 Infisical (per application)

In each application's existing Infisical project, create a `/reviews` folder in the target environment (for example `prod`) and add the keys the application needs:

```text
APPSTORE_API_KEY_ID               (App Store)
APPSTORE_API_PRIVATE_KEY          (App Store)
APPSTORE_ISSUER_ID                (App Store)
APPSTORE_APP_ID                   (App Store)
GOOGLE_PLAY_PACKAGE_NAME          (Google Play)
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON  (Google Play)
SLACK_CHANNEL_ID                  (both)
```

Include only the platforms the application ships. An Android-only application omits the `APPSTORE_*` keys; an iOS-only application omits the `GOOGLE_PLAY_*` keys.

The App Store Connect API key must have permission to read customer reviews and manage responses. The Google Play Developer API must be enabled, and the service account must be granted Play Console access to view and reply to reviews.

### 23.3 Application repository

In each application's repository:

1. Add the secret `CENTRAL_DISPATCH_TOKEN`, authorized to send `repository_dispatch` to the central repository.
2. Copy `triggers/review-sync-trigger.yml` into `.github/workflows/`, and set `app`, `project_slug`, and `env_slug` in the payload.

### 23.4 Slack

Create the application's Slack channel, invite the shared bot, and put the channel ID into the application's Infisical `/reviews` folder as `SLACK_CHANNEL_ID`. The Slack app must be installed in the workspace, have the required scopes, and be a member of the channel.

## 24. Current User Flow

### First Workflow Run

```text
Workflow starts
      │
      ├── App Store fetches latest reviews
      │
      └── Google Play fetches latest reviews
                    │
                    ▼
              Select latest five
                    │
                    ▼
            Post oldest-to-newest
                    │
                    ▼
             Save Slack timestamps
                    │
                    ▼
             Save provider state
                    │
                    ▼
             Skip reply polling
```

The developer sees the latest reviews in Slack as separate parent messages.

### Normal Workflow Run

```text
Workflow starts
      │
      ├── Fetch current App Store reviews
      └── Fetch current Google Play reviews
                    │
                    ▼
        Compare provider IDs with state
                    │
                    ▼
              Find new reviews
                    │
                    ▼
        Post only new reviews to Slack
                    │
                    ▼
             Poll known Slack threads
                    │
              ▼
          Select newest human reply
              │
              ▼
  Hash comparison: unchanged or provider update
                    │
                    ▼
              Update state mappings
                    │
                    ▼
               Commit state to Git
```

### Developer Reply Flow

```text
Developer replies in Slack thread
              │
              ▼
conversations.replies returns parent + replies
              │
              ▼
Bot parent message ignored
              │
              ▼
Human reply timestamp compared with state
              │
              ▼
Newest changed human reply selected
              │
              ▼
Apple or Google response created/updated
              │
              ▼
Reply timestamp and response hash saved
```

The Slack app is the visible sender inside Slack. The store response is published using the developer account represented by the Apple or Google API credentials.

## 25. Multi-Application Design (Implemented)

The platform is configuration-driven and multi-application. Adding an application requires configuration, credentials, and Slack setup — not new provider code.

Each application is identified by its `app` slug (used for the state folder and Slack routing) and its Infisical `project_slug` (used to locate secrets). The unit of configuration is the application's Infisical `/reviews` folder, which holds:

- App Store keys (optional).
- Google Play keys (optional).
- Slack channel ID.
- Credential environment, selected by the `env_slug` in the trigger payload.

Which provider runs is derived automatically from which keys are present, so a single application entry covers iOS-only, Android-only, or both. The same provider code is reused for every application.

### Adding an application

1. Create the application's Slack channel and invite the shared bot.
2. Add a `/reviews` folder to the application's Infisical project with the required keys.
3. Add `CENTRAL_DISPATCH_TOKEN` and the trigger workflow to the application's repository.

The first run performs an initial synchronization and creates the application's state folder; no manual state creation is needed.

## 26. Operational Recommendation

GitHub Actions is suitable while the platform has a moderate number of applications and low review volume. Application-scoped configuration and state, Infisical-based secrets, per-application dispatch triggers, and the per-provider matrix are already in place.

As the application count grows, the remaining evolution is:

```text
Infisical configuration per application   (in place)
        │
        ▼
Per-application repository_dispatch triggers   (in place)
        │
        ▼
Provider matrix per run   (in place)
        │
        ▼
Database-backed state
        │
        ▼
Queue and worker service
```

Database-backed state and a queue-and-worker service become preferable when GitHub Actions startup time, Slack rate limits, state commits, or the number of scheduled application triggers become operational constraints.
