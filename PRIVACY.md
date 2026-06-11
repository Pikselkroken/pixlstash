# PixlStash Privacy Policy

_Last updated: 2026-06-11_

PixlStash is a self-hosted application for managing your own picture and video
library. It is private by design: your content stays on hardware you control.
This policy explains exactly what PixlStash does and does not do with your data,
and the one optional feature that makes a network request to us.

## Your library stays yours

Everything you import into PixlStash — your images and videos, and everything
PixlStash derives from them (thumbnails, tags, captions, face data, search
embeddings, ratings, and other metadata) — is stored **locally**, in the library
folder and database on the machine where you run PixlStash.

PixlStash does **not** upload, transmit, or send this content anywhere. There is
no PixlStash account, no cloud sync, and no central server that receives your
library. We cannot see your library, because it never reaches us — it stays under
your control on your own hardware.

## Sharing is explicit and user-controlled

PixlStash includes an optional sharing feature. Content is shared **only** when
you choose to share it, **only** with the people you share it with, and **only**
within the scope you select (specific images, sets, characters, or projects).
Nothing is shared automatically or by default, and you can revoke a share at any
time.

## Optional update check (off until you turn it on)

PixlStash can check whether a newer version is available. This is the only feature
that contacts a PixlStash-operated server, and it is **off unless you enable
"Check for updates" in Settings → Behaviour.** Until you opt in, no update check
is ever made.

When enabled, the app contacts `https://pixlstash.dev` at most once every 24 hours
(it may check more often only when the most recent known release was a high- or
critical-severity **security** fix, so you are warned promptly). Each check sends
two pieces of information, both in the request URL:

1. **Your current app version**, and
2. **Your installation type**, as a coarse category — `docker`, `pip`,
   `electron` (the desktop app), or `other`.

As with any request to any website, the receiving server and its content-delivery
network (Cloudflare) also see ordinary request information such as your IP address,
the time of the request, and your app/browser user-agent. **No part of your
library, no login, and no personal identifier is included.**

We use this information to:

- tell you whether an update is available, show you a changelog relevant to your
  version, and direct you to the correct way to update for your installation type
  — including flagging security-relevant updates; and
- produce a rough, **aggregate** lower-bound estimate of the number of daily active
  installations.

We use these values to produce aggregate counts only. We do not build a profile of
you, and we do not use the IP address to identify you. If you later click the
"update available" link, the upgrade page you are taken to receives your version
and installation type so it can show you the right upgrade instructions for your
install.

You can turn the update check off again at any time in Settings → Behaviour; once
disabled, no further checks are made.

## Software downloads from third parties

To do its work, PixlStash downloads AI model weights (for tagging, captioning, and
search) and — in the desktop app, when you add GPU acceleration — Python packages.
These are fetched on demand from third-party services such as Hugging Face, the
Python Package Index (PyPI), and the PyTorch download index. Those requests go
directly to those providers and are subject to **their** privacy policies;
PixlStash includes only what is needed to download the files (standard request
information) and **never** your library content.

## The PixlStash website and public demo

Visiting `https://pixlstash.dev` (including any public demo) is an ordinary website
visit, subject to standard web-server and CDN request logging (such as IP address,
request time, and user-agent). Any content you upload to a **public demo** instance
is not private — please do not upload anything sensitive to a shared demo.

## Changes to this policy

We may update this policy as PixlStash evolves. Material changes will be reflected
here with an updated "Last updated" date above.

## Contact

Questions about privacy or this policy: email
[lindkvis@gmail.com](mailto:lindkvis@gmail.com), or open an issue at
<https://github.com/pikselkroken/pixlstash/issues>.
