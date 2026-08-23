#!/usr/bin/env python3
"""Keep canvas.json in step with the artboards. Re-runnable."""

import json
import pathlib

p = pathlib.Path(__file__).parent / "canvas.json"
c = json.loads(p.read_text())

# Page 1 reads left to right as the first run actually goes.
LAYOUT = {
    "Welcome.dc.html": ("bringing-in", "1 · First run", 0, 0, 1160, 1520),
    "Empty.dc.html": ("bringing-in", "1a · A new library, before anything is in it",
                      0, 1700, 1160, 900),
    "Main.dc.html": ("bringing-in", "2 · What we found", 1260, 0, 1240, 900),
    "MapTree.dc.html": ("bringing-in", "3 · Name what your folders are", 2600, 0, 1440, 860),
    "Preview.dc.html": ("bringing-in", "4 · Before anything is written", 4140, 0, 1300, 1300),
    "Storage.dc.html": ("living-in", "How your folders are laid out", 0, 0, 1160, 1400),
    "Moves.dc.html": ("living-in", "When you move things yourself", 1260, 0, 1240, 1420),
    "Views.dc.html": ("living-in", "PixlStash Views", 2600, 0, 1300, 960),
    "Insights.dc.html": ("living-in", "About your library", 4000, 0, 1240, 1160),
    "Libraries.dc.html": ("living-in", "Settings > Libraries: attach and detach",
                          5340, 0, 900, 1780),
}

by_file = {a["file"]: a for a in c["artboards"]}
for name, (page, title, x, y, w, h) in LAYOUT.items():
    a = by_file.get(name)
    if a is None:
        a = {"file": name}
        c["artboards"].append(a)
    a.update(page=page, title=title, x=x, y=y, w=w, h=h)

NOTES = {
    "pass-one": (
        "Loop pass 1. Static mockups: the drag interaction gets built and operated by keyboard in "
        "pass 2.\n\n"
        "DIRECTION A IS PICKED. The pattern-formula alternative is dropped and recorded in "
        "DECISIONS.md; the deciding argument is that two folders at the same depth can legitimately "
        "mean two different things, and only the tree can say so per row.\n\n"
        "Every figure and folder name is PLACEHOLDER, except the membership counts behind the "
        "single-valued argument, which were measured on the owner's four real libraries. The "
        "protocol's fixture pack replaces the rest before this is state-complete."
    ),
    "first-run": (
        "TWO OPTIONS AT FIRST RUN, AND THEY ARE FRAMING RATHER THAN MECHANISM. Both lead to the "
        "same folder picker; the folder decides what actually happens. Option 2 goes straight into "
        "artboards 2-4.\n\n"
        "The rule the bottom of the artboard pins down: ask for an empty library, point at a folder "
        "that is not empty, and there are exactly TWO ways forward. Bring them in, or pick a "
        "different folder. \"Start empty in here anyway\" is not offered, because a library that "
        "ignores the pictures in its own folder is a trap: the next scan finds them, new pictures "
        "get filed in among them, and two ideas of one folder is one too many.\n\n"
        "The options exist for comprehension, not to fork the code. A bare folder picker gives a "
        "new user no hint that pointing at an existing library is supported at all, and that "
        "discoverability failure is the leak this whole release is aimed at.\n\n"
        "TELEMETRY IS NOT REDESIGNED HERE. TelemetryConsentDialog already asks on first startup, "
        "with three exclusive options, a live payload preview, a never-sent list, and consent "
        "recorded however the user leaves. It is reproduced on the artboard only to fix the ORDER: "
        "the question first, then the folder.\n\n"
        "Which also corrects an earlier read of the 5 opted-in installs. The prompt is not missing, "
        "so the number is explained by something else: installs predating it, headless and Docker "
        "installs that never load the SPA, or people choosing one of the first two options. Worth "
        "finding out which before anyone touches this dialog, because it is good and the low number "
        "is not evidence against it."
    ),
    "empty-landing": (
        "1a is the other branch's ending, and it is the screen that started all of this. Today it "
        "reads \"No pictures in the database. Add pictures by dragging them here.\" on the first "
        "run of every install: the word database on the first screen, and one route in, the one "
        "that suits a curated library least.\n\n"
        "Three routes now, none of them presented as the official one, and the folder route first. "
        "It also states where new pictures will be filed, so the layout is never a surprise "
        "discovered later in Settings."
    ),
    "living": (
        "THE RULE, settled after the many-to-many problem nearly sank this: a picture moves only "
        "when its folder STOPS BEING TRUE. Not whenever something about it changes.\n\n"
        "Add a second project or a second person and nothing moves, because the folder it is in is "
        "still correct. Remove the one its folder is named after and it moves, because otherwise "
        "the folder lies. Rename a project and the FOLDER is renamed, not 18,000 files moved.\n\n"
        "Three things fall out rather than being designed in. Importing a library moves NOTHING, "
        "because the assignments came from the folders and are true the moment they are written. "
        "Multiplicity stops mattering, because nothing is ever re-derived. And a folder outside the "
        "layout contradicts nothing, so it never moves, which makes dragging a picture somewhere of "
        "your own a permanent override that needs no setting.\n\n"
        "MANAGED LIBRARIES ARE GONE as a concept. A PixlStash folder is just this, starting empty, "
        "on a default of Project / Person or Set. Today's flat libraries need no migration: files "
        "at a library root match no layout, contradict nothing, and stay put.\n\n"
        "The honest cost: the tree is never wrong but it drifts from what you would have picked. "
        "Hence \"Move to match\" as an offered action.\n\n"
        "Forced to build: renaming an entity renames its folder, and the check is debounced or a "
        "remove-then-add becomes two moves via the fallback."
    ),
    "external-moves": (
        "THE MIRROR RULE, and the answer to \"maybe it will have to be a popup\": no, and never per "
        "file. PixlStash changes an assignment only when your move makes it untrue.\n\n"
        "The one judgement call is when a move is ambiguous. Moving OUT of a project's folder cannot "
        "distinguish \"left the project\" from \"refiled the picture\", because a folder holds a "
        "picture once and a project can share it. Resolved on the owner's real data: 91-100% of "
        "assigned pictures have exactly one project or set, so a move is unambiguous for almost all "
        "of them and is applied. The few with several are listed and left alone until asked.\n\n"
        "Shape is the house rule from the 1.9 sweep: batch, propose, act-then-report. A screen on "
        "next start, a sidebar strip a few seconds after the moves stop while running.\n\n"
        "HARD REQUIREMENT: PixlStash must record its own moves, or its writes return through the "
        "watcher as user intent and the two flip each other forever."
    ),
    "consent": (
        "The last screen of the import states the rule in both directions, because it is the moment "
        "the owner agrees to let PixlStash keep the folders.\n\n"
        "The line that matters most is underneath: importing your library moves nothing at all. For "
        "somebody handing over a tree they curated by hand over years, that is the whole pitch, and "
        "under this rule it is simply true rather than a promise."
    ),
}

