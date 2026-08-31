import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import { useLibrariesStore } from "../../stores/useLibrariesStore";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i><slot /></i>" },
  VSelect: {
    props: ["modelValue", "items", "label"],
    emits: ["update:modelValue"],
    template:
      '<select :data-level="label"><slot name="selection" :index="0" /></select>',
  },
}));

const getLayoutSettings = vi.fn();
const setLayoutSettings = vi.fn();
const getLayoutMigrationPreview = vi.fn();
const runLayoutMigrationPass = vi.fn();
vi.mock("../../api/serverConfig", () => ({
  getLayoutSettings: (...a) => getLayoutSettings(...a),
  setLayoutSettings: (...a) => setLayoutSettings(...a),
  getLayoutMigrationPreview: (...a) => getLayoutMigrationPreview(...a),
  runLayoutMigrationPass: (...a) => runLayoutMigrationPass(...a),
}));

const undoBatchById = vi.fn();
vi.mock("../../stores/useOperationStore", () => ({
  useOperationStore: () => ({ undoBatchById }),
}));

import LayoutSection from "./LayoutSection.vue";

const NO_LAYOUT = {
  layout: null,
  layout_unfiled: "_Inbox",
  default_layout: "project/person,set",
};
const WITH_LAYOUT = { ...NO_LAYOUT, layout: "project/person,set" };
const NOTHING_TO_MOVE = {
  layout: "project/person,set",
  picture_count: 0,
  folder_count: 0,
  samples: [],
  collision_count: 0,
  collisions: [],
  cross_volume_count: 0,
  skipped_counts: {},
};
const WOULD_MOVE = {
  ...NOTHING_TO_MOVE,
  picture_count: 4109,
  folder_count: 312,
  samples: [
    { picture_id: 12, from: "0412.png", to: "2024 Shoots/Mira/0412.png" },
  ],
};

function setLocality({ canManage = true, loaded = true } = {}) {
  const store = useLibrariesStore();
  store.canManage = canManage;
  store.hasLoadedSuccessfully = loaded;
  return store;
}

function mountPane() {
  return mount(LayoutSection, {
    props: { open: true },
    global: {
      stubs: {
        SettingsSection: {
          props: ["title"],
          template:
            "<section><h3>{{ title }}</h3><slot name='action' /><slot /></section>",
        },
        SettingsInfoCard: { template: "<aside><slot /></aside>" },
        SettingsRow: {
          props: ["label", "sub"],
          template: '<div><span class="sub">{{ sub }}</span><slot /></div>',
        },
        // `emits` declared, and `loading` folded into `disabled`, because the
        // real AppButton does both: without the declaration the emit and the
        // fallthrough native listener BOTH fire and every click runs twice,
        // and `loading` forcing disabled is what stops a second click starting
        // a second migration (AppButton, issue #647).
        AppButton: {
          props: ["disabled", "loading", "variant", "size", "iconLeft"],
          emits: ["click"],
          template:
            '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
        },
      },
    },
  });
}

function buttonWith(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text));
}

