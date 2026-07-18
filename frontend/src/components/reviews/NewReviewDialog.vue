<template>
  <div class="rs-dialog-backdrop" @click.self="emit('close')">
    <div
      class="rs-dialog"
      role="dialog"
      aria-modal="true"
      aria-label="New review"
    >
      <h3 class="rs-dialog-title">New review</h3>

      <label class="rs-dialog-field">
        <span class="rs-dialog-label">
          Tag
          <span class="rs-dialog-label-note"
            >({{ visible.length }} of {{ allRows.length }} tags · one open
            review per tag)</span
          >
        </span>
        <div class="rs-dialog-tagbar">
          <div class="rs-dialog-search">
            <v-icon size="16" class="rs-dialog-search-icon">mdi-magnify</v-icon>
            <input
              ref="searchRef"
              v-model="q"
              type="text"
              placeholder="Filter tags…"
              @keydown.escape.stop.prevent="emit('close')"
            />
          </div>
          <div class="rs-dialog-order">
            <button
              v-for="[v, lbl] in [
                ['suggested', 'Suggested'],
                ['alpha', 'Alphabetical'],
              ]"
              :key="v"
              class="rs-dialog-order-btn"
              :class="{ 'rs-dialog-order-btn--on': order === v }"
              type="button"
              @click="order = v"
            >
              {{ lbl }}
            </button>
          </div>
        </div>
        <div class="rs-dialog-chips">
          <button
            v-for="h in visible"
            :key="h.tag"
            class="rs-dialog-chip"
            :class="{
              'rs-dialog-chip--active': tag === h.tag,
              'rs-dialog-chip--open': openTags.has(h.tag),
              'rs-dialog-chip--anomaly': store.isAnomalyTag(h.tag),
            }"
            type="button"
            :title="
              openTags.has(h.tag)
                ? 'Already open — jump to the session'
                : undefined
            "
            @click="pickTag(h.tag)"
          >
            <v-icon
              v-if="store.isAnomalyTag(h.tag)"
              size="12"
              class="rs-dialog-chip-flag"
              >mdi-alert-octagon-outline</v-icon
            >
            {{ h.tag }}{{ openTags.has(h.tag) ? " · open" : "" }}
          </button>
          <span v-if="!visible.length" class="rs-dialog-nomatch"
            >No tags match “{{ q }}”.</span
          >
        </div>
      </label>

      <div class="rs-dialog-scopes">
        <label class="rs-dialog-scope">
          <span class="rs-dialog-label">Project</span>
          <select v-model="projectId" @change="$event.target.blur()">
            <option :value="null">Any</option>
            <option v-for="p in store.projects" :key="p.id" :value="p.id">
              {{ p.name || `Project ${p.id}` }}
            </option>
          </select>
        </label>
        <label class="rs-dialog-scope">
          <span class="rs-dialog-label">Set</span>
          <select v-model="setId" @change="$event.target.blur()">
            <option :value="null">Any</option>
            <option v-for="s in store.sets" :key="s.id" :value="s.id">
              {{ s.name || `Set ${s.id}` }}
            </option>
          </select>
        </label>
        <label class="rs-dialog-scope">
          <span class="rs-dialog-label">Character</span>
          <select v-model="characterId" @change="$event.target.blur()">
            <option :value="null">Any</option>
            <option value="UNASSIGNED">Unassigned</option>
            <option v-for="c in store.characters" :key="c.id" :value="c.id">
              {{ c.name || `Character ${c.id}` }}
            </option>
          </select>
        </label>
      </div>
      <p class="rs-dialog-frozen">
        Scope is frozen when the review is created — a different scope is a
        different review.
      </p>

      <div v-if="tag" class="rs-dialog-preview">
        <div class="rs-dialog-preview-title">
          <v-icon size="15" class="rs-dialog-preview-icon">mdi-radar</v-icon>
          Scan preview
        </div>
        <div class="rs-dialog-preview-body">
          The near-neighbour scan runs once, on create; its report becomes this
          review’s cover sheet.
        </div>
        <label class="rs-dialog-include">
          <input v-model="includeReviewed" type="checkbox" />
          <span
            >Include suspects handled in earlier reviews
            <span class="rs-dialog-include-note"
              >(normally left out — this re-surfaces them)</span
            ></span
          >
        </label>
      </div>

      <p v-if="store.createError" class="rs-dialog-error">
        {{ store.createError }}
      </p>

      <div class="rs-dialog-actions">
        <button class="rs-dialog-btn" type="button" @click="emit('close')">
          Cancel
        </button>
        <button
          class="rs-dialog-btn rs-dialog-btn--go"
          type="button"
          :disabled="!tag || store.creating"
          @click="create"
        >
          <v-icon size="15">{{
            store.creating ? "mdi-loading mdi-spin" : "mdi-radar"
          }}</v-icon>
          Scan &amp; create
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
// New-review dialog: explicit creation. Open tags stay ENABLED — clicking one
// jumps to the open session instead of dead-ending on a disabled chip.
import { computed, onMounted, ref } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";

