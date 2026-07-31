# Review Synchronization Platform

## 1. Overview

This project synchronizes customer reviews from:

- Apple App Store Connect
- Google Play Developer API

Reviews are posted to Slack. A developer can reply inside the Slack thread, and the bot sends that reply back to the corresponding store.

The system is scheduled by GitHub Actions and uses Slack Web API methods only.

The current platform supports:

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
- Separate provider state files
- Atomic state writes
- State artifacts between workflow jobs
- Automatic state commits to Git

## 2. Current Architecture

```text
GitHub Actions scheduler or manual dispatch
                    │
                    ▼
        ┌──────────────────────────┐
        │ App Store job             │
        │ Google Play job           │
        │ Run in parallel           │
        └─────────────┬────────────┘
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

The App Store and Google Play jobs use separate state files and share the same Slack channel configuration for the current deployment.

## 3. Repository Structure

```text
review-bot/
│
├── .github/
│   └── workflows/
│       └── review-monitor.yml
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
│   ├── appstore_reviews.json
│   └── playstore_reviews.json
│
├── tests/
│   ├── test_appstore.py
│   ├── test_playstore.py
│   ├── test_slack_client.py
│   ├── test_state_manager.py
│   └── test_merge_state.py
│
├── requirements.txt
└── ticket.md
```

## 4. GitHub Actions Workflow

The workflow is located at:

```text
.github/workflows/review-monitor.yml
```

It runs through:

- `workflow_dispatch` for manual execution.
- A scheduled cron execution every five minutes.

The workflow uses a concurrency group so multiple workflow runs do not process and commit state simultaneously.

```yaml
concurrency:
  group: review-monitor
  cancel-in-progress: false
```

This prevents one run from cancelling another run while it is processing reviews or committing state.

## 5. Workflow Jobs

### 5.1 App Store Reviews Job

The App Store job:

1. Checks out the repository.
2. Installs Python 3.12.
3. Installs dependencies.
4. Runs the test suite.
5. Generates an App Store Connect JWT.
6. Fetches App Store reviews.
7. Performs initial or incremental synchronization.
8. Polls Slack threads when appropriate.
9. Sends Slack replies to App Store Connect.
10. Uploads `state/appstore_reviews.json` as an artifact.

### 5.2 Google Play Reviews Job

The Google Play job:

1. Checks out the repository.
2. Installs Python 3.12.
3. Installs dependencies.
4. Validates Google Play configuration.
5. Generates an OAuth access token using the official Google authentication library.
6. Fetches Google Play reviews.
7. Performs initial or incremental synchronization.
8. Polls Slack threads when appropriate.
9. Sends Slack replies to Google Play.
10. Uploads `state/playstore_reviews.json` as an artifact.

### 5.3 Commit State Job

The commit job waits for both provider jobs.

It:

1. Checks out the repository with write permission.
2. Downloads the App Store state artifact.
3. Downloads the Google Play state artifact.
4. Compares state files with the current branch.
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

Required secrets:

```text
APPSTORE_API_KEY_ID
APPSTORE_API_PRIVATE_KEY
APPSTORE_ISSUER_ID
APPSTORE_APP_ID
```

The existing JWT generator creates a short-lived ES256 JWT. The token is sent using:

```http
Authorization: Bearer <token>
```

The token is reused during the provider execution rather than regenerated for every request.

## 7. Google Play Authentication

Google Play uses a complete service-account JSON document stored in:

```text
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON
```

The package name is configured separately:

```text
GOOGLE_PLAY_PACKAGE_NAME
```

The service account is loaded with the official Google authentication library and the scope:

```text
https://www.googleapis.com/auth/androidpublisher
```

The library obtains and refreshes the OAuth access token. The project does not manually implement Google OAuth or manually create Google OAuth JWT assertions.

## 8. Slack Authentication and Configuration

Slack uses a bot token:

```text
SLACK_BOT_TOKEN
```

The current shared Slack channel is configured with:

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
7. Empty messages are ignored.
8. Deleted or unavailable threads are marked disabled.
9. Duplicate timestamps are ignored.
10. Replies at or before `last_reply_ts` are ignored.
11. Only newer human replies are candidates for provider replies.

Slack always returns the parent message even when there are no thread replies. Therefore, a response containing one message does not mean a human replied.

Empty polling results are logged at DEBUG level. Actual human replies are logged at INFO level.

## 13. App Store Reply Flow

When a new human Slack reply is found:

1. The system checks whether the review is already marked as replied.
2. The system checks whether an Apple response already exists.
3. If an Apple response exists, it does not create another response.
4. If no response exists, the Slack message text is sent to Apple.
5. The Apple response API response is validated.
6. `last_reply_ts` is updated.
7. `apple_reply_sent` is set to `true`.
8. State is saved.

Apple replies are not retried blindly because a network failure could occur after Apple accepted the response.

## 14. Google Play Reply Flow

When a new human Slack reply is found:

1. The system checks `google_reply_sent`.
2. Existing Google `developerComment` data is detected during review parsing.
3. Existing developer replies are not overwritten automatically.
4. Slack reply text is trimmed.
5. Empty text is rejected.
6. Text longer than 350 characters is safely truncated.
7. The system sends:

```http
POST /androidpublisher/v3/applications/{packageName}/reviews/{reviewId}:reply
```

with:

```json
{
  "replyText": "Developer response"
}
```

8. The API response is validated.
9. `last_reply_ts` is updated.
10. `google_reply_sent` is set to `true`.
11. State is saved.

## 15. Duplicate Protection

Duplicate review protection is based on the actual provider review ID.

Duplicate reply protection is based on:

```text
last_reply_ts
apple_reply_sent
google_reply_sent
```

A reply is processed only when:

```text
reply_timestamp > last_reply_ts
```

After the provider accepts the response, state is updated immediately.

If a provider already contains a developer reply, the state is marked as completed and the bot does not overwrite it automatically.

## 16. State Files

The current provider state files are:

```text
state/appstore_reviews.json
state/playstore_reviews.json
```

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
  "apple_reply_sent": false,
  "slack_thread_disabled": false
}
```

