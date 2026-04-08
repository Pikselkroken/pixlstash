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
agree to the CLA located at: pixlstash/CLA.md

By opening a pull request that touches backend code, you indicate your acceptance
of the CLA terms.

PRs that touch `pixlstash/**` must include this acknowledgment in the PR
description:

- [x] I have read and agree to the CLA in `pixlstash/CLA.md`.

If your PR only affects files outside `pixlstash/**`, no CLA is required.

The CLA lets you retain full copyright and confirms that backend contributions do
not prevent the maintainer from creating commercial plugins and extensions as
independent works that interoperate with the PixlStash backend.

**It does not give PixlStash owner the right to create closed source forks of your contributions**.

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

## Thank You

PixlStash is an open project, and contributions of all kinds, including bug reports, code, docs, ideas,
and feedback, are appreciated. Thanks for helping make it better!