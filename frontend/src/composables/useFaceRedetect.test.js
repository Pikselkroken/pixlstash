import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { effectScope, nextTick } from "vue";

vi.mock("../api/pictures", () => ({ detectFaces: vi.fn(async () => ({})) }));

import { detectFaces } from "../api/pictures";
import { useNoticeStore } from "../stores/useNoticeStore";
import { useWsStore } from "../stores/useWsStore";
import { useFaceRedetect } from "./useFaceRedetect";

function setup() {
  const scope = effectScope();
  const api = scope.run(() => useFaceRedetect());
  return { api, ws: useWsStore(), notices: useNoticeStore() };
}

async function land(ws, { newFaces, origin = ws.clientId, ids = [1, 2] }) {
  ws.wsFacesUpdate = {
    key: ws.wsFacesUpdate.key + 1,
    pictureIds: ids,
    newFaces,
    originClientId: origin,
  };
  await nextTick();
}

describe("useFaceRedetect", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    detectFaces.mockClear();
  });

  it("offers a more sensitive run when this tab's run found nothing new", async () => {
    const { api, ws, notices } = setup();
    await api.detectFacesAgain([1, 2]);
    expect(detectFaces).toHaveBeenLastCalledWith([1, 2], { sensitive: false });

    await land(ws, { newFaces: 0 });
    const notice = notices.notices.at(-1);
    expect(notice.text).toBe("No new faces found in these 2 pictures.");
    expect(notice.action.label).toBe("Try more sensitive");

    notices.invokeAction(notice.id);
    await nextTick();
    expect(detectFaces).toHaveBeenLastCalledWith([1, 2], { sensitive: true });

    // The sensitive run finding nothing says so, and offers nothing more.
    await land(ws, { newFaces: 0 });
    const last = notices.notices.at(-1);
    expect(last.text).toContain("even on the more sensitive setting");
    expect(last.action).toBeNull();
  });

  it("stays quiet when new faces were found, or the run was another tab's", async () => {
    const { api, ws, notices } = setup();
    await api.detectFacesAgain([1]);
    await land(ws, { newFaces: 0, origin: "another-tab" });
    await land(ws, { newFaces: 2, ids: [1] });
    await land(ws, { newFaces: 0, ids: [1] });
    expect(notices.notices).toEqual([]);
  });
});
