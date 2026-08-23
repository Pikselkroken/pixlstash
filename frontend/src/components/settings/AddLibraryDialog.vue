<script setup>
/**
 * Settings › Libraries › Add a library.
 *
 * One picker, and the folder answers. The owner names a folder; the server says
 * which of five things it is and, for the three that can be added, what adding
 * it would mean. There is no mode to choose first, because "attach the library
 * I already made" and "start a new one here" are the same gesture with a
 * different consequence, and only the folder knows which.
 *
 * **The refusals are the server's words, not ours.** `headline` and `detail`
 * arrive with the verdict, so the sentence that names the library covering this
 * folder is written once, where the rule lives. This component branches on
 * `can_add` and nothing else.
 *
 * Browsing reuses `FolderBrowser`, including its `New folder` — the add route
 * deliberately creates no directory, so making one is the picker's job and it
 * already had a button for it.
 */
import { computed, nextTick, ref, watch } from "vue";

import { addLibrary, inspectLibraryPath } from "../../api/libraries";
import { errorDetail } from "../../utils/apiError";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  // Paths already registered, so the browser can grey them out before the
  // owner walks into one and is told no.
  registeredPaths: { type: Array, default: () => [] },
  // Docker serves container paths and has no host filesystem to browse, so the
  // dialog degrades to a typed path rather than offering a picker that lies.
  inDocker: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "added"]);

// What each addable verdict calls its button. Pure labels — every word that
// carries a fact about this folder comes from the server.
const ACTION_LABELS = {
  vault: "Add it",
  pictures: "Bring them in",
  empty: "Start it",
};

const path = ref("");
const name = ref("");
/** True once the owner edits the name, so a new verdict stops overwriting it. */
const nameEdited = ref(false);
const verdict = ref(null);
const inspecting = ref(false);
const inspectError = ref("");
const adding = ref(false);
const addError = ref("");
const browserOpen = ref(false);
const pathInput = ref(null);

/** The path the current verdict describes, so a stale answer is never acted on.
    A ref, not a plain `let`: `canAdd` reads it. */
const inspectedPath = ref("");
/** Discards an inspection that was still on the wire when the path changed. */
let inspectEpoch = 0;
/** The last path asked about, so `@blur` on an unchanged field is a no-op. */
let lastAsked = "";

const actionLabel = computed(
  () => ACTION_LABELS[verdict.value?.verdict] ?? "Add",
);

const canAdd = computed(
  () =>
    Boolean(verdict.value?.can_add) &&
    verdict.value?.path === inspectedPath.value,
);

watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    inspectEpoch += 1;
    lastAsked = "";
    path.value = "";
    name.value = "";
    nameEdited.value = false;
    verdict.value = null;
    inspectedPath.value = "";
    inspectError.value = "";
    addError.value = "";
    // Bumping the epoch above orphans any request still on the wire, and its
    // `finally` is epoch-guarded — so without this the reopened dialog shows
    // "Reading that folder…" over an empty field until the next inspect lands.
    inspecting.value = false;
    await nextTick();
    pathInput.value?.focus();
  },
  { immediate: true },
);

async function inspect() {
  const candidate = path.value.trim();

  // Re-asking about the folder already answered is a no-op, and it has to be.
  // `@blur` fires this, and a browser orders mousedown -> blur -> click: without
  // this guard, clicking the Add button blurred the field, cleared the verdict
  // synchronously, and the click that followed found `canAdd` false and did
  // nothing at all. The button silently failed on its first press, every time.
  if (candidate && candidate === lastAsked && !inspectError.value) return;
  lastAsked = candidate;

  verdict.value = null;
  inspectError.value = "";
  addError.value = "";
  inspectedPath.value = "";
  if (!candidate) return;

  const startedAt = ++inspectEpoch;
  inspecting.value = true;
  try {
    const body = await inspectLibraryPath(candidate);
    if (startedAt !== inspectEpoch) return;
    verdict.value = body;
    inspectedPath.value = body.path;
    // The server derives the same default from the folder, so this only ever
    // shows the owner what they are about to get — until they change it.
    if (!nameEdited.value) name.value = body.suggested_name ?? "";
  } catch (error) {
    if (startedAt !== inspectEpoch) return;
    inspectError.value = errorDetail(error) || "Could not read that folder.";
  } finally {
    if (startedAt === inspectEpoch) inspecting.value = false;
  }
}

function chooseFolder(selected) {
  browserOpen.value = false;
  path.value = selected;
  // A folder chosen in the browser is a new answer whatever was typed before,
  // and the name follows it unless the owner has already set one.
  inspect();
}

