# Contributing to PixlStash

Thanks for your interest in contributing! PixlStash is split into two parts with
different licenses and contribution requirements. This document explains how to
contribute safely and easily.

For a repository-wide licensing map, see `docs/licensing.md`.

---

## Project Structure

PixlStash consists of two main components:

- **Backend (`pixlstash/`)**  
  - License: GPL-3.0  
  - Contributions require agreeing to the CLA (see below)

- **Frontend (`frontend/`)**  
  - License: MIT  
  - No CLA required

Please make sure your contributions follow the rules for the part of the project
you are modifying.

---

## Contributing to the Backend (GPL-3.0)

The backend is licensed under GPL-3.0 and powers the core functionality of
PixlStash. To keep the project legally clean and allow the maintainer to build
optional commercial plugins and extensions, contributions to the backend require
agreement to the **Contributor License Agreement (CLA)**.

### CLA Scope (Path-based)

The backend CLA applies only to contributions that modify files under:

- `pixlstash/**`

No CLA is required for contributions that only modify files outside `pixlstash/**`,
including frontend, documentation, CI/workflow files, scripts, tests, and website
assets.

### Backend CLA

Before submitting a pull request that modifies the backend, you must read and
agree to the CLA located at: `pixlstash/CLA.md`

By opening a pull request that touches backend code, you indicate your acceptance
of the CLA terms.

PRs that touch `pixlstash/**` must include this acknowledgment in the PR
description:

- [x] I have read and agree to the CLA in `pixlstash/CLA.md`.

If your PR only affects files outside `pixlstash/**`, no CLA is required.

The CLA lets you retain full copyright and confirms that backend contributions do
not prevent the maintainer from creating commercial plugins and extensions as
independent works that interoperate with the PixlStash backend.

**It does not give the PixlStash owner the right to create closed source forks of your contributions**.

---

## Contributing to the Frontend (MIT)

The frontend is licensed under the MIT License. This means:

- You do **not** need to sign a CLA  
- You are free to fork, modify, and reuse the UI  
- Contributions are welcome without extra steps

Just open a pull request and follow normal GitHub etiquette.

---

## Pull Request Guidelines

To help keep the project maintainable:

1. Keep PRs focused and scoped to a single change when possible.
2. Include a clear description of what the PR does and why.
3. For backend PRs, confirm that you have read and agree to the CLA.
4. Ensure code is formatted consistently with the existing style.
5. Add tests when appropriate.
6. Be respectful and constructive in discussions.

---

## Reporting Issues

If you find a bug or have a feature request:

- Search existing issues first  
- Open a new issue if needed  
- Provide clear steps to reproduce or a detailed description of the idea  

---

## Handling Security Vulnerabilities

### Reporting a vulnerability (external reporters)

