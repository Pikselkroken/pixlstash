# Spike: can PixlStash Views be built out of links?

> **Verdict: yes, with one constraint that changes the design — the *view root's*
> filesystem decides everything, and it is the owner's choice, so a location that
> cannot hold the tree is refused by name instead of being half-written.**
>
> Run 2026-08-23 for v1.11.0 Phase 7, on freshly formatted volumes and three real
> physical drives. Every number below was measured; the two questions that could
> not be measured here are named as such and are answered by making them
> unreachable rather than by predicting them.

## Why this spike existed

Phase 7 publishes sets, people and projects as folders of links. Four objections
were raised against that before any code was written, and all four are real:

1. Windows symlinks need administrator rights or Developer Mode.
2. Hard links work without privilege but cannot span drives, and a library split
   across an internal disk and a NAS is the common case.
3. exFAT has neither. That is most external drives.
4. Dropbox, OneDrive and Drive do not sync links as links.

The instruction was to answer them on real filesystems and cut the phase if they
did not hold up.

## What was measured

`os.symlink` and `os.link` attempted from a directory on each filesystem, to a
file on the same filesystem and to a file on a **different physical drive**.

| View root's filesystem | symlink | hard link | symlink → other drive | hard link → other drive |
|---|---|---|---|---|
| ext4 (drive 1) | OK | OK | **OK** | `EXDEV` |
| ext4 (drive 2, separate NVMe) | OK | OK | **OK** | `EXDEV` |
| NTFS (ntfs-3g) | OK | OK | **OK** | `EXDEV` |
| **exFAT** | `EPERM` | `EPERM` | `EPERM` | `EXDEV` |
| **VFAT** | `EPERM` | `EPERM` | `EPERM` | `EXDEV` |

Semantics, on ext4:

| Behaviour | Result |
|---|---|
| Reading a picture through a relative symlink | OK, byte-identical |
| `rm -rf` of the whole views tree | every original intact |
| Writing through a **hard** link | edits the original (expected) |
| Deleting the original while a **hard** link exists | **the bytes survive under the views folder** and the space is not freed |
| A symlink whose target moved | `lexists` true, `exists` false, `open` → `ENOENT` |
| Creating 50,000 symlinks | **0.46 s** |
| `rmtree` of 50,000 symlinks | **0.34 s** |

## The four objections, answered

**1 — exFAT is the narrowest problem, not the widest.** A symlink is a stored
path, so it points across devices happily: linking from ext4 to a file on a
separate physical drive worked. Link support is therefore a property of the
**view root**, not of the library. A library on an exFAT external drive or a NAS
is fine as long as the tree itself lands somewhere that has links. The failing
case is only *"the owner picked the external drive for the views folder"*, and
that is a choosable location, so the answer is a refusal naming the reason.

**2 — a hard link is not the general fallback.** It fails across devices on every
filesystem tested, and on exFAT/VFAT it fails on the same device too. It is used
only when the view root and the file share a device and symlinks are
unavailable, which is exactly the Windows-without-Developer-Mode case. It also
carries a hazard the design had not accounted for: deleting the original does
**not** free the file while a hard link exists, so a "deleted" picture would live
on invisibly under the views folder. That is stated in the service's docstring
and is the reason symlinks are tried first even where both work.

**3 — Windows was not measurable in this session** and is deliberately not
predicted. `probe_link_support` asks the chosen directory by attempting one link
and removing it, so the answer is measured at the moment it matters, on the
owner's machine, in the folder they picked. `tests/test_views_links.py::
test_this_filesystem_offers_a_link_mode` is that probe running under the suite,
so the gate's existing Windows shards report the real Windows answer on every run
— no extra workflow, no manual.

**4 — cloud sync was not measurable either** (no sync client on the spike
machine), and the design makes the answer irrelevant: `check_views_root` refuses
a folder that is at or under a cloud-sync root, detected by the client's own
in-tree marker (`.dropbox.cache`, `.tmp.driveupload`, …) or by the sync folder's
name. A client that followed the links would upload the whole library again,
which is the exact duplication views exist to avoid, so refusing is right even if
one client turns out to behave. **This is the one refusal in the feature that is
a precaution rather than a measurement, and it is labelled as such in the code**
— it can be wrong in both directions, so it is deliberately narrow: the ancestor
walk stops *below* `$HOME`, and `.dropbox` is not a marker, because the Dropbox
client keeps that one in the home directory rather than in the synced tree and
treating it as a marker refused every path a Dropbox user could pick.

## Two hazards the spike found that were not on the list

**A views tree inside a library root breaks that library's backups.**
`library_backup_service._validate_regular_file` raises `Refusing symlinked
library payload` on any symlink under the library root, so the owner's backups
would fail outright. `check_views_root` refuses that location — and, since v1.11 registers several
libraries from Settings, any *other* registered library's folder too, using the
roots the hub hands the route.

**A views tree inside a reference folder gets indexed as new pictures.**
`os.walk` skips symlinked *directories* by default but lists symlinked *files* in
`files`, and `reference_folder_scan_task` indexes every supported file it finds.
Each picture would be imported a second time under its view path.
`check_views_root` refuses that location, and the scan additionally prunes any
directory carrying the `.pixlstash-views` marker — because a folder can be
registered as a reference folder *after* a tree was published inside it.

## What the verdict changed in the design

- The view root is validated before a byte is written, and each refusal names its
  reason. Half a tree is never written.
- The link mode is probed, not assumed, and reported back to the UI, so the pane
  can say *hard links* where that is what landed.
- A rebuild deletes, so **what** it may delete is decided per entry rather than
  by `shutil.rmtree`, which is not link-aware: a symlink goes, a regular file
  with another hard link elsewhere goes, and anything else stays and is reported
  back. A file whose only copy is in a view folder is the owner's, however it got
  there, and this feature's own copy is what invites them to put it there.
  Claiming a folder with a `.pixlstash-views` marker is the second guard, and it
  is about adoption: a folder that already has content and no marker is refused
  rather than adopted.
- The rebuild is a full re-derive rather than an incremental update, because
  50,000 links cost under half a second in each direction and an incremental path
  is a correctness risk bought for nothing.
- Symlinks are stored **relative** when the view root and the file share a device
  (so the library and its views survive being moved together) and absolute
  otherwise, where a relative path is either impossible — different drive letters
  on Windows — or absurd.

## Reproducing it

The volumes were loopback images formatted with `mkfs.exfat` / `mkfs.vfat` /
`mkfs.ntfs` and mounted through `udisksctl`, which needs no root on a desktop
session. The cross-device cases used two separate NVMe drives already mounted on
the machine. `probe_link_support` is the same three syscalls the table's first
two columns exercise, so re-running the suite on any host reproduces the row for
that host.