const props = defineProps({
  preset: { type: String, default: "" },
  // Prefilled from the app's launch context (project/set/character selection).
  initialScope: {
    type: Object,
    default: () => ({ projectId: null, setId: null, characterId: null }),
  },
});
const emit = defineEmits(["close"]);

const store = useReviewSessionsStore();

const tag = ref(props.preset || "");
const q = ref("");
const order = ref("suggested");
const includeReviewed = ref(false);
const projectId = ref(props.initialScope.projectId ?? null);
const setId = ref(props.initialScope.setId ?? null);
const characterId = ref(props.initialScope.characterId ?? null);
const searchRef = ref(null);

onMounted(() => searchRef.value?.focus());

const openTags = computed(() => new Set(store.sessions.map((s) => s.tag)));

const allRows = computed(() => store.healthRows);

function corrections(r) {
  return (r.est_wrong ?? 0) + (r.est_missing ?? 0) + (r.mismatch ?? 0);
}

const visible = computed(() => {
  const needle = q.value.trim().toLowerCase();
  return allRows.value
    .filter((h) => !needle || h.tag.toLowerCase().includes(needle))
    .slice()
    .sort((a, b) =>
      order.value === "alpha"
        ? a.tag.localeCompare(b.tag)
        : corrections(b) - corrections(a) || a.tag.localeCompare(b.tag),
    );
});

function pickTag(t) {
  const open = store.sessions.find((s) => s.tag === t);
  if (open) {
    // Already open: jump to it instead of dead-ending.
    store.openSession(open.id);
    emit("close");
    return;
  }
  tag.value = t;
}

async function create() {
  const review = await store.createReview({
    tag: tag.value,
    projectId: projectId.value,
    setId: setId.value,
    characterId: characterId.value,
    includeReviewed: includeReviewed.value,
  });
  if (review) emit("close");
}
</script>

<style scoped>
.rs-dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 4300;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.45);
}
.rs-dialog {
  width: 480px;
  max-width: calc(100vw - 32px);
  max-height: calc(100vh - 64px);
  overflow-y: auto;
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  border-radius: var(--radius-md);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.rs-dialog :is(button, input, select):focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
}
.rs-dialog-title {
  font-size: 16px;
  font-weight: var(--weight-bold);
}
.rs-dialog-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rs-dialog-label {
  font-size: 11px;
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-dialog-label-note {
  text-transform: none;
  letter-spacing: 0;
}
.rs-dialog-tagbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.rs-dialog-search {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
}
.rs-dialog-search-icon {
  position: absolute;
  left: 9px;
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  pointer-events: none;
}
.rs-dialog-search input {
  width: 100%;
  height: 30px;
  padding: 0 10px 0 30px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: 13px;
}
.rs-dialog-order {
  display: inline-flex;
  flex-shrink: 0;
  padding: 2px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}
.rs-dialog-order-btn {
  height: 24px;
  padding: 0 10px;
  border: none;
  border-radius: calc(var(--radius-sm) - 2px);
  cursor: pointer;
  font-size: 12px;
  font-weight: var(--weight-semibold);
  background: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-dialog-order-btn--on {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}
.rs-dialog-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  max-height: 190px;
  overflow-y: auto;
  padding: 2px;
}
.rs-dialog-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 28px;
  padding: 0 11px;
  border-radius: 999px;
  cursor: pointer;
  font-size: 12.5px;
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-dialog-chip--active {
  border-color: rgb(var(--v-theme-accent));
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 15%, transparent);
  color: rgb(var(--v-theme-accent));
}
.rs-dialog-chip--open {
  opacity: 0.55;
}
.rs-dialog-chip--anomaly {
  color: rgb(var(--v-theme-error));
}
.rs-dialog-chip-flag {
  flex-shrink: 0;
}
.rs-dialog-nomatch {
  font-size: 12.5px;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  padding: 4px 2px;
}

.rs-dialog-scopes {
  display: flex;
  gap: 8px;
}
.rs-dialog-scope {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.rs-dialog-scope select {
  height: 30px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: 12px;
  cursor: pointer;
  color-scheme: dark;
}
.rs-dialog-scope option {
  background-color: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-dialog-frozen {
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  margin-top: -6px;
}

.rs-dialog-preview {
  padding: 10px 13px;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-dark-surface), 0.05);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  font-size: 13px;
}
.rs-dialog-preview-title {
  font-weight: var(--weight-semibold);
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 5px;
}
.rs-dialog-preview-icon {
  color: rgb(var(--v-theme-accent));
}
.rs-dialog-preview-body {
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
}
.rs-dialog-include {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  cursor: pointer;
}
.rs-dialog-include input {
  accent-color: rgb(var(--v-theme-primary));
}
.rs-dialog-include-note {
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  font-size: 12px;
}

.rs-dialog-error {
  font-size: 12.5px;
  color: rgb(var(--v-theme-error));
}

.rs-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.rs-dialog-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 14px;
  cursor: pointer;
  border-radius: var(--radius-sm);
  font-size: 13.5px;
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-dialog-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.rs-dialog-btn--go {
  border-color: rgb(var(--v-theme-accent));
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 16%, transparent);
  color: rgb(var(--v-theme-accent));
}
</style>
