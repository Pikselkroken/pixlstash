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
security fixes. The level should reflect the CVSS score of the most severe issue:

| Level    | CVSS range |
|----------|------------|
| Critical | 9.0 – 10.0 |
| High     | 7.0 – 8.9  |
| Moderate | 4.0 – 6.9  |
| Low      | 0.1 – 3.9  |

The CI build reads this tag from the changelog and publishes it in
`latest-version.json` so that running instances can warn users when they need to
upgrade for security reasons.

---

## Thank You

PixlStash is an open project, and contributions of all kinds, including bug reports, code, docs, ideas,
and feedback, are appreciated. Thanks for helping make it better!