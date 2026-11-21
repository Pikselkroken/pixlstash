/**
 * The prompt assistant's ("Photo look") remembered choices for one surface.
 *
 * The server's pre-flight answer (`group.prompt.assist`) is the catalogue: the
 * categories, their options and their shipped defaults. This owns what the
 * owner did on top of it, per model family and per context (`photo` for the
 * Run dialog, `sheet` for the sheet step), in `localStorage` under
 * `pixlstash:promptAssist` until the hub keeps it (Phase 3):
 *
 *   { <family>: { <context>: { on?, choices?: { <category>: choice } } } }
 *
 * **Sparse: an absent key means never chosen**, so that row follows the
 * shipped default even when a release changes it. A choice is `{ id }` (a
 * preset), `{ text }` (typed) or `"none"` (an emptied field), and an explicit
 * `"none"` is kept.
 *
 * The texts typed in a category ("Your entries") are kept exactly as the sheet
 * step keeps its typed Top, Bottoms and Shoes (`utils/recentValues`): in the
 * same document under `recent["photoLook:<family>:<context>:<category>"]`,
 * recorded when a run is sent with them, newest first, a case-folded repeat
 * moved to the front, twenty at most.
 *
 * The prompt box is never touched: the request carries the choices beside the
 * prompt (`prompt_assist`), and the server assembles.
 */
import { computed, reactive, ref, unref, watch } from "vue";

import { readChoices, withRecent, writeChoices } from "../utils/recentValues";

const STORE_KEY = "pixlstash:promptAssist";

/** Trimmed, as the sheet step's typed values are. */
const tidy = (text) => String(text || "").trim();
const same = (a, b) => tidy(a).toLowerCase() === tidy(b).toLowerCase();

function readAll() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {};
  } catch (err) {
    console.warn("[prompt-assist] could not read the remembered photo look", err);
    return {};
  }
}

/**
 * One category's choice as `POST /workflows/run` takes it. The only place the
 * wire encoding lives, so a change to the contract is this one line.
 */
export function encodeChoice(choice) {
  return choice;
}

/**
 * @param {import("vue").Ref<object|null>} assist the pre-flight's `prompt.assist`
 * @param {string} context "photo" or "sheet"
 */
