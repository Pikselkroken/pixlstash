import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const getFolderStructureCommitStatus = vi.fn();
const startFolderStructureCommit = vi.fn();
const stopFolderStructureCommit = vi.fn();
vi.mock("../../api/folderStructure", () => ({
  getFolderStructureCommitStatus: (...a) =>
    getFolderStructureCommitStatus(...a),
  startFolderStructureCommit: (...a) => startFolderStructureCommit(...a),
  stopFolderStructureCommit: (...a) => stopFolderStructureCommit(...a),
}));

import FolderMappingPreviewStep from "./FolderMappingPreviewStep.vue";

function mountStep(props = {}) {
  return mount(FolderMappingPreviewStep, {
    props: {
      path: "/home/me/pictures",
      readTaskId: "read-1",
      assignments: [{ kind: "project", relative_path: "2024 Shoots" }],
      commitOnMount: true,
      ...props,
    },
    global: {
      stubs: {
        "v-icon": { template: "<i><slot /></i>" },
        "v-progress-circular": { template: "<span />" },
        AppButton: {
          props: ["disabled", "loading", "variant", "size"],
          template: '<button :disabled="disabled || loading"><slot /></button>',
        },
      },
    },
  });
}

function buttonWith(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text));
}

describe("FolderMappingPreviewStep", () => {
  beforeEach(() => {
    for (const fn of [
      getFolderStructureCommitStatus,
      startFolderStructureCommit,
      stopFolderStructureCommit,
    ]) {
      fn.mockReset();
    }
    getFolderStructureCommitStatus.mockResolvedValue({
      status: "running",
      stage: "indexing",
      processed: 3,
      total: 10,
    });
  });

  it("cannot be stopped in the seconds before the commit has a task id", async () => {
    // Between `committing` going true and the server answering, both stops used
    // to send stop("") - which fails with "Could not stop the import." while the
    // mapping commits anyway.
    let started;
    startFolderStructureCommit.mockImplementation(
      () => new Promise((resolve) => (started = resolve)),
    );

    const wrapper = mountStep();
    await flushPromises();

    const later = buttonWith(wrapper, "Organise later");
    const abort = buttonWith(wrapper, "Abort");
    expect(later.attributes("disabled")).toBeDefined();
    expect(abort.attributes("disabled")).toBeDefined();

    await later.trigger("click");
    await abort.trigger("click");
    await flushPromises();
    expect(stopFolderStructureCommit).not.toHaveBeenCalled();

    // Positive control: the moment the id lands, both stops work and address it.
    started({ task_id: "commit-7" });
    await flushPromises();

    const liveLater = buttonWith(wrapper, "Organise later");
    expect(liveLater.attributes("disabled")).toBeUndefined();
    await liveLater.trigger("click");
    await flushPromises();
    expect(stopFolderStructureCommit).toHaveBeenCalledWith("commit-7", "defer");
  });

  it("still commits with no assignments when nothing is running", async () => {
    // "Organise later" before the import means something else entirely, and the
    // guard above must not disable it.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-9" });

    const wrapper = mountStep({ commitOnMount: false });
    await flushPromises();

    const later = buttonWith(wrapper, "Organise later");
    expect(later.attributes("disabled")).toBeUndefined();
    await later.trigger("click");
    await flushPromises();

    expect(startFolderStructureCommit).toHaveBeenCalledWith(
      "read-1",
      [],
      "",
      "reference",
      null,
      [],
    );
  });

  // "N are created or matched" is the only number on the screen that says how
  // much the commit changes, and it is arithmetic nobody re-checks by eye.
  describe("the count under 'what happens when you press the button'", () => {
    function factText(wrapper) {
      return wrapper
        .findAll(".preview-step__fact")
        .map((el) => el.text())
        .find((text) => text.includes("created or matched"));
    }

    it("counts a name reused across two kinds once per kind", async () => {
      // A `Wedding` set inside a `Wedding` project is two entities, and the
      // commit creates two. Collapsing them to a single name undercounted
      // exactly the libraries that name a folder after the thing above it.
      const wrapper = mountStep({
        commitOnMount: false,
        assignments: [
          { kind: "project", relative_path: "2024/Wedding" },
          { kind: "set", relative_path: "2024/Wedding/Wedding" },
        ],
      });
      await flushPromises();

      expect(factText(wrapper)).toContain("2 ");
    });

    it("counts tags, and names them", async () => {
      // Tags were counted by the number and left out of the sentence beside
      // it, so a mapping of nothing but tags read as "0 ... are created".
      const wrapper = mountStep({
        commitOnMount: false,
        assignments: [
          { kind: "tag", relative_path: "beach" },
          { kind: "tag", relative_path: "sunset" },
        ],
      });
      await flushPromises();

      const text = factText(wrapper);
      expect(text).toContain("2 ");
      expect(text).toContain("tags");
    });

    it("still collapses the same name repeated within one kind", async () => {
      // Two `Alice` folders at different depths are one Person, and always were.
      const wrapper = mountStep({
        commitOnMount: false,
        assignments: [
          { kind: "person", relative_path: "2023/Alice" },
          { kind: "person", relative_path: "2024/Alice" },
        ],
      });
      await flushPromises();

      expect(factText(wrapper)).toContain("1 ");
    });

    it("names only the kinds the mapping has, and agrees its verb", async () => {
      // "1 projects, sets, people and tags are created or matched" named three
      // kinds that were not in the mapping and disagreed with its own count.
      const wrapper = mountStep({
        commitOnMount: false,
        assignments: [{ kind: "project", relative_path: "2024/Wedding" }],
      });
      await flushPromises();

      const text = factText(wrapper);
      expect(text).toContain("1 project is created or matched");
      for (const absent of ["sets", "people", "tags"]) {
        expect(text).not.toContain(absent);
      }
    });

    it("names every kind when the mapping has none of them", async () => {
      // Everything mapped to "just a folder" creates nothing, and "0 projects,
      // sets, people and tags" is true of every kind at once.
      const wrapper = mountStep({
        commitOnMount: false,
        assignments: [{ kind: "folder", relative_path: "2024" }],
      });
      await flushPromises();

      const text = factText(wrapper);
      expect(text).toContain("0 ");
      for (const noun of ["projects", "sets", "people", "tags"]) {
        expect(text).toContain(noun);
      }
    });

    it("names every facet it counts", async () => {
      // The sentence is built from FACET_KINDS so a fifth facet cannot be
      // counted and left unnamed the way Tag was.
      const wrapper = mountStep({
        commitOnMount: false,
        assignments: [
          { kind: "project", relative_path: "p" },
          { kind: "set", relative_path: "s" },
          { kind: "person", relative_path: "a" },
          { kind: "tag", relative_path: "t" },
        ],
      });
      await flushPromises();

      const text = factText(wrapper);
      expect(text).toContain("4 ");
      for (const noun of ["projects", "sets", "people", "tags"]) {
        expect(text).toContain(noun);
      }
    });
  });
});

