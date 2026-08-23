# Telemetry ingestion Worker

Receives anonymous install pings and publishes aggregate cohort numbers. This is
the first PixlStash endpoint that accepts unauthenticated writes from the public
internet, so it is built as an attack surface rather than as plumbing.

**Production:** deployed at `https://t.pixlstash.dev` on Cloudflare Workers. It
uses an EU-jurisdiction D1 database, the `RATE_LIMITER` binding, and a
five-minute scheduled trigger. `GET /v1/aggregates` is protected by
`AGGREGATES_TOKEN`, which
is mirrored as the `TELEMETRY_AGGREGATE_TOKEN` Actions secret in the private
`pixlstash-metrics` repository.

## Where the data lives

Three places, and only the first two ever hold an identifier.

| Where | What | Retention |
|---|---|---|
| The user's own machine | `install-id.json` beside `server-config.json`: one random UUIDv4 | Until the user hits Recreate, or deletes it |
| Cloudflare D1 | One row per install: the UUID, first-seen and last-seen **dates**, a 63-bit activity bitmap, a sticky resurrection flag, a new-install flag, one of four install-type buckets | 400 days since last seen, then pruned in bounded scheduled slices |
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

### D1 location

D1's physical location is fixed at creation and **cannot be changed afterwards**.
Recovering from the wrong choice means creating a new database and migrating.

The rows are a persistent identifier tied to first-seen and last-seen dates, so
the production database was created with Cloudflare's binding EU jurisdiction:

```sh
npx wrangler@4.118.0 d1 create pixlstash-telemetry \
  --jurisdiction=eu --binding=DB --update-config
```

`--jurisdiction=eu` is a binding compliance constraint, not the best-effort
latency hint provided by `--location`. It is creation-only. The command writes
the real database UUID into the local `wrangler.jsonc`; never commit that
environment-specific edit. The deploy script disables automatic provisioning,
so a missing UUID fails closed instead of silently creating an unconstrained
database.

Capture the complete create-command output in the release ticket, then attach
the D1 dashboard's database details showing the EU jurisdiction and database
UUID. Record the reviewer, UTC time, Wrangler version, and the schema command's
successful output. This is the release evidence for the one-shot placement
decision; do not rely on a prose assertion made before creation.

Apply the schema and deploy from this directory:

```sh
npx wrangler@4.118.0 d1 execute pixlstash-telemetry \
  --file=./schema.sql --remote
npm run deploy
npx wrangler secret put AGGREGATES_TOKEN
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

**The activity bitmap.** One fixed-width 17-character field per install, no
event log. Bit N set means the install pinged N days before `last_seen`. The
window is 63 bits. It is encoded as prefixed hexadecimal text because D1's
Workers API cannot bind JavaScript `BigInt` values or return integers above
`Number.MAX_SAFE_INTEGER` without losing precision.

It is shifted **lazily on write**, not swept nightly by the cron. A sweep that
misses a day or runs twice silently corrupts every row with no way to detect it
afterwards; lazy shifting is idempotent and self-correcting.

**Compute daily, never backfill.** A life-week cell is only answerable while its
bits are still inside the 63-day window, so each day's cells are computed while
they exist and stored immutably in `aggregate_snapshot`. The scan and prune are
split into bounded, five-minute scheduled slices. The accumulator and cursor
are checkpointed in D1; the immutable snapshot is committed before pruning can
begin. A failed slice is replayed without dropping rows or double-counting its
durable accumulator. A month of missed triggers is still a month of permanently
missing cells, not a month to be reconstructed later.

**Weekly buckets, not daily points.** Only Docker installs ping every day.
Desktop and pip installs ping when someone runs them, so a weekend-only user
misses an exact day-7 check and would read as churned.

**ID-bearing check-ins per type** (`id_bearing_checkins_by_type`) is the
numerator for the MAU coverage ratio computed in `pixlstash-metrics`. The
denominator there is *all* version check-ins from Cloudflare zone analytics,
which this Worker never sees — it only ever receives the ID-bearing ones. It
does, however, know each one's install type, so it publishes the split and the
metrics side does the division per bucket instead of pooling.

It is derived from the activity bitmap, over the same rolling window and the
same buckets as `active_installs_by_type`, so no request is logged and no new
column is stored. Two consequences for the consumer:

- These are check-in **counts**, not distinct installs. Dividing them by a
  distinct-install denominator is a unit error.
- A bit is a ping *day*. Two pings on one UTC day count once here and twice in a
  raw request count, so this is a floor on requests. The client throttles to one
  check per day (`pixlstash/telemetry/sender.py`), so in practice they agree.
- It is a **rolling total republished every day**, not a daily figure. The
  window is `ACTIVE_WINDOW_DAYS` days *plus the snapshot date itself* — 29 days
  inclusive — because it is deliberately the same window as
  `active_installs_by_type`, whose predicate is
  `daysBetween(last_seen, today) <= ACTIVE_WINDOW_DAYS`. Pairing a numerator
  and a denominator taken over different spans is the error this field exists
  to remove, so the two are kept identical rather than rounded to a tidy 28.
  Consecutive snapshots overlap by 28 of those 29 days, so they must never be
  summed or plotted as a time series — read one snapshot, and divide it by the
  *same* 29 days of zone-analytics check-ins. A rolling total was chosen over a
  per-day count because a missed cron then costs accuracy rather than a
  permanently absent day, which suits a Worker whose slices can be delayed. The
  cost is that it cannot be decomposed back into days.
- `null` rather than an object when a scan resumed from a checkpoint that was
  written before this field existed *and had already folded in rows*: their
  check-ins are unrecoverable and the snapshot is never backfilled, so the day
  is reported as uncountable rather than as a confident undercount. A pre-field
  checkpoint that had scanned nothing yet lost nothing, and still counts.

**Resurrection rate** is the metric that answers pause-versus-churn directly: a
silence of 14 days or more that a later ping closes. `first_seen`/`last_seen`
alone give a decay curve and cannot distinguish the two. Once observed, that
return is stored as a sticky boolean so it remains true after the original gap
ages out of the rolling bitmap.

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
npm ci
npm test               # unit tests plus Miniflare/workerd D1 integration
npm run check:config   # Wrangler schema validation and local dry-run bundle
npm run dev            # Wrangler local development with local D1
```

The D1 integration suite runs the schema and admission/checkpoint SQL in
Miniflare's workerd-backed D1 implementation. This is intentional: a Map stub
cannot reproduce SQLite transactions, `changes()`, `RETURNING`, constraints, or
concurrent admission at the final capacity slot.

## Plan and service limits

This Worker requires **Workers Paid**. Free-plan Cron Triggers have a 10 ms CPU
limit and 50 D1 queries per invocation, which is not a credible envelope for a
population that can grow to five million rows. The tracked `cpu_ms: 30000`
setting makes that requirement explicit. Each five-minute slice remains bounded
to at most 20 two-thousand-row scan pages or five ten-thousand-row prune pages,
so failure recovery and cost are predictable; Paid is a prerequisite, not
permission for an unbounded daily sweep.