async function add() {
  if (!canAdd.value || adding.value) return;
  addError.value = "";
  adding.value = true;
  try {
    const library = await addLibrary(inspectedPath.value, name.value.trim());
    emit("added", library);
    emit("close");
  } catch (error) {
    // The server re-inspects, so a folder that became covered since the
    // verdict is refused here rather than in the card above. Re-ask so the card
    // agrees with the refusal, and only then write the message: `inspect`
    // clears it, being the thing that runs whenever the path changes.
    const refusal = errorDetail(error) || "Could not add that folder.";
    // Force the re-ask past the no-op guard: the point is that the answer may
    // have changed under us, which is the one case where asking again is not a
    // repeat.
    lastAsked = "";
    await inspect();
    addError.value = refusal;
  } finally {
    adding.value = false;
  }
}
</script>

<template>
  <AppDialog
    :open="open"
    title="Add a library"
    subtitle="Point PixlStash at a folder. Nothing inside it is moved."
    :width="820"
    @close="emit('close')"
  >
    <div class="add-library">
      <div class="add-library__path">
        <AppInput
          ref="pathInput"
          v-model="path"
          class="add-library__field"
          label="Folder"
          placeholder="/home/me/Pictures"
          icon="folder-outline"
          @enter="inspect"
          @blur="inspect"
        />
        <AppButton
          v-if="!inDocker"
          class="add-library__browse"
          size="sm"
          variant="secondary"
          @click="browserOpen = true"
        >
          Browse…
        </AppButton>
      </div>

      <p v-if="inDocker" class="add-library__note">
        PixlStash is running in a container, so this is a path inside it.
      </p>

      <p
        v-if="inspecting"
        class="add-library__note"
        role="status"
        aria-live="polite"
      >
        Reading that folder…
      </p>

      <p v-else-if="inspectError" class="add-library__error" role="alert">
        {{ inspectError }}
      </p>

      <!-- One card, five shapes. The words are the server's; only the icon,
           the border and whether there is a button are decided here. -->
      <div
        v-else-if="verdict"
        class="add-library__verdict"
        :class="{ 'add-library__verdict--warn': !verdict.can_add }"
      >
        <span class="add-library__mark" aria-hidden="true">{{
          verdict.can_add ? "✓" : "!"
        }}</span>
        <div class="add-library__text">
          <div class="add-library__headline">{{ verdict.headline }}</div>
          <div class="add-library__detail">{{ verdict.detail }}</div>
          <!-- In the card rather than under it, so it sits with the thing it
               names and ahead of the button that commits it. Prefilled with the
               folder's own name, which is what the server would pick anyway. It
               is here because library names must be unique: two folders both
               called `2024` would otherwise be unaddable from this dialog, and
               the owner sent to the command line — the thing this removes. -->
          <AppInput
            v-if="verdict.can_add"
            v-model="name"
            class="add-library__name"
            label="Call it"
            :placeholder="verdict.suggested_name"
            @update:model-value="nameEdited = true"
          />
        </div>
        <AppButton
          v-if="verdict.can_add"
          size="sm"
          variant="primary"
          :loading="adding"
          @click="add"
        >
          {{ actionLabel }}
        </AppButton>
      </div>

      <p v-if="addError" class="add-library__error" role="alert">
        {{ addError }}
      </p>
    </div>

    <template #footer>
      <AppButton size="sm" variant="secondary" @click="emit('close')">
        Cancel
      </AppButton>
    </template>
  </AppDialog>

  <FolderBrowser
    :open="browserOpen"
    allow-create-folder
    :registered-paths="registeredPaths"
    already-registered-label="Already a library"
    :initial-path="path || null"
    @select="chooseFolder"
    @close="browserOpen = false"
  />
</template>

<style scoped>
.add-library {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.add-library__path {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}

.add-library__field {
  flex: 1;
  min-width: 0;
}

.add-library__note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  line-height: var(--leading-snug);
}

.add-library__error {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-error));
  background: rgb(var(--v-theme-error));
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
}

.add-library__name {
  margin-top: var(--space-3);
  max-width: 320px;
}

.add-library__verdict {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
}

.add-library__verdict--warn {
  border-color: rgb(var(--v-theme-warning));
}

.add-library__mark {
  flex-shrink: 0;
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-success));
}

.add-library__verdict--warn .add-library__mark {
  color: rgb(var(--v-theme-warning));
}

.add-library__text {
  flex: 1;
  min-width: 0;
}

.add-library__headline {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.add-library__detail {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  line-height: var(--leading-snug);
  margin-top: var(--space-1);
}

@media (max-width: 799px) {
  .add-library__path,
  .add-library__verdict {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
