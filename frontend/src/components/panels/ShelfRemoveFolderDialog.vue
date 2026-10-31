<template>
  <!-- Destructive: no @accept, so Enter never removes. Escape, the scrim and
       the close button cancel, except while the removal runs: it cannot be
       called back, so nothing may look like a cancel then. -->
  <AppDialog
    :open="open"
    role="alertdialog"
    title="Remove folder"
    :subtitle="source.path"
    size="lg"
    :persistent="working"
    @close="dismiss"
  >
    <p class="srf-lead" data-testid="remove-folder-lead">{{ lead }}</p>
    <p v-if="changed" class="srf-note" role="status">
      The folder changed since it was read. This is what is in it now.
    </p>

    <div class="srf-columns">
      <section class="srf-col" aria-labelledby="srf-goes">
        <h3 id="srf-goes" class="srf-head">Goes to the {{ trash }}</h3>
        <ul class="srf-files" data-testid="remove-folder-files">
          <li v-for="file in facts.files" :key="file.relpath" class="srf-file">
            <span class="srf-name">{{ file.relpath }}</span>
            <span v-if="file.link" class="srf-tag">link · its target stays</span>
            <span v-else-if="file.model_id == null" class="srf-tag">
              not listed
            </span>
            <span class="srf-size">{{ formatModelSize(file.size) }}</span>
          </li>
        </ul>
      </section>

      <section class="srf-col" aria-labelledby="srf-breaks">
        <h3 id="srf-breaks" class="srf-head">Breaks outside this folder</h3>
        <template v-for="group in breakGroups" :key="group.key">
          <h4 class="srf-sub">{{ group.label }}</h4>
          <ul class="srf-breaks" :data-testid="`remove-folder-${group.key}`">
            <li v-for="entry in group.entries" :key="entry.id">
              <span class="srf-name">{{ entry.name }}</span>
              <span class="srf-uses">loses {{ entry.models.join(", ") }}</span>
            </li>
          </ul>
        </template>
        <p v-if="!hasBreaks" class="srf-calm">
          Nothing outside this folder uses these models.
        </p>
        <p v-if="kept" class="srf-calm">{{ kept }}</p>
        <p v-if="facts.pictures" class="srf-calm" data-testid="remove-folder-pictures">
          {{ picturesWords }}
        </p>
      </section>
    </div>

    <label v-if="nameShown" class="srf-type">
      <span class="srf-type-hint">{{ typeHint }}</span>
      <input
        ref="nameInput"
        v-model="typed"
        class="srf-type-input"
        data-testid="remove-folder-name"
        type="text"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
        :placeholder="facts.name"
        :aria-label="`Type ${facts.name} to confirm`"
      />
    </label>

    <p v-if="refusal" class="srf-refusal" role="alert">{{ refusal }}</p>

    <template #footer>
      <AppButton
        variant="ghost"
        key-hint="esc"
        :disabled="working"
        @click="dismiss"
      >
        Cancel
      </AppButton>
      <AppButton
        variant="secondary"
        data-testid="remove-folder-move-out"
        :disabled="!facts.copies?.length || working"
        @click="moveModelsOut"
      >
        Move the models out first…
      </AppButton>
      <AppButton
        variant="danger"
        data-testid="remove-folder-confirm"
        :loading="working"
        :disabled="!canConfirm"
        @click="confirm"
      >
        {{ confirmLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
// Removing a folder that holds files (#2207). An empty one never gets here.
//
// What goes is on the left, every file of it; what breaks outside the folder
// is on the right. The folder's name must be typed when something outside
// breaks, or when the removal is permanent. Shift turns the button into
// Permanently delete, and the click's own `shiftKey` is what decides: the
// tracked key only changes the label, as on the selection pill.

import { computed, nextTick, onUnmounted, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import {
  planFolderRemove,
  removeFolder,
} from "../../composables/useModelFolderRemove";
import { errorMessage } from "../../utils/apiError";
import { formatModelSize, trashName } from "../../utils/modelShelf";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `{folderId, path, name}` of the directory being removed. */
  source: { type: Object, default: () => ({ folderId: 0, path: "", name: "" }) },
  /** The dry run's answer, read before the dialog opened. */
  plan: { type: Object, default: null },
});

const emit = defineEmits(["close", "move-models"]);

const EMPTY = { files: [], models: [], copies: [], breaks: {}, pictures: 0, name: "" };

/** The plan shown: the prop, or a fresh read after the folder changed. */
const facts = ref(EMPTY);
const typed = ref("");
const askedName = ref(false);
const changed = ref(false);
const working = ref(false);
const refusal = ref("");
const nameInput = ref(null);
const trash = trashName();

// Cosmetic only, as on the selection pill: the label follows it, the click's
// own `shiftKey` decides.
const shiftHeld = ref(false);
function trackShift(event) {
  shiftHeld.value = Boolean(event.shiftKey);
}
function dropShift() {
  shiftHeld.value = false;
}
function listen(on) {
  const method = on ? "addEventListener" : "removeEventListener";
  window[method]("keydown", trackShift);
  window[method]("keyup", trackShift);
  window[method]("blur", dropShift);
}

watch(
  () => props.open,
  (open) => {
    listen(open);
    if (!open) return;
    facts.value = props.plan ?? EMPTY;
    typed.value = "";
    askedName.value = false;
    changed.value = false;
    working.value = false;
    refusal.value = "";
    shiftHeld.value = false;
  },
  { immediate: true },
);
onUnmounted(() => listen(false));

const plural = (n, one, many) =>
  `${Number(n).toLocaleString()} ${n === 1 ? one : many}`;

const lead = computed(() => {
  const files = facts.value.files?.length ?? 0;
  const bytes = (facts.value.files ?? []).reduce((sum, f) => sum + (f.size || 0), 0);
  return `${facts.value.name}: ${plural(files, "file", "files")}, ${formatModelSize(bytes)}.`;
});

const breakGroups = computed(() =>
  [
    { key: "people", label: "People" },
    { key: "sets", label: "Picture sets" },
    { key: "workflows", label: "Workflows" },
  ]
    .map((group) => ({ ...group, entries: facts.value.breaks?.[group.key] ?? [] }))
    .filter((group) => group.entries.length),
);

const hasBreaks = computed(() => breakGroups.value.length > 0);

const kept = computed(() => {
  const n = (facts.value.models ?? []).filter((m) => m.elsewhere).length;
  if (!n) return "";
  return `${plural(n, "model has", "models have")} a copy elsewhere and ${n === 1 ? "stays" : "stay"} on the shelf.`;
});

const picturesWords = computed(() => {
  const n = Number(facts.value.pictures) || 0;
  return `${plural(n, "picture", "pictures")} in this library ${n === 1 ? "was" : "were"} made with these models. They stay.`;
});

/** Typing the name is asked for when something outside breaks, or on Shift. */
const nameShown = computed(
  () => hasBreaks.value || shiftHeld.value || askedName.value || typed.value !== "",
);
// Trimmed on both sides: a folder made outside the app can end in a space, and
// a typed name that strips it must still confirm.
const nameOk = computed(
  () => typed.value.trim() === String(facts.value.name ?? "").trim(),
);

const typeHint = computed(() =>
  hasBreaks.value
    ? `Something outside this folder breaks. Type ${facts.value.name} to remove it.`
    : `A permanent delete has no undo. Type ${facts.value.name} to confirm.`,
);

const confirmLabel = computed(() =>
  shiftHeld.value ? "Permanently delete" : `Move folder to ${trash}`,
);

// Gated on the breaks only, never on the tracked Shift: a Shift-click with no
// name typed must reach `confirm`, which opens the field and keeps it open.
// Gating it here would leave the field visible only while Shift is held, and
// a name typed with Shift held is in capitals.
const canConfirm = computed(
  () =>
    !working.value &&
    !!facts.value.fingerprint &&
    (!hasBreaks.value || nameOk.value),
);

/** Close, unless the removal is in flight and could not be stopped. */
function dismiss() {
  if (!working.value) emit("close");
}

async function confirm(event) {
  if (working.value) return;
  const permanent = Boolean(event?.shiftKey);
  if ((hasBreaks.value || permanent) && !nameOk.value) {
    askedName.value = true;
    await nextTick();
    nameInput.value?.focus();
    return;
  }
  working.value = true;
  refusal.value = "";
  try {
    await removeFolder(props.source, facts.value, permanent);
    emit("close");
  } catch (err) {
    const said = errorMessage(err, `Could not remove ${facts.value.name}.`);
    if (err?.response?.status !== 409) {
      refusal.value = said;
      return;
    }
    // A 409 is a folder that changed, or one this cannot remove right now (a
    // move running). Read it again: a new fingerprint is the first, and what
    // is shown becomes what a second press would remove.
    try {
      const fresh = await planFolderRemove(props.source);
      if (fresh.fingerprint !== facts.value.fingerprint) {
        facts.value = fresh;
        changed.value = true;
      } else {
        refusal.value = said;
      }
    } catch (again) {
      refusal.value = errorMessage(again, said);
    }
  } finally {
    working.value = false;
  }
}

function moveModelsOut() {
  const copies = facts.value.copies ?? [];
  emit("move-models", {
    items: copies.map((copy) => ({ folder_id: copy.folder_id, relpath: copy.relpath })),
    totalBytes: copies.reduce((sum, copy) => sum + (copy.size || 0), 0),
  });
}
</script>

<style scoped>
.srf-lead {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.srf-note {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.srf-columns {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: var(--space-5);
}

@media (max-width: 600px) {
  .srf-columns {
    grid-template-columns: minmax(0, 1fr);
  }
}

.srf-col {
  min-width: 0;
}

.srf-head {
  margin: 0 0 var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
}

.srf-sub {
  margin: var(--space-3) 0 var(--space-1);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.srf-files,
.srf-breaks {
  list-style: none;
  margin: 0;
  padding: 0;
}

/* Scrolls inside the dialog: a folder of previews can hold hundreds. */
.srf-files {
  max-height: 40vh;
  overflow-y: auto;
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
}

.srf-file {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-xs);
}

.srf-file + .srf-file {
  border-top: 1px solid rgb(var(--v-theme-divider));
}

.srf-name {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  color: rgb(var(--v-theme-on-surface));
}

.srf-file .srf-name {
  font-family: var(--font-mono);
}

.srf-tag {
  flex-shrink: 0;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.srf-size {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.srf-breaks li {
  display: flex;
  flex-direction: column;
  padding: var(--space-1) 0;
  font-size: var(--text-sm);
}

.srf-uses {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.srf-calm {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.srf-type {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.srf-type-hint {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The input DeleteForeverDialog draws for its typed confirmation. */
.srf-type-input {
  font: inherit;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-input-text));
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
}

.srf-refusal {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-error));
}
</style>
