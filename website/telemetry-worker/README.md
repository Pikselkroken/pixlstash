# Telemetry ingestion Worker

Receives anonymous install pings and publishes aggregate cohort numbers. This is
the first PixlStash endpoint that accepts unauthenticated writes from the public
internet, so it is built as an attack surface rather than as plumbing.

**Not deployed.** Everything here runs and is tested locally. Deployment needs
CSO sign-off on both the endpoint and the Cloudflare API credential.

## Where the data lives

Three places, and only the first two ever hold an identifier.

| Where | What | Retention |
|---|---|---|
| The user's own machine | `install-id.json` beside `server-config.json`: one random UUIDv4 | Until the user hits Recreate, or deletes it |
| Cloudflare D1 | One row per install: the UUID, first-seen and last-seen **dates**, a 63-bit activity bitmap, a new-install flag, one of four install-type buckets | 400 days since last seen, then pruned by the daily cron |
| `pixlstash-metrics` (private git) | Aggregates only: counts and percentages | Forever, which is exactly why no identifier may ever go there |

Nothing touches the PixlStash library database. The install ID deliberately
lives beside the server config rather than in `vault.db` so a snapshot restore or
a library switch cannot change or duplicate an installation's identity.

**Never stored anywhere:** IP addresses, user agents, request timestamps,
versions, or any other request metadata. The client IP is read once as a
rate-limit key and discarded inside the request handler.

The user-facing wording in `PRIVACY.md` is therefore "we never log or retain it",
not "we never request it". The Worker does read `CF-Connecting-IP`, and an
absolute that the code does not honour literally is the first thing a hostile
reader attacks.

### Choosing the D1 region, which is a one-shot decision

D1's physical location is fixed at creation and **cannot be changed afterwards**.
Recovering from the wrong choice means creating a new database and migrating.

The rows are a persistent identifier tied to first-seen and last-seen dates, so
they are personal data under GDPR and the primary belongs in the EU:

```sh
wrangler d1 create pixlstash-telemetry --location=weur
```

`--location` is a **hint**: Cloudflare uses "the nearest possible location (by
latency) to your preference" and does not guarantee placement. D1 also supports a
**jurisdiction** constraint, which is binding rather than best-effort and is
likewise creation-only. For an EEA user base the jurisdiction is the stronger
choice. Confirm the exact flag against `wrangler d1 create --help` before
running it, because there is no second attempt.

Then apply the schema:

```sh
wrangler d1 execute pixlstash-telemetry --file=./schema.sql --remote
```

## Endpoints

### `POST /v1/ping`

```json
{
  "install_id": "9f2c1b7e-4d5a-4c81-b3e6-8a7d2f0e5c14",
  "is_new_install": true,
  "install_type": "pip"
}
```

Returns `204` with an empty body. Nothing is echoed back.

Defences, in the order they run:

| Control | Behaviour |
|---|---|
| Method | Anything but POST is `405` |
| Rate limit | 20/minute per IP, `429` past that |
| Size cap | 512 bytes. `Content-Length` is checked first as a cheap rejection, then the actual read is capped independently, because the header is attacker-controlled |
| Parse | Malformed JSON is `400` |
| Schema | Reject-by-default: an unrecognised key, a missing key, a wrong type, a non-canonical UUIDv4, or an install type outside the four buckets is `400` and nothing is stored |
| Response | Fixed strings from our own constants. Submitted input is never reflected |

`first_seen` and `is_new_install` are write-once. A later ping cannot rewrite an
install's cohort or move it into the new-install population.

### `GET /v1/aggregates`

Bearer-token protected, compared in constant time. Returns the last 90 daily
snapshots. `503` rather than an open response when no token is configured, so a
misconfiguration fails closed.

Pulled by `pixlstash-metrics`, never pushed. A push design would mean storing a
long-lived GitHub write token in Cloudflare; this way the Worker holds no
credential that can write to anything of ours.

## How the counting works

**The activity bitmap.** Eight bytes per install, no event log. Bit N set means
the install pinged N days before `last_seen`. The window is 63 bits, not 64,
because SQLite `INTEGER` is signed.

It is shifted **lazily on write**, not swept nightly by the cron. A sweep that
misses a day or runs twice silently corrupts every row with no way to detect it
afterwards; lazy shifting is idempotent and self-correcting.

**Compute daily, never backfill.** A life-week cell is only answerable while its
bits are still inside the 63-day window, so each day's cells are computed while
they exist and stored immutably in `aggregate_snapshot`. A month of missed crons
is a month of permanently missing cells, not a month to be reconstructed later.

**Weekly buckets, not daily points.** Only Docker installs ping every day.
Desktop and pip installs ping when someone runs them, so a weekend-only user
misses an exact day-7 check and would read as churned.

**Resurrection rate** is the metric that answers pause-versus-churn directly: a
silence of 14 days or more that a later ping closes. `first_seen`/`last_seen`
alone give a decay curve and cannot distinguish the two.

## Poisoning

Anyone can POST fabricated UUIDs. That is inherent to an unauthenticated
endpoint and is mitigated, not eliminated:

- Only canonical UUIDv4 is accepted, so ids cannot be trivially enumerated
- Per-IP rate limiting bounds volume from one source
- Cohorts below `MIN_COHORT` (20) are **suppressed rather than published**, so a
  handful of fabricated ids cannot move a published cell in an otherwise empty
  week. `suppressed_cohorts` reports how many were withheld, so a suppressed
  cell is never mistaken for an empty one
- `resurrection_rate` is `null`, not `0`, when nothing is eligible yet

Published numbers are a **floor, not a census**, and opt-in cohorts read better
than reality because people who opt in are more engaged. Both caveats belong in
the metrics README when the numbers first appear.

## Development

```sh
npm test          # 50 tests, no install required beyond Node
npm run dev       # wrangler dev, needs the D1 binding
```

Tests run against an in-memory D1 stub that recognises only the statements this
Worker actually issues, and throws on anything else. A new query cannot pass
tests without the stub being taught about it first.