describe("LayoutSection", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    setLocality();
    for (const fn of [
      getLayoutSettings,
      setLayoutSettings,
      getLayoutMigrationPreview,
      runLayoutMigrationPass,
      undoBatchById,
    ]) {
      fn.mockReset();
    }
  });

  it("offers a layout, and no migration, until one is chosen", async () => {
    getLayoutSettings.mockResolvedValue(NO_LAYOUT);

    const wrapper = mountPane();
    await flushPromises();

    expect(buttonWith(wrapper, "Choose a layout…")).toBeTruthy();
    // Nothing about moving files exists before there is a layout to move onto,
    // and the preview is not even asked for.
    expect(wrapper.text()).not.toContain("Move your library onto this layout");
    expect(getLayoutMigrationPreview).not.toHaveBeenCalled();
  });

  it("never says a layout will move files - that is the release's promise", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);

    const wrapper = mountPane();
    await flushPromises();

    // The rule's own copy is a table of what does and does not move, and the
    // migration - which does move things - says so separately and says
    // "Nothing has moved yet" until the button is pressed.
    expect(wrapper.text()).toContain(
      "A picture only moves when its folder stops being true",
    );
    expect(wrapper.text()).toContain("Nothing has moved yet");
    expect(runLayoutMigrationPass).not.toHaveBeenCalled();
  });

  it("counts the migration before offering it, in files and folders", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("4,109");
    expect(wrapper.text()).toContain("312");
    expect(wrapper.text()).toContain("2024 Shoots/Mira/0412.png");
  });

  it("says there is nothing to move rather than offering a no-op", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(NOTHING_TO_MOVE);

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("Nothing to move");
    expect(buttonWith(wrapper, "Move them now")).toBeFalsy();
  });

  it("runs every pass under ONE batch id, so the whole move is one undo", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);
    runLayoutMigrationPass
      .mockResolvedValueOnce({
        batch_id: "srv-layout-migration-0123456789abcdef",
        moved_count: 200,
        next_after_id: 200,
        done: false,
      })
      .mockResolvedValueOnce({
        batch_id: "srv-layout-migration-0123456789abcdef",
        moved_count: 40,
        next_after_id: 240,
        done: true,
      });

    const wrapper = mountPane();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();

    expect(runLayoutMigrationPass).toHaveBeenCalledTimes(2);
    // The first pass mints the id; every pass after it echoes that one back.
    // Dropping it would leave the owner undoing 200 pictures at a time.
    expect(runLayoutMigrationPass.mock.calls[0][0]).toEqual({
      afterId: 0,
      batchId: null,
    });
    expect(runLayoutMigrationPass.mock.calls[1][0]).toEqual({
      afterId: 200,
      batchId: "srv-layout-migration-0123456789abcdef",
    });
    expect(wrapper.text()).toContain("Moved 240 pictures");
  });

  it("undoes the whole run by its batch id, not the last pass", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);
    runLayoutMigrationPass.mockResolvedValue({
      batch_id: "srv-layout-migration-0123456789abcdef",
      moved_count: 12,
      next_after_id: 12,
      done: true,
    });

    const wrapper = mountPane();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();
    await buttonWith(wrapper, "Undo").trigger("click");
    await flushPromises();

    expect(undoBatchById).toHaveBeenCalledWith(
      "srv-layout-migration-0123456789abcdef",
    );
  });

  it("keeps what already moved when a pass fails, and says it can be finished", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);
    runLayoutMigrationPass
      .mockResolvedValueOnce({
        batch_id: "srv-layout-migration-0123456789abcdef",
        moved_count: 200,
        next_after_id: 200,
        done: false,
      })
      .mockRejectedValueOnce(new Error("boom"));

    const wrapper = mountPane();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();

    // The resumable half of the contract: half-moved and wholly consistent, and
    // the copy must not imply anything has to be repaired or redone.
    expect(wrapper.text()).toContain("press Move again to finish it");
    // Undo is still offered over what the failed run did move.
    expect(buttonWith(wrapper, "Undo")).toBeTruthy();
  });

  it("re-counts after a layout change, because the offer follows the layout", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    setLayoutSettings.mockResolvedValue({ ...NO_LAYOUT, layout: "project" });
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);

    const wrapper = mountPane();
    await flushPromises();
    expect(getLayoutMigrationPreview).toHaveBeenCalledTimes(1);

    await buttonWith(wrapper, "Turn off").trigger("click");
    await flushPromises();

    expect(setLayoutSettings).toHaveBeenCalledWith({ layout: null });
  });

  it("shows the locality sentence instead of controls a remote owner cannot use", async () => {
    setLocality({ canManage: false });

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain(
      "only available on the machine running PixlStash",
    );
    expect(getLayoutSettings).not.toHaveBeenCalled();
  });

  it("keeps the Undo when the undo is refused rather than throwing", async () => {
    // `undoBatchById` answers null when it refuses - read-only, another
    // operation in flight, or a failure it reported itself. Clearing the banner
    // regardless would discard the batch id, which is the only route back.
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);
    runLayoutMigrationPass.mockResolvedValue({
      batch_id: "srv-layout-migration-0123456789abcdef",
      moved_count: 12,
      next_after_id: 12,
      done: true,
    });
    undoBatchById.mockResolvedValue(null);

    const wrapper = mountPane();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();
    await buttonWith(wrapper, "Undo").trigger("click");
    await flushPromises();

    expect(buttonWith(wrapper, "Undo")).toBeTruthy();
    expect(wrapper.text()).toContain("The Undo is still here");
  });

  it("reports what a run refused instead of claiming a clean finish", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);
    runLayoutMigrationPass.mockResolvedValue({
      batch_id: "srv-layout-migration-0123456789abcdef",
      moved_count: 8,
      next_after_id: 8,
      done: true,
      skipped: [
        { picture_id: 1, reason: "move_failed" },
        { picture_id: 2, reason: "move_failed" },
      ],
    });

    const wrapper = mountPane();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Moved 8 pictures");
    expect(wrapper.text()).toContain("2 could not be moved just now");
  });

  it("still names the cross-volume files when none of them can move", async () => {
    // The case the check exists for: every candidate is across a mount point,
    // so picture_count is 0 and "nothing to move" would be a lie.
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue({
      ...NOTHING_TO_MOVE,
      cross_volume_count: 50,
      skipped_counts: { destination_other_volume: 50 },
    });

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("cannot be moved");
    expect(wrapper.text()).toContain("50");
    expect(wrapper.text()).not.toContain("Nothing to move");
  });

  it("cannot add a level once every facet is already in the layout", async () => {
    // The fallback this replaces appended a facet already in use, which renders
    // a duplicated folder level (`portrait/portrait/`) the backend takes
    // verbatim.
    getLayoutSettings.mockResolvedValue({
      ...NO_LAYOUT,
      layout: "project,person/set,tag",
    });
    getLayoutMigrationPreview.mockResolvedValue(NOTHING_TO_MOVE);

    const wrapper = mountPane();
    await flushPromises();

    expect(buttonWith(wrapper, "add one")).toBeFalsy();
  });

  it("stops rather than spinning when the cursor does not advance", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);
    // A server that keeps answering `done: false` with the same cursor would
    // otherwise loop forever at full request rate.
    runLayoutMigrationPass.mockResolvedValue({
      batch_id: "srv-layout-migration-0123456789abcdef",
      moved_count: 0,
      next_after_id: 0,
      done: false,
    });

    const wrapper = mountPane();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();

    expect(runLayoutMigrationPass).toHaveBeenCalledTimes(1);
  });

  it("offers a retry, not an empty builder, when the layout cannot be read", async () => {
    // An empty `segments` after a failed GET looks exactly like "no layout",
    // and the next click would PATCH one over a layout nobody has read.
    getLayoutSettings.mockRejectedValue(new Error("backend asleep"));

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("backend asleep");
    expect(buttonWith(wrapper, "Try again")).toBeTruthy();
    expect(buttonWith(wrapper, "Choose a layout…")).toBeFalsy();
  });

  it("warns about collisions and about files that cannot move at all", async () => {
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue({
      ...WOULD_MOVE,
      collision_count: 3,
      collisions: [
        { picture_id: 9, from: "a/0001.png", to: "2024 Shoots/0001-2.png" },
      ],
      cross_volume_count: 12,
    });

    const wrapper = mountPane();
    await flushPromises();

    expect(wrapper.text()).toContain("2024 Shoots/0001-2.png");
    expect(wrapper.text()).toContain("never renamed and never overwritten");
    // Cross-volume is a refusal, not a slow move: the copy must not promise one.
    expect(wrapper.text()).toContain("cannot be moved");
  });
});