Please use the **"Report a vulnerability"** button on the
[Security tab](https://github.com/pikselkroken/pixlstash/security) of the GitHub
repository. This opens a private advisory draft visible only to you and the
maintainer — do **not** open a public issue for security vulnerabilities.

### Fixing a vulnerability (maintainer workflow)

Follow coordinated disclosure so that a fix is available before the vulnerability
is public knowledge:

1. **Open a private advisory draft** — use the "Report a vulnerability" button on
   your own repository. This keeps all discussion private until you choose to
   publish.
2. **Request a CVE ID** from within the advisory UI (GitHub is a CNA — typically
   granted within a day). You can do this before the fix is ready.
3. **Prepare the fix** — use the temporary private fork GitHub can create for you
   from within the advisory, or work locally. Do not push to a public branch until
   the release is ready.
4. **Land the fix and tag the release** — merge the fix, update `CHANGELOG.md`
   with a `[Security: LEVEL]` tag on the version header (see the Changelog
   Convention section below), and publish the release on GitHub/PyPI.
5. **Publish the advisory** — only after the fixed release is live. This makes the
   GHSA public, activates the CVE, triggers Dependabot alerts for downstream
   users, and pushes the advisory to osv.dev and the PyPI advisory feeds.

> Publishing the advisory *before* the release would announce a vulnerability with
> no fix available. Always land the release first.

---

## Changelog Convention

When adding a new entry to `CHANGELOG.md`, use this format for the version header:

```
# [VERSION]
```

or, if the release contains a security fix:

```
# [VERSION] [Security: LEVEL]
```

where `LEVEL` is one of: `Critical`, `High`, `Moderate`, `Low`.

Use the **highest** severity level present in that release if there are multiple
security fixes. Start from the CVSS score of the most severe issue:

| Level    | CVSS range |
|----------|------------|
| Critical | 9.0 – 10.0 |
| High     | 7.0 – 8.9  |
| Moderate | 4.0 – 6.9  |
| Low      | 0.1 – 3.9  |

### Rate the impact on PixlStash, not the upstream score

A published CVSS score describes the worst case for *everyone* who uses the
affected code. What we tag is what an installed PixlStash is actually exposed to,
so adjust the starting level for how the vulnerable code is reached here:

- **Not shipped to users** — a build- or test-only dependency (anything marked
  `"dev": true` in a lockfile, e.g. `node-gyp`, `jsdom`, `@electron/get`) is not
  part of the app anyone runs. Updating it protects no installed version, so the
  release gets **no tag**. Still record the update as a normal changelog entry and
  say in that entry why it carries no tag.
- **Shipped but unreachable** — the dependency ships, but nothing in PixlStash can
  reach the vulnerable code path. Downgrade, and state in the entry which path is
  and is not reachable.
- **Reachable but constrained** — exploiting it needs a precondition most installs
  do not meet (an off-by-default setting, an already-authenticated attacker, a
  specific filter combination). Downgrade at most one level and describe the
  precondition, so users can tell whether it applies to them.

**When in doubt, tag the higher level.** Downgrade only when you can name the
reason in the changelog entry. If the reachability argument takes more than a
sentence, or nobody has traced the call path, treat it as reachable and tag it at
its upstream severity — under-warning is the expensive mistake, and a release
tagged more cautiously than it needed to be costs users nothing but an update.

Note that this cuts both ways: a low upstream score can warrant a **higher** tag
when PixlStash uses the weak component for something load-bearing, or when the
fix forces user action (reissued tokens, invalidated share links).

### What the tag does

The CI build reads this tag from the changelog and publishes it in
`latest-version.json` so that running instances can warn users when they need to
upgrade for security reasons. Two thresholds matter in
`frontend/src/composables/useVersionCheck.js`:

- **Any level** turns the in-app update badge from "available" into
  "security ⚠️" and tells the user to update as soon as possible. `Low` and
  `Moderate` produce an identical warning — only the wording of the tooltip
  differs. The meaningful choice is therefore *tag or no tag*, not `Low` vs
  `Moderate`.
- **`Critical` and `High`** make the check stop throttling, so the prompt
  reappears on every page load instead of once a day. Note what this does *not*
  do: the throttle is keyed on the level stored from the install's **previous**
  check, so no tag reaches an instance any faster than any other. Every level,
  including no tag at all, takes up to 24 hours to surface. Escalating to
  `Critical` buys persistence after that first check, not speed.

Reserve `Critical` and `High` for releases where continuing to run the old
version is genuinely unsafe. The stored level is sticky: it is written on every
successful check regardless of whether the install is out of date, so a
`Critical` release keeps *every* install — including ones that already updated —
checking on every page load until a later release publishes without a
`Critical`/`High` tag.

None of this is a push channel. If a vulnerability is severe enough that waiting
up to a day for the next check is unacceptable, the tag is not the mechanism —
post the advisory and tell people directly.

Tagging a release users are not exposed to spends the same attention as a real
one, and the warning only works for the next 1.8.1 or 1.8.3 if it has not been
worn out on build tooling.

---

## Demo Site Deployment (Fly.io)

The demo site at `demo.pixlstash.dev` runs as a self-contained, read-only Docker
image on Fly.io. No persistent volume is used — the database and images are baked
into the image at build time.

### Prerequisites

- [flyctl](https://fly.io/docs/hands-on/install-flyctl/) installed and authenticated
- Docker installed locally
- A curated `demo-data/` directory alongside the repo root (never committed — it
  is `.gitignore`d):

```
demo-data/
  server-config.json   # set image_root to the local absolute path of demo-data/images/
  images/              # vault.db + all picture files
```

### Building the demo database interactively

1. Create `demo-data/server-config.json` with `image_root` pointing at your local
   `demo-data/images/` path, e.g.:
   ```json
   {
     "host": "127.0.0.1",
     "port": 9537,
     "image_root": "/home/you/Projects/pixlstash/demo-data/images",
     "require_ssl": false,
     "cookie_samesite": "Lax",
     "cookie_secure": false,
     "disable_password_auth": false
   }
   ```
2. Launch pixlstash against that config:
   ```bash
   python -m pixlstash.app --server-config demo-data/server-config.json
   ```
3. Log in, import pictures, create sets/characters, let tagging and scoring run.
4. Create a read-only token (Settings → Tokens → scope: READ, no resource restriction).
   Note the token value — it becomes the `?token=` in the public URL.
5. Set `"disable_password_auth": true` in `demo-data/server-config.json` so nobody
   can log in via username/password once deployed.

### Building and deploying the image

The `Dockerfile.demo` build automatically rewrites `image_root` to the
in-container path `/home/pixlstash/images`, so you do not need to edit
`server-config.json` before building.

```bash
# Build the image locally
docker build -f Dockerfile.demo -t registry.fly.io/pixlstash-demo:latest .

# Authenticate Docker with Fly and push
flyctl auth docker
docker push registry.fly.io/pixlstash-demo:latest

# Deploy — fly.toml picks up the app name and config automatically
flyctl deploy --image registry.fly.io/pixlstash-demo:latest
```

### Sharing the demo

Link visitors directly to:

```
https://demo.pixlstash.dev?token=<READ_TOKEN>
```

Obtain the current token value from the Fly.io secret or from the demo
`server-config.json` in the private deployment repository.

The frontend picks up the token on load and authenticates all API calls
automatically. No login screen is shown.

> **Note:** This is a read-only token baked into the public URL — it is not a
> secret. Anyone visiting the demo site will see it in their address bar.

### Refreshing the demo content

Repeat the interactive database building steps above, then rebuild and redeploy
the image. The old machine is replaced in-place with no downtime window needed.

---

## Thank You

PixlStash is an open project, and contributions of all kinds, including bug reports, code, docs, ideas,
and feedback, are appreciated. Thanks for helping make it better!