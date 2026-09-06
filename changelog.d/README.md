# `changelog.d/` — one file per user-visible change

`CHANGELOG.md` was a conflict magnet: every branch appended to the same few
lines at the top of the same file, so any two open PRs collided there even when
they touched nothing else in common, and the collision came back on every
re-merge of `develop`. Nothing about those conflicts was ever a real
disagreement about the product.

So nothing writes `CHANGELOG.md` any more except a release. A change that
deserves a changelog line drops a file here instead; `scripts/assemble_changelog.py`
folds them into one version section when the release is cut, and deletes them.
Two branches adding two files conflict in nothing.

## Whether to write one at all

**A fragment is for a user-visible change — the thing you would want to read in
the release notes of an app you use.** A new capability, a changed default, a
control that moved, a bug that a released version actually shows you.

**Not for anything that only existed inside this development cycle.** A bug
introduced and fixed before it ever shipped never happened as far as a user is
concerned; a refactor, a test fix, a CI repair, a doc edit and a typo in a
string nobody released are all the same. Writing those up makes the release
notes longer and less true. Most PRs need no fragment, and that is the normal
case, not an oversight — nothing fails if a branch adds none.

If the change fixes something a released version does, it is user-visible even
when the code is a one-liner. If it fixes something last week's branch broke,
it is not.

## Writing one

One file per change, named for its branch or PR number so two of them never
collide:

```
changelog.d/library-root-scan.md
changelog.d/1166.md
```

The file is the changelog entry itself: markdown list items, exactly as they
should read in the release notes, wrapped at 80 columns. No heading, no
version, no date — the release supplies those.

```markdown
- PixlStash now watches your library folder itself, not only the folders you
  point it at. Rename a picture in your file manager and it keeps its tags,
  score, people and sets.
```

Write it in the product's voice, for someone who does not know the code: what
they can now do, in their words. `- Fixed: ...` is the convention for a fix.
Several related lines can share one file; unrelated changes want separate ones.

## Cutting a release

```
python scripts/assemble_changelog.py 1.11.1
python scripts/assemble_changelog.py 1.11.1 --security High
```

That prepends one `# [1.11.1]` section to `CHANGELOG.md` with every fragment's
lines under it, then removes the fragments. Read the assembled section before
committing it — ordering is by filename, so a release worth reading usually
wants a hand-edit afterwards to group the entries and lead with the ones that
matter. `--security` writes the `[Security: LEVEL]` tag the release workflow
reads off the top heading to publish `latest-version.json`.
