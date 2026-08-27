// Folder registration, and the two states it has to render rather than hit.
//
// `LOCAL_OWNER_ONLY` means a remote owner reads the list and 403s on every
// mutator, and the managed store answers 409 on DELETE because its state
// refuses rather than because the caller may not. Both are designed states
// here: a button that always fails on click is the failure this suite pins.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listModelFolders = vi.fn();
const createModelFolder = vi.fn();
const forgetModelFolder = vi.fn();
const rescanModelFolder = vi.fn();

vi.mock("../../api/modelFolders", () => ({
  MANAGED_KIND: "managed",
  SOURCE_KIND: "source",
  CREATABLE_KINDS: ["user", "source"],
  listModelFolders: (...a) => listModelFolders(...a),
  createModelFolder: (...a) => createModelFolder(...a),
  forgetModelFolder: (...a) => forgetModelFolder(...a),
  rescanModelFolder: (...a) => rescanModelFolder(...a),
}));

vi.mock("../../stores/useModelShelfStore", () => ({
  useModelShelfStore: () => ({ fetchRows: vi.fn() }),
}));

// The move job is the store's, not the dialog's - the dialog only starts one.
const relocate = vi.fn();
let moveBusy = false;
vi.mock("../../stores/useModelMovesStore", () => ({
  useModelMovesStore: () => ({
    relocate: (...a) => relocate(...a),
    get busy() {
      return moveBusy;
    },
  }),
}));

import ModelFoldersDialog from "./ModelFoldersDialog.vue";
import { useLibrariesStore } from "../../stores/useLibrariesStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-tooltip": {
        props: ["text"],
        template: "<div><slot name='activator' :props='{}' /></div>",
      },
      // The dialog shell teleports; its chrome is AppDialog's own contract.
      AppDialog: {
        template: "<div><slot name='header-right' /><slot /></div>",
      },
      FolderBrowser: true,
    },
  },
};

function folder(overrides = {}) {
  return {
    id: 1,
    path: "/home/g/loras",
    kind: "user",
    owner: null,
    movable: "per_item",
    relocatable: false,
    host_path: null,
    delete_after_import: false,
    last_checked: null,
    created_at: "2026-08-01T10:00:00",
    file_count: 91,
    present_bytes: 0,
    ...overrides,
  };
}

const MANAGED = folder({
  id: 2,
  path: "/var/pixlstash/models",
  kind: "managed",
  movable: "root_only",
  relocatable: true,
  file_count: 3,
});

// PixlStash's own download folder. `movable` is identical to the InsightFace
// packs' below, which is why the row reads the server's `relocatable` and never
// derives Move from `movable` itself.
const DOWNLOADS = folder({
  id: 4,
  path: "/home/g/.local/share/pixlstash/downloaded_models",
  kind: "foreign",
  owner: "pixlstash",
  movable: "root_only",
  relocatable: true,
  file_count: 4,
});

const INSIGHTFACE = folder({
  id: 5,
  path: "/home/g/.insightface/models",
  kind: "foreign",
  owner: "pixlstash",
  movable: "root_only",
  // Relocatable since #906. It reads identically to DOWNLOADS above, which is
  // the point: the two are told apart by path on the server and by nothing at
  // all here, so the row can only ever ask.
  relocatable: true,
  file_count: 2,
});

// The HuggingFace cache: `fixed`, because its location is `HF_HOME` and another
// tool owns it. The row that must never carry Move, and the reason the verb is
// read from the server rather than derived from `kind` - this is `foreign` too.
const HF_CACHE = folder({
  id: 6,
  path: "/home/g/.cache/huggingface/hub",
  kind: "foreign",
  owner: "pixlstash",
  movable: "fixed",
  relocatable: false,
  file_count: 26,
});

