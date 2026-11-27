import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  GOALS,
  WORKSPACES,
  NEEDS,
  HEADLINES,
  CONCEPTS,
} from "./catalogue.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, "..");
const read = (path) => readFileSync(resolve(SRC, path), "utf8");

// The routes the Guide may navigate to, and the settings tabs it may open,
// read from their sources so a rename there fails here.
const ROUTES = [...read("router/index.js").matchAll(/path: "(\/[^":]*)"/g)].map((m) => m[1]);
const SETTINGS_TABS = [
  ...read("components/settings/UserSettingsDialog.vue").matchAll(/^\s+id: "([a-z-]+)",$/gm),
].map((m) => m[1]);

function vueSources(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...vueSources(path));
    else if (entry.name.endsWith(".vue")) out.push(readFileSync(path, "utf8"));
  }
  return out;
}

const allTasks = CONCEPTS.flatMap((c) => c.tasks.map((t) => ({ concept: c, task: t })));
const allSteps = allTasks.flatMap(({ concept, task }) =>
  task.steps.map((step) => ({ concept, task, step })),
);

describe("catalogue: identifiers", () => {
  it("concept ids are unique", () => {
    const ids = CONCEPTS.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("task ids are unique within their concept", () => {
    for (const concept of CONCEPTS) {
      const ids = concept.tasks.map((t) => t.id);
      expect(new Set(ids).size, `${concept.id} task ids`).toBe(ids.length);
    }
  });

  it("headline ids are unique", () => {
    const ids = HEADLINES.map((h) => h.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("goal, workspace and goal ids are unique", () => {
    expect(new Set(GOALS.map((g) => g.id)).size).toBe(GOALS.length);
    expect(new Set(WORKSPACES.map((w) => w.id)).size).toBe(WORKSPACES.length);
  });
});

describe("catalogue: references resolve", () => {
  const conceptIds = new Set(CONCEPTS.map((c) => c.id));
  const goalIds = new Set(GOALS.map((g) => g.id));
  const workspaceIds = new Set(WORKSPACES.map((w) => w.id));
  const needKeys = new Set(Object.keys(NEEDS));

  it("every concept goal and workspace exists", () => {
    for (const concept of CONCEPTS) {
      if (concept.goal !== null) expect(goalIds.has(concept.goal), concept.id).toBe(true);
      for (const ws of concept.workspaces) expect(workspaceIds.has(ws), `${concept.id} -> ${ws}`).toBe(true);
    }
  });

  it("every concept and task need key exists", () => {
    const keysIn = (needs) => needs.flatMap((n) => (Array.isArray(n) ? n : [n]));
    for (const concept of CONCEPTS) {
      for (const key of keysIn(concept.needs)) expect(needKeys.has(key), `${concept.id} needs ${key}`).toBe(true);
      for (const task of concept.tasks) {
        for (const key of keysIn(task.needs)) {
          expect(needKeys.has(key), `${concept.id}/${task.id} needs ${key}`).toBe(true);
        }
      }
    }
  });

  it("every NEEDS.first is a concept", () => {
    for (const [key, need] of Object.entries(NEEDS)) {
      expect(conceptIds.has(need.first), `NEEDS.${key}.first`).toBe(true);
    }
  });

  it("every headline points at a concept and task that exist", () => {
    for (const h of HEADLINES) {
      const concept = CONCEPTS.find((c) => c.id === h.concept);
      expect(concept, `headline ${h.id} concept`).toBeTruthy();
      expect(
        concept.tasks.some((t) => t.id === h.task),
        `headline ${h.id} task ${h.task}`,
      ).toBe(true);
    }
  });

  it("every headline release is 2.0 or next", () => {
    for (const h of HEADLINES) expect(["2.0", "next"]).toContain(h.release);
  });
});

describe("catalogue: completeness", () => {
  it("every non-new concept has at least one task", () => {
    for (const concept of CONCEPTS) {
      if (concept.id === "new") continue;
      expect(concept.tasks.length, concept.id).toBeGreaterThanOrEqual(1);
    }
  });

  it("every task has at least one step with a non-empty say", () => {
    for (const { concept, task } of allTasks) {
      expect(task.steps.length, `${concept.id}/${task.id}`).toBeGreaterThanOrEqual(1);
    }
    for (const { concept, task, step } of allSteps) {
      expect(typeof step.say === "string" && step.say.trim().length > 0, `${concept.id}/${task.id}`).toBe(true);
    }
  });

  it("every task has a sentence and an entry", () => {
    for (const { concept, task } of allTasks) {
      expect(task.sentence?.trim(), `${concept.id}/${task.id} sentence`).toBeTruthy();
      expect(task.entry?.trim(), `${concept.id}/${task.id} entry`).toBeTruthy();
    }
  });

  it("the startup slots are 1..6 plus 'empty', each used once", () => {
    const slots = CONCEPTS.map((c) => c.startup).filter((s) => s !== null);
    const numbered = slots.filter((s) => typeof s === "number").sort((a, b) => a - b);
    expect(numbered).toEqual([1, 2, 3, 4, 5, 6]);
    expect(slots.filter((s) => s === "empty")).toHaveLength(1);
  });

  it("every numbered welcome slide names a task of its own concept", () => {
    for (const c of CONCEPTS.filter((c) => typeof c.startup === "number")) {
      expect(
        c.tasks.some((t) => t.id === c.startupTask),
        `${c.id} startupTask=${c.startupTask}`,
      ).toBe(true);
    }
  });
});

describe("catalogue: navigation targets", () => {
  it("reads the routes and settings tabs it checks against", () => {
    expect(ROUTES).toEqual(expect.arrayContaining(["/", "/workflows", "/models/runs"]));
    expect(SETTINGS_TABS).toEqual(expect.arrayContaining(["appearance", "libraries", "comfyui"]));
  });

  it("every go is a known route or settings tab", () => {
    for (const { concept, task, step } of allSteps) {
      if (step.go === undefined) continue;
      const ok =
        ROUTES.includes(step.go) ||
        (step.go.startsWith("settings:") && SETTINGS_TABS.includes(step.go.slice("settings:".length)));
      expect(ok, `${concept.id}/${task.id} go=${step.go}`).toBe(true);
    }
  });
});

describe("catalogue: data-guide hooks", () => {
  const source = vueSources(SRC).join("\n");

  it("every data-guide used in a target exists in a component", () => {
    const missing = [];
    for (const { concept, task, step } of allSteps) {
      if (!step.target) continue;
      for (const match of step.target.matchAll(/data-guide="([^"]+)"/g)) {
        if (!source.includes(`data-guide="${match[1]}"`)) {
          missing.push(`${concept.id}/${task.id}: ${match[1]}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });
});