NOTES["libraries"] = (
    "DRAWN AS THE DIALOG IT ACTUALLY LIVES IN. The first pass drew this as a full-width page; it "
    "is a section of UserSettingsDialog, 820px wide with a nav rail beside it, which is why the "
    "list is compact rows rather than a table.\n\n"
    "THE ONLY REAL GAP IN THE RELEASE, and it is thinner than it looks. The hub registry already "
    "has attach(), register_pending() for a folder whose vault does not exist yet, detach() that "
    "clears a flag rather than deleting the row so share links survive, overlap detection, and "
    "typed errors for every refusal. What is missing is HTTP routes over it: today "
    "routes/libraries.py has GET /libraries and POST /libraries/active and nothing else.\n\n"
    "Four routes: GET /libraries/inspect?path= (new, and the one that makes \"the folder answers "
    "the question\" work), POST /libraries, DELETE /libraries/{id}, and /filesystem/browse which "
    "already exists.\n\n"
    "The two refusals at the top are the registry's own errors made visible. The overlap one "
    "matters most: two libraries indexing the same pictures would each move them by their own "
    "layout, and neither would be wrong."
)

NEW_NOTES = {
    "libraries": ("living-in", 5340, -300, 620),
    "first-run": ("bringing-in", 0, -300, 620),
    "empty-landing": ("bringing-in", 0, 1560, 620),
    "consent": ("bringing-in", 4140, -300, 600),
}

by_id = {n["id"]: n for n in c["annotations"]}
for nid, text in NOTES.items():
    n = by_id.get(nid)
    if n is None:
        page, x, y, w = NEW_NOTES[nid]
        n = {"id": nid, "page": page, "x": x, "y": y, "w": w}
        c["annotations"].append(n)
    n["text"] = text

# The vocabulary note moves out of the way of the widened first page.
for n in c["annotations"]:
    if n["id"] == "vocabulary":
        n.update(x=2600, y=-300, w=560)
    if n["id"] == "what-is-derivable":
        n.update(x=1260, y=-300, w=600)
    if n["id"] == "pass-one":
        n.update(x=640, y=-300, w=600)

c["launch"] = {"view": "canvas", "page": "bringing-in"}
p.write_text(json.dumps(c, indent=2) + "\n")
print(f"canvas.json updated: {len(c['artboards'])} artboards, "
      f"{len(c['annotations'])} notes")
