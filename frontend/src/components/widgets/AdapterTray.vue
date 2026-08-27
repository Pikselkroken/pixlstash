<template>
  <!-- Three ways to render nothing, and they are different facts: no entity yet
       (nothing to attach to), no answer yet (nothing earned), and refused (not
       ours to say). Only a read that came back empty may say "none". Rationale:
       frontend_architecture.md, `AdapterTray.vue`. -->
  <div
    v-if="filterKey && numericId !== null && settled && !refused"
    class="adapter-tray"
  >
    <div class="adapter-tray__header">
      <span :id="headingId" class="section-label">Adapters</span>
      <!-- The count is a claim about the entity, so it is only made when the
           read was whole. Over a partial failure it would be the confident
           wrong number this component exists to not print; the error line
           under it says the list is short, and a figure beside that reads as
           the total anyway. -->
      <span v-if="adapters.length && !errorText" class="adapter-tray__count">
        {{ adapters.length }} attached
      </span>
    </div>
    <!-- Named through the heading, or a screen reader gets "list, 3 items". -->
    <ul
      v-if="adapters.length"
      class="adapter-tray__grid"
      :aria-labelledby="headingId"
    >
      <!-- Keyed on the hub `model.id`: every row carries one, and it is what
           the shelf's other verbs address a row by. -->
      <li
        v-for="row in adapters"
        :key="row.id"
        class="adapter-card"
        :title="cardTitle(row)"
      >
        <ModelMark :row="row" />
        <span class="adapter-card__body">
          <span class="adapter-card__name">{{ nameOf(row) }}</span>
          <span class="adapter-card__meta">{{
            row.base_model || "Base model not set"
          }}</span>
          <span v-if="row.trigger_words" class="adapter-card__trigger">{{
            row.trigger_words
          }}</span>
        </span>
      </li>
    </ul>
    <!-- `v-if`, not `v-else`: an error sits UNDER a partial list. The rows that
         arrived are true AND the list is short, and both have to be on screen. -->
    <p v-if="errorText" class="adapter-tray__error" role="alert">
      {{ errorText }}
    </p>
    <p v-else-if="!adapters.length && !loading" class="adapter-tray__empty">
      No adapters yet - assign them from the Models shelf.
    </p>
  </div>
</template>

<script setup>
// The adapters one person or one set uses, as a read-only tray.
//
// Assigning still happens on the shelf, where the whole library of adapters is
// in front of you; this end only answers "which ones does this person use",
// which is the question the editor is open for. That keeps the tray a display
// and off the `PUT /adapters/{sha256}/attachments` path, whose replace-the-
// whole-set semantics need the other entities' rows to write safely.
//
// Filtered by the route's own `character_id` / `set_id`. That buys the wire, not
// the query - the server intersects in Python because the hub and the vault are
// separate SQLite files - but it keeps the definition of "attached" server-side,
// which is the half that matters here.

import { computed, ref, useId, watch } from "vue";

import { listAdapters } from "../../api/modelShelf";
import { errorDetail } from "../../utils/apiError";
import { modelName } from "../../utils/modelShelf";
import ModelMark from "./ModelMark.vue";

// Both kinds an attachment can be read back through. `unknown` is here because
// the server lets an unclassified file be attached - of the file kinds, the
// attach route rejects only a checkpoint (400) and an engine (409) - and asking
// for `adapter` alone told the owner "no adapters yet" about a person whose
// shelf row showed their mark. Two requests, because the route's `file_kind`
// Query is a single `str`. The cost, the one-line backend fix, and the
// checkpoint gap this leaves: frontend_architecture.md, `AdapterTray.vue`.
const ATTACHABLE_FILE_KINDS = ["adapter", "unknown"];

// Which `listAdapters` filter each entity type selects. A lookup rather than a
// `type === "character" ? … : …`, because a ternary makes every value that is
// not the one it names - a typo, a type the app grows later - silently mean
// "set", and the failure is a picture set's adapters rendered under a person's
// name. A validator warns in development; this is what fails closed in
// production, and `filterKey` below is the only thing that reads it.
const FILTER_KEY = { character: "characterId", set: "setId" };

const props = defineProps({
  /**
   * `character` or `set` - which filter the id is for. Spelled out rather than
   * read off `FILTER_KEY`, which `defineProps` is hoisted above and cannot see;
   * the runtime guard on the watcher is the one that fails closed.
   */
  entityType: {
    type: String,
    required: true,
    validator: (v) => v === "character" || v === "set",
  },
  /** The entity's id, or null while it has none yet (an unsaved create). */
  entityId: { type: [Number, String], default: null },
});

const adapters = ref([]);
// One-way: nothing is claimed before the first answer, and a re-read empties the
// section rather than tearing it down. `loading` is the other half - it keeps
// the empty line from claiming "none" in the gap between the two.
const settled = ref(false);
const loading = ref(false);
const refused = ref(false);
const errorText = ref("");

