# Security review: v1.9 telemetry lane (`feat/telemetry-install-id`)

**Date:** 2026-08-02
**Scope:** the Cloudflare Worker ingestion endpoint, install-ID storage and the
sender, the two new API routes, the Cloudflare credential and deploy workflow,
and the `pixlstash-metrics` collector changes on `feat/install-cohort-metrics`.

> ## This review does NOT satisfy the independent sign-off requirement
>
> CLAUDE.md is explicit: *"The author of a security fix must not be the one who
> certifies it complete. Spawn a separate reviewer/board tasked to refute."* That
> rule exists because a BOLA audit here once shipped a fix that closed four
> endpoints and left three siblings of the same severity open.
>
> I wrote every line under review. This is a self-review, and it is worth exactly
> what a self-review is worth. **An independent adversarial pass is still
> required before any of this faces the internet.** Treat what follows as input
> to that reviewer, not as a clearance.

---

## Assets, actors, trust boundaries

| | |
|---|---|
| **New asset** | One row per install in Cloudflare D1: a random UUIDv4, two dates, a 63-bit bitmap, a new-install flag, an install-type bucket |
| **New actor** | Anyone on the public internet. This is the first endpoint in the product's history that accepts unauthenticated writes |
| **New boundary** | `t.pixlstash.dev/v1/*`, answered by a Worker at Cloudflare's edge. The GitHub Pages origin is never reached |
| **New credential** | A Cloudflare API token with Workers + D1 edit scope, needed by a deploy workflow that does not exist yet |
| **Unchanged** | The library, `vault.db`, snapshots, and every existing route. Nothing in this lane reads or transmits library content |

---

## Findings, worst first

### 1. HIGH — Any website could make its visitors POST fabricated pings (FIXED)

**Location:** `website/telemetry-worker/src/index.js`, `handlePing` / `readCappedBody`

**Exploit:** the endpoint parsed any body regardless of `Content-Type`. A POST
carrying `text/plain` is a CORS **simple request**: no preflight, so the browser
sends it. Any site could embed

```js
fetch("https://t.pixlstash.dev/v1/ping", {
  method: "POST", mode: "no-cors",
  headers: { "Content-Type": "text/plain" },
  body: '{"install_id":"<random v4>","is_new_install":true,"install_type":"pip"}',
});
```

and every visitor would silently write a fabricated install from **their own IP**.
Per-IP rate limiting is no defence: each visitor is a different IP. A moderately
trafficked page manufactures thousands of installs an hour, which both poisons
published cohorts above the `MIN_COHORT` floor and grows the D1 table without
bound. CWE-352 in shape; OWASP A01.

**Fix:** require `Content-Type: application/json`, which is not CORS-safelisted
and therefore forces a preflight. The Worker returns no
`Access-Control-Allow-Origin`, so the preflight fails and the browser never sends
the POST. The server-side sender already sets the header.

**Verification:** `test/worker.test.js` refuses `text/plain`,
`application/x-www-form-urlencoded`, `multipart/form-data` and an absent header
with 415 and writes nothing; `application/json; charset=utf-8` still succeeds.

### 2. MEDIUM — Rate limiting failed open on a misconfigured deploy (FIXED)

**Location:** `website/telemetry-worker/src/index.js`, `if (env.RATE_LIMITER)`

**Exploit:** the limiter was applied only when the binding existed. A deploy that
dropped or renamed the `ratelimits` block would run the only unauthenticated
write endpoint we operate with **no volume control at all**, and nothing in any
response would reveal it. CWE-703.

**Fix:** absent binding now returns 503 and logs an error. Fails closed.

**Verification:** `fails closed when the rate limiter binding is missing` asserts
503 and an empty table.

### 3. MEDIUM — Unbounded row growth in D1 (OPEN, needs a decision)

**Location:** `website/telemetry-worker/src/index.js`, `recordPing`

**Exploit:** nothing caps how many distinct install rows can be created. With
finding 1 fixed, an attacker is down to direct requests at 20/min/IP, which is
still 28,800 rows/day per IP and trivially parallelised across hosts. D1's free
tier is 5 GB; at roughly 80 bytes/row that is tens of millions of rows before
storage fails, but the aggregation pass loads **the whole table into one Worker
invocation**, which will OOM long before that.

**Recommended fix:** a per-day cap on *new* install creation, plus a hard row
ceiling above which inserts are refused and an alert fires. Existing installs
must keep updating when the cap is hit, so the cap applies to INSERT only, never
UPDATE, or an attacker could deny service to real installs.

**Why not fixed here:** it needs a threshold chosen against expected growth,
which is a product decision, and the aggregation pass needs a paging strategy to
match. Both belong with the independent reviewer.

### 4. LOW — Oversized body is buffered before the cap is enforced (OPEN)

**Location:** `readCappedBody`

**Exploit:** `Content-Length` is checked first, but it is attacker-controlled and
may be absent on a chunked request. `await request.arrayBuffer()` then buffers
the whole body before the length check. Cloudflare caps request bodies at 100 MB
on the free plan and a Worker has 128 MB of memory, so a single crafted request
can plausibly OOM the isolate.

