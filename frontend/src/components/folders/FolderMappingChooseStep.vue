<script setup>
/**
 * "Add a library" - the wizard's first pane.
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
 * Browsing reuses `FolderBrowser`, including its `New folder` - the add route
 * deliberately creates no directory, so making one is the picker's job and it
 * already had a button for it.
 *
 * "vault" and "empty" are added and switched to here, through the same
 * switch-and-reload `LibrariesSection.switchTo()` uses. "pictures" creates
 * nothing yet: "Bring them in" swaps the verdict card for the scan card in
 * place (`FolderMappingScanStep`, in the same `FolderMappingCard` frame) and
 * the read runs against a folder that is not a library - the library is only
 * built once the owner has said what the folders mean. A `resume` entry
 * re-enters the same pane with the path fixed and the read reattaching.
 */
import { computed, onMounted, ref } from "vue";

import { addLibrary, inspectLibraryPath } from "../../api/libraries";
import { errorDetail } from "../../utils/apiError";
import {
  useLibrariesStore,
  useLibrarySwitchStore,
} from "../../stores/useLibrariesStore";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import FolderMappingCard from "./FolderMappingCard.vue";
import FolderMappingScanStep from "./FolderMappingScanStep.vue";

const props = defineProps({
  // A saved read to pick up: the path is fixed and the scan card reattaches.
  resume: { type: Object, default: null },
});

const emit = defineEmits(["scan", "task", "ready", "cancel", "close"]);

const librariesStore = useLibrariesStore();
const switchStore = useLibrarySwitchStore();

// What each addable verdict calls its button. Pure labels - every word that
// carries a fact about this folder comes from the server.
const ACTION_LABELS = {
  vault: "Add it",
  pictures: "Bring them in",
  empty: "Start here",
};

const path = ref(props.resume?.path ?? "");
const name = ref(props.resume?.label ?? "");
/** True once the owner edits the name, so a new verdict stops overwriting it. */
const nameEdited = ref(false);
const verdict = ref(null);
const inspecting = ref(false);
const inspectError = ref("");
const adding = ref(false);
const addError = ref("");
const browserOpen = ref(false);
const pathInput = ref(null);
/** The scan card has replaced the verdict card; the path is now fixed. */
const scanning = ref(Boolean(props.resume));

/** The path the current verdict describes, so a stale answer is never acted on.
    A ref, not a plain `let`: `canAdd` reads it. */
const inspectedPath = ref("");
/** Discards an inspection that was still on the wire when the path changed. */
let inspectEpoch = 0;
/** The last path asked about, so `@blur` on an unchanged field is a no-op. */
let lastAsked = "";

// Paths already registered, so the browser can grey them out before the
// owner walks into one and is told no. Docker serves container paths and has
// no host filesystem to browse, so the picker degrades to a typed path.
const registeredPaths = computed(() =>
  librariesStore.libraries.map((library) => library.path).filter(Boolean),
);
const inDocker = computed(() => librariesStore.inDocker);

const actionLabel = computed(
  () => ACTION_LABELS[verdict.value?.verdict] ?? "Add",
);

const canAdd = computed(
  () =>
    Boolean(verdict.value?.can_add) &&
    verdict.value?.path === inspectedPath.value,
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
    // shows the owner what they are about to get - until they change it.
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
  if (verdict.value.verdict === "pictures") {
    // No library yet. The verdict card becomes the scan card, in the same
    // frame; the wizard builds the library once the mapping is accepted.
    path.value = inspectedPath.value;
    scanning.value = true;
    emit("scan", { path: path.value, label: name.value.trim() });
    return;
  }
  addError.value = "";
  adding.value = true;
  try {
    const library = await addLibrary(inspectedPath.value, name.value.trim());
    emit("close");
    // The same switch-then-reload flow LibrariesSection's own "Switch" button
    // starts. No confirmation prompt: pressing the button already said what
    // the owner wants, unlike an ordinary switch away from a library in use.
    await switchStore.begin(library, librariesStore.activeLibrary, null);
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

onMounted(() => {
  if (!scanning.value) pathInput.value?.focus();
});
</script>

<template>
  <div class="choose-step">
    <div class="choose-step__path">
      <AppInput
        ref="pathInput"
        v-model="path"
        class="choose-step__field"
        label="Folder"
        placeholder="/home/me/Pictures"
        icon="folder-outline"
        :disabled="scanning"
        @enter="inspect"
        @blur="inspect"
      />
      <AppButton
        v-if="!inDocker"
        class="choose-step__browse"
        size="sm"
        variant="secondary"
        :disabled="scanning"
        @click="browserOpen = true"
      >
        Browse…
      </AppButton>
    </div>

    <p v-if="inDocker" class="choose-step__note">
      PixlStash is running in a container, so this is a path inside it.
    </p>

    <FolderMappingScanStep
      v-if="scanning"
      :path="path"
      :resume-task-id="resume?.taskId ?? ''"
      :match-existing="Boolean(resume)"
      @task="emit('task', $event)"
      @ready="emit('ready', $event)"
      @cancel="emit('cancel')"
    />

    <p
      v-else-if="inspecting"
      class="choose-step__note"
      role="status"
      aria-live="polite"
    >
      Reading that folder…
    </p>

    <p v-else-if="inspectError" class="choose-step__error" role="alert">
      {{ inspectError }}
    </p>

    <!-- One card, five shapes. The words are the server's; only the border
         and whether there is a button are decided here. -->
    <FolderMappingCard
      v-else-if="verdict"
      class="choose-step__verdict"
      :title="verdict.headline"
      :lead="verdict.detail"
      :warn="!verdict.can_add"
    >
      <!-- In the card rather than under it, so it sits with the thing it
           names and ahead of the button that commits it. Prefilled with the
           folder's own name, which is what the server would pick anyway. It
           is here because library names must be unique: two folders both
           called `2024` would otherwise be unaddable from this dialog, and
           the owner sent to the command line - the thing this removes. -->
      <AppInput
        v-if="verdict.can_add"
        v-model="name"
        class="choose-step__name"
        label="Call it"
        :placeholder="verdict.suggested_name"
        @update:model-value="nameEdited = true"
      />
      <template v-if="verdict.can_add" #actions>
        <AppButton variant="primary" :loading="adding" @click="add">
          {{ actionLabel }}
        </AppButton>
        <AppButton variant="secondary" @click="emit('cancel')">
          Cancel
        </AppButton>
      </template>
    </FolderMappingCard>

    <p v-if="addError" class="choose-step__error" role="alert">
      {{ addError }}
    </p>
  </div>

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
.choose-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.choose-step__path {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}

.choose-step__field {
  flex: 1;
  min-width: 0;
}

.choose-step__note {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.65);
  line-height: var(--leading-snug);
}

.choose-step__error {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-error));
  background: rgb(var(--v-theme-error));
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
}

.choose-step__name {
  max-width: 320px;
}

@media (max-width: 799px) {
  .choose-step__path {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