// Unique per instance, so the person editor and the set editor can both be
// mounted without their two lists pointing `aria-labelledby` at one id.
const headingId = useId();

/**
 * The filter this entity type selects, or null when it names none.
 *
 * `Object.hasOwn`, not `in`: `in` walks the prototype chain, so `constructor`
 * and `toString` would pass the check and resolve to a function, which spreads
 * into the request as a key no route declares - and FastAPI drops what it does
 * not declare, so the "filtered" read comes back as every adapter on the
 * machine, rendered under one entity's name. Same failure the wire test in
 * `api/modelShelf.test.js` exists to prevent, one line away from it.
 *
 * The TEMPLATE gates on this too, not only the watcher. `settled` is one-way,
 * so a type that goes invalid after a successful read would otherwise leave the
 * section standing with its rows cleared, saying "No adapters yet" about an
 * entity it can no longer address.
 */
const filterKey = computed(() =>
  Object.hasOwn(FILTER_KEY, props.entityType)
    ? FILTER_KEY[props.entityType]
    : null,
);

// The empty cases go first: `Number(null)` and `Number("")` are both 0, so an
// unsaved entity would otherwise read as id 0 and get filtered on.
const numericId = computed(() => {
  const raw = props.entityId;
  if (raw === null || raw === undefined || raw === "") return null;
  const id = Number(raw);
  // `isInteger`, not `isFinite`: these are row ids. `7.5` is finite and would
  // reach an `Optional[int]` Query as a 422 the reader has to interpret.
  return Number.isInteger(id) ? id : null;
});

/**
 * What to call a row. `modelName` returns "" when there is no title and no
 * filename either - a field inviting a rename on the shelf, a hole in a card
 * here.
 */
function nameOf(row) {
  return modelName(row).text || "Unnamed adapter";
}

/**
 * The hover text: the name, plus the file when that says something more. The
 * name is one ellipsised line in a ~180px track, so it is the half most likely
 * to be truncated and the half a bare `title="filename"` left out.
 */
function cardTitle(row) {
  const name = nameOf(row);
  const filename = String(row?.filename || "").trim();
  return filename && filename !== name ? `${name} - ${filename}` : name;
}

// Only the newest flight may write, or a slow read for one person lands last and
// paints their adapters into another person's dialog. Same guard and same
// reason as `useModelShelfStore`'s `epoch` (frontend_architecture §4).
let epoch = 0;

/**
 * Everything this component claims about an entity, back to claiming nothing.
 *
 * NOT `refused`: that is what the session was told about the shelf, not a claim
 * about the entity, and clearing it per read blinked a refused tray into view
 * and out again. It is reassigned from each answer instead.
 */
function reset() {
  adapters.value = [];
  errorText.value = "";
}

/**
 * When a row joined the shelf. `newest_member_at` first, the rule
 * `useModelShelfStore`'s accessor applies: a stack's date is its newest
 * member's, never its cover's, or one relation sorts two ways in two views.
 */
function addedAt(row) {
  return row?.newest_member_at || row?.added_at || "";
}

async function fetchAdapters(id) {
  const mine = ++epoch;
  // Cleared BEFORE the await. The epoch only stops a late answer from winning;
  // the rows already on screen belong to the previous entity and would sit
  // under this one's name for the length of the read.
  reset();
  loading.value = true;
  const filter = { [filterKey.value]: id };
  // `allSettled`, not `all`: one kind failing must not throw away the other's
  // rows.
  const flights = await Promise.allSettled(
    ATTACHABLE_FILE_KINDS.map((fileKind) =>
      listAdapters({ ...filter, fileKind }),
    ),
  );
  if (mine !== epoch) return;
  loading.value = false;
  settled.value = true;

  // The failure matrix, decided by two counts rather than cell by cell -
  // patching it a case at a time is what let a partial 403 fall between "every
  // flight refused" and "one flight failed" and print "No adapters yet".
  // Exactly one outcome hides, and the table is in frontend_architecture.md.
  //
  // `refused` means "this session may not read the shelf", which is only what a
  // WHOLLY refused read says: a refusal standing beside a success is a session
  // that changed underneath two concurrent requests - a fault to report, not a
  // permission to respect.
  const failures = flights.filter((f) => f.status === "rejected");
  const total = failures.length === flights.length;
  const allRefusals = failures.every((f) => f.reason?.response?.status === 403);

  refused.value = total && allRefusals;
  if (refused.value) return;

  // Which reason to show, chosen by KIND and not by array position: a 403
  // carries no `detail`, so picking it over a 500 standing beside it costs the
  // reader the only sentence that said what broke.
  const fault = (
    failures.find((f) => f.reason?.response?.status !== 403) ?? failures[0]
  )?.reason;

  if (failures.length) {
    console.error(
      total
        ? "Failed to list the adapters attached to this entity."
        : "One file kind failed while listing this entity's adapters; showing the rest.",
      { entityType: props.entityType, entityId: id },
      fault,
    );
  }

  adapters.value = flights
    .filter((f) => f.status === "fulfilled")
    .flatMap((f) => f.value)
    // Newest first across both kinds, which is what each call already returns
    // within its own. Compared as plain strings rather than with
    // `localeCompare`: these are ISO-8601 timestamps, which order lexically by
    // construction, and a locale-aware collation is both slower and free to
    // disagree with that. A row with no date falls out last for free - "" is
    // below every real timestamp, and last is where the shelf puts a row with
    // no value for the key it is sorting on.
    .sort((a, b) => {
      const x = addedAt(a);
      const y = addedAt(b);
      return x < y ? 1 : x > y ? -1 : 0;
    });

  // Our sentence leads, because whether this is ALL the adapters or only some
  // of them is the part no server detail states. `errorMessage` is not used
  // here for that reason alone - it is `errorDetail || err.message || fallback`
  // and would return one of those INSTEAD of the lead; its transport fallback
  // is worth having, so it is appended by hand. A dead backend rejects with no
  // `response` at all, and "Couldn't read the adapters." on its own leaves the
  // reader nothing to act on.
  if (failures.length) {
    const lead = total
      ? "Couldn't read the adapters."
      : "Some adapters couldn't be read, so this list may be short.";
    const why = errorDetail(fault) || fault?.message || "";
    errorText.value = `${lead} ${why}`.trim();
  }
}