export function usePromptAssist(assist, context) {
  const state = reactive({ on: undefined, choices: {} });
  // Each category's typed entries, as `recentValues` holds them.
  const recent = ref({});
  const family = computed(() => unref(assist)?.family || "");
  const categories = computed(() => unref(assist)?.categories || []);

  function load() {
    const mine = readAll()[family.value]?.[context] || {};
    state.on = mine.on;
    state.choices = { ...mine.choices };
    recent.value = readChoices().recent || {};
  }
  watch(family, load, { immediate: true });

  function save() {
    if (!family.value) return;
    try {
      const all = readAll();
      const mine = {};
      if (state.on !== undefined) mine.on = state.on;
      if (Object.keys(state.choices).length) mine.choices = state.choices;
      all[family.value] = { ...all[family.value], [context]: mine };
      localStorage.setItem(STORE_KEY, JSON.stringify(all));
    } catch (err) {
      console.warn(
        `[prompt-assist] could not remember the photo look for ${family.value}/${context}`,
        err,
      );
    }
  }

  const defaultOn = computed(() => Boolean(unref(assist)?.default_on));
  /** On where the server says so (MiniMax H3 Image), else once turned on. */
  const on = computed(() =>
    unref(assist) ? (state.on ?? defaultOn.value) : false,
  );
  function setOn(value) {
    // Sparse like the rows: back at the default is "never chosen".
    state.on = value === defaultOn.value ? undefined : value;
    save();
  }

  const category = (id) => categories.value.find((cat) => cat.id === id);
  const option = (cat, id) => cat?.options?.find((opt) => opt.id === id);

  function shippedDefault(cat) {
    return option(cat, cat.default) ? { id: cat.default } : "none";
  }

  /** What the row holds now: the owner's choice, else the shipped default. */
  function current(id) {
    const cat = category(id);
    const stored = state.choices[id];
    if (stored === "none") return "none";
    if (stored?.text) return stored;
    // A preset a release has since retired reads as never chosen.
    if (stored?.id && option(cat, stored.id)) return stored;
    return cat ? shippedDefault(cat) : "none";
  }

  /** The field's text: a preset's short name, typed text, or "" for nothing. */
  function fieldText(id) {
    const choice = current(id);
    if (choice === "none") return "";
    return choice.text ?? option(category(id), choice.id)?.label ?? "";
  }

  /** The words the row adds, in full ("" for nothing). */
  function addedText(id) {
    const choice = current(id);
    if (choice === "none") return "";
    return choice.text ?? option(category(id), choice.id)?.text ?? "";
  }

  function isDefault(id) {
    const cat = category(id);
    return JSON.stringify(current(id)) === JSON.stringify(shippedDefault(cat));
  }

  function defaultLabel(id) {
    const cat = category(id);
    return option(cat, cat?.default)?.label || "";
  }

  /**
   * Keep what the field says. Empty adds nothing; a preset's name or its full
   * sentence picks that preset; anything else is the owner's own text. An
   * unchanged field writes nothing, so leaving a never-chosen row pins nothing.
   */
  function commit(id, text) {
    const typed = tidy(text);
    if (typed === fieldText(id)) return;
    const preset = category(id)?.options?.find(
      (opt) => same(opt.label, typed) || same(opt.text, typed),
    );
    state.choices = {
      ...state.choices,
      [id]: !typed ? "none" : preset ? { id: preset.id } : { text: typed },
    };
    save();
  }

  const recentKey = (id) => `photoLook:${family.value}:${context}:${id}`;
  const entries = (id) => (recent.value[recentKey(id)] || []).filter(Boolean);

  /** Store category *id*'s entries as *change(list)* makes them. */
  function setEntries(id, change) {
    writeChoices((all) => {
      const lists = { ...all.recent };
      lists[recentKey(id)] = change(lists[recentKey(id)] || []);
      all.recent = lists;
      recent.value = lists;
    });
  }

  /** Take one remembered text off a row's list; a field showing it empties. */
  function remove(id, text) {
    setEntries(id, (list) => list.filter((entry) => !same(entry, text)));
    if (same(current(id).text, text)) {
      state.choices = { ...state.choices, [id]: "none" };
      save();
    }
  }

  function clearAll(id) {
    if (current(id).text) {
      state.choices = { ...state.choices, [id]: "none" };
      save();
    }
    setEntries(id, () => []);
  }

  /** One row back to following its shipped default. */
  function resetRow(id) {
    const { [id]: _gone, ...rest } = state.choices;
    state.choices = rest;
    save();
  }

  /** The shipped defaults again, On included. Typed entries stay. */
  function reset() {
    state.on = undefined;
    state.choices = {};
    save();
  }

  const changed = computed(
    () =>
      on.value !== defaultOn.value ||
      categories.value.some((cat) => !isDefault(cat.id)),
  );

  /** `prompt_assist` for the request body: null when off. */
  function request() {
    if (!on.value) return null;
    return {
      context,
      choices: Object.fromEntries(
        categories.value.map((cat) => [cat.id, encodeChoice(current(cat.id))]),
      ),
    };
  }

  /** A run was sent: its typed texts join their rows' lists, newest first. */
  function recordRun() {
    if (!on.value) return;
    for (const cat of categories.value) {
      const text = current(cat.id).text;
      if (text) setEntries(cat.id, (list) => withRecent(list, text));
    }
  }

  return {
    categories,
    on,
    setOn,
    hasStoredOn: () => state.on !== undefined,
    current,
    fieldText,
    addedText,
    isDefault,
    defaultLabel,
    commit,
    entries,
    remove,
    clearAll,
    resetRow,
    reset,
    changed,
    request,
    recordRun,
  };
}