Typical Google Play entry:

```json
{
  "slack_ts": "1785404443.414699",
  "last_reply_ts": null,
  "google_reply_sent": false
}
```

State writes are atomic:

1. A temporary file is created in the state directory.
2. JSON is written and flushed.
3. The file is synchronized to disk.
4. `os.replace` atomically replaces the old state file.

## 17. State Commit Flow

Provider jobs do not push directly to Git. They upload state artifacts.

The final commit job:

1. Downloads provider artifacts.
2. Reads remote state from the branch.
3. Merges remote and local review mappings.
4. Preserves the newest reply timestamp.
5. Preserves any successful reply flag.
6. Preserves disabled-thread status.
7. Resets the working branch to the latest remote branch.
8. Reapplies merged state.
9. Commits only changed files.
10. Retries push operations up to three times.

This prevents the App Store and Google Play jobs from simultaneously pushing conflicting commits.

## 18. Failure Handling

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

## 19. Missing or Invalid Data

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

## 20. API Limitations

Google Play review retrieval is intentionally limited to the first API page in the current implementation.

Google’s API also exposes only reviews created or modified within a recent period. A long workflow outage or a very high review volume can result in reviews not being returned.

Pagination should be added before operating at high review volume.

Slack thread polling is also subject to Slack API rate limits. Each application adds more thread polling calls, so matrix parallelism must be controlled as the number of applications grows.

## 21. Testing

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
- State file isolation.
- State merge behavior.
- Google reply status preservation.

Run tests locally with:

```bash
PYTHONPATH=scripts python3 -m unittest discover -s tests -v
```

Compile Python files with:

```bash
python3 -m compileall -q scripts tests
```

## 22. Manual Setup

### Apple

Configure:

```text
APPSTORE_API_KEY_ID
APPSTORE_API_PRIVATE_KEY
APPSTORE_ISSUER_ID
APPSTORE_APP_ID
```

The App Store Connect API key must have permission to read customer reviews and manage responses.

### Google Play

Configure:

```text
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON
GOOGLE_PLAY_PACKAGE_NAME
```

The Google Play Developer API must be enabled. The service account must be granted Play Console access with permission to view reviews and reply to reviews.

### Slack

Configure:

```text
SLACK_BOT_TOKEN
SLACK_CHANNEL_ID
```

The Slack app must be installed in the workspace, have the required scopes, and be a member of the target channel.

## 23. Current User Flow

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
          Detect newer human replies
                    │
                    ▼
       Send replies to the correct store
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
Provider-specific reply endpoint called
              │
              ▼
Provider response validated
              │
              ▼
Reply timestamp marked processed
```

The Slack app is the visible sender inside Slack. The store response is published using the developer account represented by the Apple or Google API credentials.

## 24. Future Multi-Application Design

The current implementation is ready to evolve into a configuration-driven platform.

The future application registry should contain one entry per application and platform:

```text
airlines70-ios
airlines70-android
nakshatra-ios
nakshatra-android
```

Each entry should have:

- Provider.
- Provider application identifier.
- Slack channel ID.
- Credential environment.
- Enabled status.
- Initial synchronization settings.

The same provider code should be reused for every application using that provider.

Adding an application should require configuration, credentials, Slack setup, and state creation—not new provider code.

## 25. Operational Recommendation

GitHub Actions is suitable while the platform has a small number of applications and low review volume.

As the application count grows, migrate to:

```text
Configuration registry
        │
        ▼
Application matrix or queue
        │
        ▼
Provider workers
        │
        ▼
Database-backed state
        │
        ▼
Slack Web API
```

The current code should first move to application-scoped configuration and state. A matrix workflow is the next practical step. A queue and worker service become preferable when GitHub Actions startup time, Slack rate limits, state commits, or matrix limits become operational constraints.