// Both, because either alone selects the wrong rows: the id picks the entity,
// the type picks which filter it is. A refusal is read off the response rather
// than predicted from `sessionContext` - the server owns who may read the shelf
// and a second statement of it here would drift.
watch(
  [numericId, filterKey],
  ([id]) => {
    if (id === null || !filterKey.value) {
      // The epoch moves even though nothing is displayed: a read still in
      // flight belongs to an entity this is no longer pointed at, and letting
      // it land would log a failure against an abandoned id.
      epoch += 1;
      reset();
      return;
    }
    fetchAdapters(id);
  },
  { immediate: true },
);
</script>

<style scoped>
.adapter-tray {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-2);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.adapter-tray__header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* 0.7 rather than the 0.5–0.6 this app drifted to for secondary text: composited
   on `surface` it measures 5.94:1 in light and 6.65:1 in dark, so every string
   in the tray clears the 4.5:1 body floor (§4 Contrast). 0.6 is 4.31:1 in light
   and misses it. Rank here is size and weight, never opacity - the name is
   `--text-sm` medium and everything under it `--text-xs` regular.

   The heading is the shared `.section-label` and is NOT restyled here (§3: use
   it, do not re-roll it), and it carries no local colour here. Adding this
   heading is what surfaced that the class shipped at alpha 0.5 = 3.19:1, under
   the 4.5:1 floor §4 sets for 11px text; the lead-designer ruling was to fix it
   at 0.7 in `style.css` for every label at once, which is in this change. */
.adapter-tray__count,
.adapter-tray__empty,
.adapter-card__meta,
.adapter-card__trigger {
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.adapter-tray__count {
  font-size: var(--text-xs);
}

.adapter-tray__empty,
.adapter-tray__error {
  font-size: var(--text-sm);
  margin: 0;
}

.adapter-tray__empty {
  font-style: italic;
}

.adapter-tray__error {
  color: rgb(var(--v-theme-error));
}

.adapter-tray__grid {
  display: grid;
  /* Three per row in both 720-wide editors, where the tray spans both columns,
     one on a narrow window - the same card either way, so neither editor needs
     its own tray. (The tray only renders for a saved entity, which in the
     person editor is exactly the case that is 720 rather than 480.) */
  /* `min(180px, 100%)` rather than a bare 180px: a hard minimum track cannot
     shrink, so below 180px of content width the grid would overflow its dialog
     sideways instead of reflowing to one narrower column. */
  grid-template-columns: repeat(auto-fill, minmax(min(180px, 100%), 1fr));
  gap: var(--space-3);
  /* No height cap and no scroller of its own: `AppDialog` scrolls its body and
     keeps the footer outside it, so no number of cards can push Save out of
     reach, and a scroller nested in that one would strand the rows past its cap
     from the keyboard. */
  list-style: none;
  margin: 0;
  padding: 0;
}

.adapter-card {
  /* `ModelMark` at the shared `--entity-thumb`, not a local override of it. A
     card has room for a bigger mark, but nothing in the app re-assigns that
     token today (design-tokens.css says the sidebar does; it does not - it has
     its own `--sidebar-thumb-size`), and being the first to do it for a size
     preference is a design decision, not a layout detail. */
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
}

.adapter-card__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.adapter-card__name {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
  line-height: var(--leading-snug);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.adapter-card__meta,
.adapter-card__trigger {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.adapter-card__trigger {
  font-family: var(--font-mono);
}
</style>
