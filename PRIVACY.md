# PixlStash Privacy Policy

_Last updated: 2026-08-02_

PixlStash is a self-hosted application for managing your own picture and video
library. It is private by design: your content stays on hardware you control.
This policy explains exactly what PixlStash does and does not do with your data,
and the two optional features that make a network request to us.

## Your library stays yours

Everything you import into PixlStash, meaning your images and videos and everything
PixlStash derives from them (thumbnails, tags, captions, face data, search
embeddings, ratings, and other metadata), is stored **locally**, in the library
folder and database on the machine where you run PixlStash.

PixlStash does **not** upload, transmit, or send this content anywhere. There is
no PixlStash account, no cloud sync, and no central server that receives your
library. We cannot see your library, because it never reaches us. It stays under
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
"Check for updates" in Settings → Privacy.** Until you opt in, no update check
is ever made.

When enabled, the app contacts `https://pixlstash.dev` at most once every 24 hours
(it may check more often only when the most recent known release was a high- or
critical-severity **security** fix, so you are warned promptly). Each check sends
two pieces of information, both in the request URL:

1. **Your current app version**, and
2. **Your installation type**, as a coarse category: `docker`, `pip`,
   `electron` (the desktop app), or `other`.

As with any request to any website, the receiving server and its content-delivery
network (Cloudflare) also see ordinary request information such as your IP address,
the time of the request, and your app/browser user-agent. **No part of your
library, no login, and no personal identifier is included.**

We use this information to:

- tell you whether an update is available, show you a changelog relevant to your
  version, and direct you to the correct way to update for your installation type
  including flagging security-relevant updates; and
- produce a rough, **aggregate** lower-bound estimate of the number of daily active
  installations.

We use these values to produce aggregate counts only. We do not build a profile of
you, and we do not use the IP address to identify you. If you later click the
"update available" link, the upgrade page you are taken to receives your version
and installation type so it can show you the right upgrade instructions for your
install.

You can turn the update check off again at any time in Settings → Privacy; once
disabled, no further checks are made.

## The anonymous install ID (off until you turn it on)

PixlStash generates a random identifier for your installation the first time it
starts, and stores it in a file called `install-id.json` next to your
`server-config.json`. It is **off** on every installation and every install type,
and upgrading from an earlier version leaves it off. Nothing is sent unless you
turn on **"Send an anonymous install ID"** in Settings → Privacy.

It exists to answer one question that plain visit counts cannot: whether people
keep using PixlStash, or install it once and stop. Without it we can count
requests but cannot tell ten people using PixlStash once from one person using it
ten times.

When you turn it on, PixlStash sends once a day, at most:

```
{
  "install_id": "9f2c1b7e-4d5a-4c81-b3e6-8a7d2f0e5c14",
  "is_new_install": true,
  "install_type": "pip"
}
```

That is the whole message. `is_new_install` is true only when install-ID
telemetry was enabled as part of the first consent decision on a fresh install.
It is false for upgrades and for people who decline first and opt in later, so
people who have used PixlStash for months are not counted as brand new.
`install_type` is the same coarse category as the update check, where `pip` also
covers the Windows server installer.

Two things about how the identifier is made:

- It is random. It is never derived from your MAC address, hostname, machine ID,
  serial number, or any other property of your computer, so it cannot be used to
  recognise your machine anywhere else.
- The file records the date it was created, not the time, because a precise
  creation moment would itself be close to unique.

Settings has a **Recreate ID** button. It replaces the identifier with a new
random one; the old one is overwritten, is never sent again, and nothing on disk
links the two.

**About your IP address.** As with any request to any website, the server that
answers it and its content-delivery provider (Cloudflare) see the address it came
from, and Cloudflare logs it under its own retention. That is outside our
control and true of every request to any site.

What is in our control: **we never write your IP address into our own store, and
it is never attached to your install ID.** Our code reads it for one purpose,
only while the request is being handled, which is refusing floods from a single
source so fabricated data cannot swamp the counts. Nothing links the address a
request came from to the identifier it carried.

We store your install ID, the dates we first and last heard from it, and a
compact record of which days it was active. Nothing else. That record is deleted
after 400 days of silence, and only aggregate counts are ever published.

## Software downloads from third parties

To do its work, PixlStash downloads AI model weights (for tagging, captioning, and
search) and, in the desktop app when you add GPU acceleration, Python packages.
These are fetched on demand from third-party services such as Hugging Face, the
Python Package Index (PyPI), and the PyTorch download index. Those requests go
directly to those providers and are subject to **their** privacy policies;
PixlStash includes only what is needed to download the files (standard request
information) and **never** your library content.

## The PixlStash website and public demo

Visiting `https://pixlstash.dev` (including any public demo) is an ordinary website
visit, subject to standard web-server and CDN request logging (such as IP address,
request time, and user-agent). Any content you upload to a **public demo** instance
is not private, so please do not upload anything sensitive to a shared demo.

## Changes to this policy

We may update this policy as PixlStash evolves. Material changes will be reflected
here with an updated "Last updated" date above.

## Contact

Questions about privacy or this policy: email
[lindkvis@gmail.com](mailto:lindkvis@gmail.com), or open an issue at
<https://github.com/pikselkroken/pixlstash/issues>.
