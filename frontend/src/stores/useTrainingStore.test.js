// The training panel's override builder: a cleared steps field is left out of
// the config so the template default applies, rather than sent as 0.

import { describe, it, expect, beforeEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useTrainingStore } from "./useTrainingStore.js";

describe("useTrainingStore.buildConfigOverrides steps", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("sends the steps the user typed", () => {
    const store = useTrainingStore();
    store.steps = 1200;

    const overrides = store.buildConfigOverrides();

    expect(overrides.config.process[0].train.steps).toBe(1200);
  });

  it("omits steps when the field is cleared, instead of sending 0", () => {
    const store = useTrainingStore();
    store.steps = "";

    const overrides = store.buildConfigOverrides();

    expect(overrides.config?.process?.[0]?.train?.steps).toBeUndefined();
  });
});