**Recommended fix:** read through `request.body.getReader()` and abort once
`MAX_BODY_BYTES` is exceeded, rather than materialising the buffer.

**Residual:** low. Cloudflare kills the isolate and the next request gets a fresh
one; there is no data-integrity impact.

### 5. LOW — The collector persists unvalidated remote data (OPEN)

**Location:** `pixlstash-metrics/scripts/collect_metrics.py`, `fetch_install_cohorts`

**Exploit:** `{key: snapshot.get(key) for key in COHORT_KEYS}` bounds the *keys*
but not the *values*. Whatever the Worker returns for those keys is written
verbatim into `history.json`, which is committed to a permanent, append-only git
history and rendered by `generate_plots.py`.

This is inconsistent with the repo's own stated discipline three sections above
it: *"Input is bounded before anything is persisted."* Version strings get a
charset and a length cap; cohort values get nothing. The Worker is ours, so this
is defence-in-depth rather than a live hole, but the whole point of that rule is
that it does not depend on the source being trustworthy.

**Recommended fix:** bound the values the same way: numerics in range, cohort
keys matching an ISO-date pattern, and a cap on the number of cohort entries and
retention cells.

### 6. INFO — Poisoning above the suppression floor remains possible

`MIN_COHORT = 20` stops a handful of fabricated ids from moving a published
cell, but an attacker who creates 20+ fabricated new-installs in one ISO week
gets that cohort published. This is inherent to an unauthenticated endpoint and
is why the metrics README carries "a floor, not a census". Recorded so the
independent reviewer does not have to rediscover it.

---

## What I checked and found clean

- **Secrets:** no credential in the diff or in the tree. `AGGREGATES_TOKEN` is
  set via `wrangler secret put` and is deliberately absent from `wrangler.toml`;
  `TELEMETRY_AGGREGATE_TOKEN` is a repo secret referenced by name only. The only
  UUID-shaped literals are the documented sample ID.
- **Authorization on the two new routes:** both `OWNER_ONLY`, declared in
  `ROUTE_POLICIES`, covered by the CI completeness guardrail, and tested in both
  directions (out-of-scope 403 **and** in-scope 200) in
  `tests/test_telemetry_install_id_authz.py`. A resource-scoped share token
  cannot read the install ID, which is the correct call: it is a stable
  installation identifier and would let a link-holder correlate visits.
- **No inline authz check was added.** The gate owns it, per §16.2.
- **Aggregates endpoint:** bearer token compared in constant time, 503 rather
  than an open response when unconfigured, so a misconfiguration fails closed.
- **Response hygiene:** 204 with no body on success; error details are fixed
  strings from our own constants, never reflected input. A test asserts a
  submitted `<script>` key is not echoed.
- **Install-ID file permissions:** `write_json_atomic` stages through
  `tempfile.mkstemp`, which creates the file 0600 before `os.replace`, so the
  identifier is not world-readable.
- **Sender:** HTTPS only, 10s timeout, no retry, never raises, daemon thread so
  it cannot delay shutdown. Suppressed under `PYTEST_CURRENT_TEST`, `CI` and
  `PIXLSTASH_DEMO_MODE`, so our own infrastructure cannot manufacture installs in
  the cohorts this measures.
- **No library data on any path.** The payload is three fields; the ping carries
  no version, no timestamp, no path, no hostname.
- **Prune correctness:** `last_seen < cutoff` compares `YYYY-MM-DD` strings,
  where lexicographic order is chronological.

---

## Release blockers

1. **The independent adversarial review has not happened.** This document is by
   the author. Nothing here ships until someone else has tried to refute it.
2. **Finding 3 (unbounded row growth)** needs a decision and a fix before the
   endpoint is public.
3. **The deploy workflow does not exist yet.** The Cloudflare API token it will
   need is the first Cloudflare *write* credential in this repo, and it must be
   scoped to Workers + D1 edit on the one zone, never an account-wide key. That
   workflow is itself in scope for the review that has not happened.
4. **The D1 region is a one-shot, irreversible decision.** Rows are a persistent
   identifier tied to dates, so they are personal data under GDPR. Use the
   **jurisdiction** constraint, which is binding, not the `--location` hint,
   which Cloudflare documents as best-effort. Neither can be changed after
   creation.

## Accepted risks

| Risk | Blast radius | Compensating control | Owner | Revisit |
|---|---|---|---|---|
| Fabricated pings above the `MIN_COHORT` floor | Published cohort numbers skew | Suppression floor, per-IP limit, "a floor, not a census" in the README | Gaute | 2026-10-01 growth gate |
| `recordPing` read-modify-write race | One lost activity bit on one install | Volume makes it near-impossible; cost is one missing day | Gaute | If volume exceeds ~10 pings/sec |

## Hardening that can wait

- Findings 4 and 5.
- Consider whether `install_type` should be write-once like `first_seen`. Today
  a ping can change it, which is correct for a genuine pip-to-docker migration
  but also means anyone holding an install ID could flip that field. Guessing a
  UUIDv4 is not feasible, so this is theoretical.