async function open(rows, { canManage = true, inDocker = false } = {}) {
  listModelFolders.mockResolvedValue(rows);
  const wrapper = mount(ModelFoldersDialog, {
    props: { open: true },
    ...globalOpts,
  });
  const libraries = useLibrariesStore();
  libraries.canManage = canManage;
  libraries.inDocker = inDocker;
  await new Promise((r) => setTimeout(r, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  listModelFolders.mockReset().mockResolvedValue([]);
  createModelFolder.mockReset().mockResolvedValue(folder({ id: 9 }));
  relocate.mockReset().mockResolvedValue(true);
  moveBusy = false;
  forgetModelFolder.mockReset().mockResolvedValue({ tombstoned_files: 0 });
  rescanModelFolder.mockReset().mockResolvedValue({ status: "started" });
});

describe("the managed store", () => {
  it("offers no forget affordance at all, not a disabled one", async () => {
    // DELETE on it is a 409, so a button there could only ever fail. No button
    // is a better answer than a button that is always wrong.
    const wrapper = await open([MANAGED]);
    const labels = wrapper
      .findAll("button")
      .map((b) => b.attributes("aria-label") || "");
    expect(labels.some((l) => l.startsWith("Forget"))).toBe(false);
    expect(labels.some((l) => l.startsWith("Move"))).toBe(true);
  });

  it("explains the missing control in the row, not in a tooltip", async () => {
    // A reason that lives only in a tooltip is a reason the keyboard and the
    // screen reader never reach.
    const wrapper = await open([MANAGED]);
    expect(wrapper.text()).toContain(
      "PixlStash keeps its own models here, so this folder stays.",
    );
  });

  it("keeps every row's action column at the same width", async () => {
    // A slot that collapsed on one row would slide every other row's buttons
    // sideways. Absent actions are hidden, never unrendered.
    const wrapper = await open([
      folder(),
      MANAGED,
      folder({ id: 3, kind: "foreign" }),
    ]);
    const counts = wrapper
      .findAll(".mf-row__actions")
      .map((group) => group.findAll("button").length);
    expect(new Set(counts).size).toBe(1);
  });
});

describe("relocation", () => {
  function moveButton(wrapper) {
    return wrapper
      .findAll("button")
      .find((b) => (b.attributes("aria-label") || "").startsWith("Move"));
  }

  it("offers Move on the folders the server says relocate", async () => {
    const wrapper = await open([MANAGED, DOWNLOADS, INSIGHTFACE]);
    const labels = wrapper
      .findAll("button")
      .map((b) => b.attributes("aria-label") || "")
      .filter((l) => l.startsWith("Move"));
    expect(labels).toHaveLength(3);
    expect(labels.join(" ")).toContain("downloaded_models");
    expect(labels.join(" ")).toContain(".insightface");
  });

  it("offers none on a root_only folder that cannot relocate", async () => {
    // The HuggingFace cache reads `foreign` and sits beside two folders that do
    // relocate, so nothing in the row itself distinguishes it. Deriving the verb
    // from `kind` or from `movable` would put a button there that can only ever
    // 409, which is why the server is asked.
    const wrapper = await open([HF_CACHE]);
    expect(moveButton(wrapper)).toBeUndefined();
  });

  it("sends the picked path for the folder the owner clicked", async () => {
    const wrapper = await open([MANAGED, DOWNLOADS]);
    const move = wrapper
      .findAll("button")
      .filter((b) => (b.attributes("aria-label") || "").startsWith("Move"))[1];
    await move.trigger("click");
    wrapper
      .getComponent({ name: "FolderBrowser" })
      .vm.$emit("select", "/mnt/x");
    await wrapper.vm.$nextTick();
    expect(relocate).toHaveBeenCalledWith(DOWNLOADS.id, "/mnt/x");
  });

  it("blocks the verb while a move is running rather than taking the 409", async () => {
    // One job, machine-wide, is the server's rule. A second POST is a 409, so
    // the reason is said in the row instead of reported afterwards.
    moveBusy = true;
    const wrapper = await open([MANAGED]);
    const move = moveButton(wrapper);
    expect(move.attributes("aria-disabled")).toBe("true");
    expect(move.attributes("disabled")).toBeUndefined();
    await move.trigger("click");
    expect(relocate).not.toHaveBeenCalled();
  });

  it("is unavailable to a remote owner, reachably", async () => {
    const wrapper = await open([MANAGED], { canManage: false });
    const move = moveButton(wrapper);
    expect(move.attributes("aria-disabled")).toBe("true");
    expect(move.attributes("aria-describedby")).toBe("mf-remote-note");
    await move.trigger("click");
    expect(relocate).not.toHaveBeenCalled();
  });

  it("sends the picked path for the InsightFace packs like any other row", async () => {
    // The one row whose path means something different on the server - it names
    // the InsightFace *root*, not the folder - which is deliberately invisible
    // here: the dialog sends what the owner picked and the server does the rest.
    const wrapper = await open([INSIGHTFACE]);
    await moveButton(wrapper).trigger("click");
    wrapper
      .getComponent({ name: "FolderBrowser" })
      .vm.$emit("select", "/mnt/big/.insightface");
    await wrapper.vm.$nextTick();
    expect(relocate).toHaveBeenCalledWith(
      INSIGHTFACE.id,
      "/mnt/big/.insightface",
    );
  });
});

describe("forgetting", () => {
  function forgetButton(wrapper) {
    return wrapper
      .findAll("button")
      .find((b) => (b.attributes("aria-label") || "").startsWith("Forget"));
  }

  it("blocks the verb while a move is running rather than taking the 409", async () => {
    // Forgetting deletes the very location rows a move is repointing, so the
    // server takes the one machine-wide slot for it too (#1017). The row that
    // matters is this one: an ordinary `user` folder is forgettable and NOT
    // relocatable, so a guard written only for Move leaves it clickable and the
    // owner gets a red error toast from a button that looked available.
    moveBusy = true;
    const wrapper = await open([folder()]);
    const forget = forgetButton(wrapper);
    expect(forget.attributes("aria-disabled")).toBe("true");
    expect(forget.attributes("disabled")).toBeUndefined();
    await forget.trigger("click");
    expect(forgetModelFolder).not.toHaveBeenCalled();
    // And the row says why. `rowReason` used to gate this on `relocatable`, so
    // this row was blocked and silent - the exact combination that reads as a
    // broken button.
    expect(wrapper.get(".mf-row .helptip").classes()).not.toContain(
      "helptip--empty",
    );
  });

  it("still lets it through with no move running", async () => {
    // Over-blocking is its own regression: the slot is free far more often than
    // it is held, and Forget is the shelf's only tombstone.
    const wrapper = await open([folder()]);
    const forget = forgetButton(wrapper);
    expect(forget.attributes("aria-disabled")).toBeUndefined();
    await forget.trigger("click");
    expect(forgetModelFolder).toHaveBeenCalledWith(1);
  });
});

describe("a remote owner", () => {
  it("sees the mutators, blocked, described, and still focusable", async () => {
    // The read is owner-only and succeeds from anywhere; only the writes are
    // §16.3. `aria-disabled`, never the attribute, or the reason it points at
    // is unreachable by keyboard.
    const wrapper = await open([folder()], { canManage: false });
    const scan = wrapper
      .findAll("button")
      .find((b) => (b.attributes("aria-label") || "").startsWith("Scan"));
    expect(scan.attributes("aria-disabled")).toBe("true");
    expect(scan.attributes("aria-describedby")).toBe("mf-remote-note");
    expect(wrapper.get("#mf-remote-note").text()).toContain(
      "allow_remote_host_ops",
    );
  });

  it("does not fire the request the server would refuse", async () => {
    const wrapper = await open([folder()], { canManage: false });
    const scan = wrapper
      .findAll("button")
      .find((b) => (b.attributes("aria-label") || "").startsWith("Scan"));
    await scan.trigger("click");
    expect(rescanModelFolder).not.toHaveBeenCalled();
  });

  it("still lets a local owner through", async () => {
    // Over-blocking is its own regression, so the positive direction is pinned
    // beside the negative one.
    const wrapper = await open([folder()]);
    const scan = wrapper
      .findAll("button")
      .find((b) => (b.attributes("aria-label") || "").startsWith("Scan"));
    expect(scan.attributes("aria-disabled")).toBeUndefined();
    await scan.trigger("click");
    expect(rescanModelFolder).toHaveBeenCalledWith(1);
  });
});

describe("the help mark", () => {
  it("keeps its box on a row that has nothing to explain", async () => {
    // The owner's requirement: an available row and a blocked row have
    // identical geometry, so the slot is reserved rather than conditional.
    const wrapper = await open([folder()]);
    const help = wrapper.get(".mf-row .helptip");
    expect(help.classes()).toContain("helptip--empty");
    expect(help.attributes("aria-label")).toBeUndefined();
  });

  it("names what it explains rather than saying 'Help'", async () => {
    const wrapper = await open([folder()], { canManage: false });
    const help = wrapper.get(".mf-row .helptip");
    expect(help.classes()).not.toContain("helptip--empty");
    expect(help.attributes("aria-label")).toContain("loras");
  });
});

describe("Docker", () => {
  it("blocks adding rather than letting the 400 happen", async () => {
    // POST needs a host path PixlStash cannot ask for from inside a container.
    const wrapper = await open([folder()], { inDocker: true });
    const add = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Add folder"));
    expect(add.attributes("aria-disabled")).toBe("true");
  });
});

describe("how much disk the folder is using", () => {
  it("shows the size beside the count, because a count alone understates a cache", async () => {
    // The case this exists for: few rows, enormous folder. "26 models" reads as
    // small until the 116 GB is next to it.
    const wrapper = await open([
      folder({
        path: "/home/g/.cache/huggingface/hub",
        kind: "foreign",
        owner: "pixlstash",
        file_count: 26,
        present_bytes: 116.3 * 1024 ** 3,
      }),
    ]);
    expect(wrapper.text()).toContain("116.3 GB");
  });

  it("omits the size at zero rather than claiming 0 B", async () => {
    // A folder with nothing `present` has no measurement, and "0 B" would read
    // as one. The managed store on a fresh install is exactly this.
    const wrapper = await open([folder({ file_count: 0, present_bytes: 0 })]);
    expect(wrapper.text()).not.toContain("0 B");
  });
});

describe("setting the ai-toolkit output folder", () => {
  const SOURCE = folder({
    id: 4,
    path: "/home/g/ai-toolkit/output",
    kind: "source",
    movable: "external",
  });

  it("offers to set it while none is registered", async () => {
    const wrapper = await open([folder()]);
    expect(wrapper.text()).toContain("Set ai-toolkit folder");
  });

  it("stops offering once one is registered, because there is only one", async () => {
    // ai-toolkit writes every run under a single output root, so a second
    // button-press has nothing left to set. The row it created carries the
    // Forget that undoes it.
    const wrapper = await open([folder(), SOURCE]);
    expect(wrapper.text()).not.toContain("Set ai-toolkit folder");
  });

  it("registers what the picker returns as the output root, not as a model folder", async () => {
    // The bug this whole change exists to fix: the picker used to hardcode
    // `kind: "user"`, so no UI could ever produce a source folder and the
    // import surface was unreachable.
    const wrapper = await open([folder()]);
    const setBtn = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Set ai-toolkit folder"));
    await setBtn.trigger("click");
    await wrapper
      .findComponent({ name: "FolderBrowser" })
      .vm.$emit("select", "/home/g/ai-toolkit/output");
    await wrapper.vm.$nextTick();

    expect(createModelFolder).toHaveBeenCalledWith({
      path: "/home/g/ai-toolkit/output",
      kind: "source",
    });
  });

  it("still registers an ordinary folder as one, after a set was cancelled", async () => {
    // The picker is shared, so the verb that opened it has to be forgotten when
    // it closes. Without that, cancelling a Set and then pressing Add would
    // silently register the next folder as an output root.
    const wrapper = await open([folder()]);
    const byText = (t) =>
      wrapper.findAll("button").find((b) => b.text().includes(t));
    await byText("Set ai-toolkit folder").trigger("click");
    const browser = wrapper.findComponent({ name: "FolderBrowser" });
    await browser.vm.$emit("close");
    await byText("Add folder").trigger("click");
    await browser.vm.$emit("select", "/home/g/loras2");
    await wrapper.vm.$nextTick();

    expect(createModelFolder).toHaveBeenCalledWith({
      path: "/home/g/loras2",
      kind: "user",
    });
  });
});