describe("caption files beside the pictures", () => {
  const READ_RESULT = {
    root: { path: "/home/me/pictures" },
    picture_count: 3,
    captions: [
      { suffix: ".txt", kind: "tags", files: 120, folders: 4, sample: "1girl, solo" },
      { suffix: "_caption.txt", kind: "description", files: 40, folders: 1, sample: "A woman in a field." },
    ],
    levels: [],
  };

  function selects(wrapper) {
    return wrapper.findAll("select");
  }

  // The commit refuses captions with mode `reference` (400), so every caption
  // question is asked in the mode that accepts them.
  function mountImport(props) {
    return mountStep({ mode: "local_import", ...props });
  }

  it("offers each pattern the read found, pre-filled with the read's guess", () => {
    const wrapper = mountImport({ commitOnMount: false, readResult: READ_RESULT });
    expect(wrapper.text()).toContain("*.txt");
    expect(wrapper.text()).toContain("120 files in 4 folders");
    expect(wrapper.text()).toContain("1girl, solo");
    expect(selects(wrapper).map((s) => s.element.value)).toEqual([
      "tags",
      "description",
    ]);
    // Per kind, because only a tags file spares the tagger.
    expect(wrapper.text()).toContain("120 caption files are read as tags");
    expect(wrapper.text()).toContain("40 caption files are read as descriptions");
    expect(wrapper.text()).not.toContain("left unread");
    // The suffix is the row's label; the select carries the name for AT only.
    expect(wrapper.text()).not.toContain("Read *.txt as");
    expect(selects(wrapper)[0].attributes("aria-label")).toBe("Read *.txt as");
  });

  it("sends the owner's answers with the commit, ignore included", async () => {
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
    const wrapper = mountImport({ commitOnMount: false, readResult: READ_RESULT });
    await selects(wrapper)[0].setValue("ignore");
    expect(wrapper.text()).toContain("40 caption files are read as descriptions");
    expect(wrapper.text()).not.toContain("read as tags");
    expect(wrapper.text()).toContain("120 caption files are left unread");
    await buttonWith(wrapper, "Yes, build this library").trigger("click");
    await flushPromises();
    expect(startFolderStructureCommit).toHaveBeenCalledWith(
      "read-1",
      expect.any(Array),
      "",
      "local_import",
      READ_RESULT,
      [
        { suffix: ".txt", kind: "ignore" },
        { suffix: "_caption.txt", kind: "description" },
      ],
    );
  });

  it("hands the answers to `build` when the library does not exist yet", async () => {
    const wrapper = mountImport({
      commitOnMount: false,
      libraryExists: false,
      readResult: READ_RESULT,
    });
    await buttonWith(wrapper, "Yes, build this library").trigger("click");
    expect(wrapper.emitted("build")[0]).toEqual([
      [{ kind: "project", relative_path: "2024 Shoots" }],
      [
        { suffix: ".txt", kind: "tags" },
        { suffix: "_caption.txt", kind: "description" },
      ],
    ]);
  });

  it("prefers saved answers over the read's guess, and sends them when the read is gone", async () => {
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
    const saved = [{ suffix: ".txt", kind: "description" }];
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: READ_RESULT,
      captions: saved,
    });
    expect(selects(wrapper)[0].element.value).toBe("description");

    // A resumed commit after the library switch: no read result to list the
    // patterns from, only what was saved.
    const resumed = mountImport({
      commitOnMount: true,
      readResult: null,
      captions: saved,
    });
    await flushPromises();
    expect(resumed.text()).not.toContain("Caption files beside");
    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual(saved);
  });

  it("keeps a saved 'read nothing' when the card has patterns to show", async () => {
    // `[]` is an answer, not an absence: Organise later, or a commit that
    // failed after one. Seeding each row from the read's guess instead put the
    // declined conventions back on the card and committed them on the retry.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-4" });
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: READ_RESULT,
      captions: [],
    });
    expect(selects(wrapper).map((s) => s.element.value)).toEqual([
      "ignore",
      "ignore",
    ]);

    await buttonWith(wrapper, "Yes, build this library").trigger("click");
    await flushPromises();

    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([
      { suffix: ".txt", kind: "ignore" },
      { suffix: "_caption.txt", kind: "ignore" },
    ]);
  });

  it("asks nothing, and sends nothing, for a reference folder", async () => {
    // A reference folder's caption files belong to its own editor, and the
    // commit refuses the mode/captions pair with a 400.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-2" });
    const wrapper = mountStep({ commitOnMount: false, readResult: READ_RESULT });
    expect(wrapper.text()).not.toContain("Caption files beside");
    expect(selects(wrapper)).toHaveLength(0);
    expect(wrapper.text()).not.toContain("caption files are read");

    await buttonWith(wrapper, "Yes, build this library").trigger("click");
    await flushPromises();
    expect(startFolderStructureCommit.mock.calls.at(-1)[3]).toBe("reference");
    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([]);
  });

  it("leaves the answer open for Organise later after a partial read", async () => {
    // Declining to decide after a read that stopped early is not "read
    // nothing": the unwalked folders were never offered, so the key is left
    // out and the import probes there, the same rule as the wizard's later().
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: { ...READ_RESULT, captions_complete: false },
    });

    await buttonWith(wrapper, "Organise later").trigger("click");
    await flushPromises();

    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toBeNull();
  });

  it("commits no answer at all for Organise later", async () => {
    // The card is on screen with the read's guesses in it, but declining to
    // decide is not confirming them: `[]` says "read nothing", and only a
    // missing key would let the import probe.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
    const wrapper = mountImport({ commitOnMount: false, readResult: READ_RESULT });
    expect(selects(wrapper)).toHaveLength(2);

    await buttonWith(wrapper, "Organise later").trigger("click");
    await flushPromises();

    expect(startFolderStructureCommit.mock.calls.at(-1)[1]).toEqual([]);
    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([]);
  });

  it("commits the saved answer on mount, never the read's guess", async () => {
    // The commit starts before the card can be read, so the guesses in it were
    // never anybody's answer. This is what "Drop this, organise later" on an
    // existing library resumes into, with nothing saved.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
    mountImport({ commitOnMount: true, readResult: READ_RESULT, captions: [] });
    await flushPromises();

    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([]);
  });

  it("passes 'never asked' through the commit on mount rather than answering", async () => {
    // `null` is an entry saved before this card existed: nobody was asked, and
    // `[]` would answer "read nothing" on their behalf and silence sidecars
    // that same import used to pick up by probing.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
    mountImport({
      commitOnMount: true,
      readResult: READ_RESULT,
      captions: null,
    });
    await flushPromises();

    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toBeNull();
  });

  it("never lets a late read result overwrite the saved answer", async () => {
    // A resumed auto-commit starts before the read result is back. When it
    // lands the card seeds itself from the read's guesses - which is not the
    // owner answering. Reporting that seeding replaced the held `[]` with the
    // guesses, so a retry after a failed auto-commit sent a convention nobody
    // confirmed.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
    const wrapper = mountImport({
      commitOnMount: true,
      readResult: null,
      captions: [],
    });
    await flushPromises();
    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([]);

    await wrapper.setProps({ readResult: READ_RESULT });
    await flushPromises();

    // The card itself stays hidden while the commit runs, but the seeding
    // happens either way - it is the report that must not.
    expect(wrapper.emitted("update:captions")).toBeUndefined();
  });

  it("reports an answer the owner actually makes", async () => {
    // The positive control for the test above: only the select's own change
    // replaces what the wizard holds, and it still does.
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: READ_RESULT,
    });
    expect(wrapper.emitted("update:captions")).toBeUndefined();

    await selects(wrapper)[0].setValue("ignore");

    expect(wrapper.emitted("update:captions").at(-1)).toEqual([
      [
        { suffix: ".txt", kind: "ignore" },
        { suffix: "_caption.txt", kind: "description" },
      ],
    ]);
  });

  it("answers normally from 'never asked' once the card is on screen", async () => {
    // The same `null`, but with patterns to show: the card seeds from the
    // read's guesses and what the owner leaves there is a real answer.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: READ_RESULT,
      captions: null,
    });
    expect(selects(wrapper)[0].element.value).toBe("tags");

    await selects(wrapper)[0].setValue("ignore");
    await buttonWith(wrapper, "Yes, build this library").trigger("click");
    await flushPromises();

    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([
      { suffix: ".txt", kind: "ignore" },
      { suffix: "_caption.txt", kind: "description" },
    ]);
  });

  it("does not claim a description file spares the tagger", async () => {
    // A picture with a description still has no tags of its own, so it is
    // queued for the tagger exactly as one with no caption file at all.
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: {
        ...READ_RESULT,
        captions: [READ_RESULT.captions[1]],
      },
    });

    expect(wrapper.text()).toContain("40 caption files are read as descriptions");
    expect(wrapper.text()).not.toContain("read as tags");
    expect(wrapper.text()).not.toContain("tagged from scratch");
  });

  it("answers a suffix that is also an inherited object key", async () => {
    // The suffixes come from the owner's filenames, so `constructor` and
    // `__proto__` are reachable. On a plain object the seeding check reads the
    // inherited value as an answer already made and skips it, and the commit
    // then sends a function as the kind.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-3" });
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: {
        ...READ_RESULT,
        captions: [
          {
            suffix: "constructor",
            kind: "tags",
            files: 7,
            folders: 1,
            sample: "1girl, solo",
          },
        ],
      },
    });
    expect(selects(wrapper)[0].element.value).toBe("tags");

    await buttonWith(wrapper, "Yes, build this library").trigger("click");
    await flushPromises();
    expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([
      { suffix: "constructor", kind: "tags" },
    ]);
  });

  it("says so when the read only got through part of the tree", async () => {
    // The answers are per suffix and apply tree-wide, so nothing about the
    // payload changes; what is missing is a pattern that lives only in the
    // part the walk never reached. Silence would read as "these are all your
    // caption files".
    const line = "Not every folder was checked";
    expect(
      mountImport({ commitOnMount: false, readResult: READ_RESULT }).text(),
    ).not.toContain(line);
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: { ...READ_RESULT, captions_complete: false },
    });
    expect(wrapper.text()).toContain(line);
    expect(selects(wrapper)).toHaveLength(2);
  });

  it("asks nothing when the read found no caption files", () => {
    const wrapper = mountImport({
      commitOnMount: false,
      readResult: { ...READ_RESULT, captions: [] },
    });
    expect(wrapper.text()).not.toContain("Caption files beside");
    expect(selects(wrapper)).toHaveLength(0);
  });

  describe("a read that stopped early and found no pattern", () => {
    const PARTIAL_EMPTY = {
      ...READ_RESULT,
      captions: [],
      captions_complete: false,
    };

    it("still says the list is incomplete, with nothing to list", () => {
      // An empty list under the warning is the only way to say "these may not
      // be all your caption files"; hiding the card says the opposite.
      const wrapper = mountImport({
        commitOnMount: false,
        readResult: PARTIAL_EMPTY,
      });
      expect(wrapper.text()).toContain("Caption files beside");
      expect(wrapper.text()).toContain("Not every folder was checked");
      expect(selects(wrapper)).toHaveLength(0);
    });

    it("lets the import probe rather than answering 'read nothing'", async () => {
      // Nobody was asked about the folders the walk never reached, so `[]`
      // would answer for them; `null` omits the key and probes (§22).
      startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
      const wrapper = mountImport({
        commitOnMount: false,
        readResult: PARTIAL_EMPTY,
      });
      await buttonWith(wrapper, "Yes, build this library").trigger("click");
      await flushPromises();

      expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toBeNull();
    });

    it("keeps a saved 'read nothing' as the answer it is", async () => {
      // A resumed commit whose owner already answered `[]`. The read being
      // partial does not un-ask them.
      startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
      const wrapper = mountImport({
        commitOnMount: false,
        readResult: PARTIAL_EMPTY,
        captions: [],
      });
      await buttonWith(wrapper, "Yes, build this library").trigger("click");
      await flushPromises();

      expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([]);
    });

    it("answers '[]' when the same empty read got through the whole tree", async () => {
      // Everything was sniffed and there is nothing to read, so probing would
      // open a `.txt` §20 deliberately dropped.
      startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
      const wrapper = mountImport({
        commitOnMount: false,
        readResult: { ...READ_RESULT, captions: [] },
      });
      await buttonWith(wrapper, "Yes, build this library").trigger("click");
      await flushPromises();

      expect(startFolderStructureCommit.mock.calls.at(-1)[5]).toEqual([]);
    });

    it("asks nothing of a reference folder, partial or not", () => {
      // `captions` with mode `reference` is a 400, so a partial read must not
      // put the card on screen there either.
      const wrapper = mountStep({
        commitOnMount: false,
        readResult: PARTIAL_EMPTY,
      });
      expect(wrapper.text()).not.toContain("Caption files beside");
      expect(wrapper.text()).not.toContain("Not every folder was checked");
    });
  });
});
