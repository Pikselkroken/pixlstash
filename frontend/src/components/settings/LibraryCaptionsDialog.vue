<script setup>
/**
 * Caption files: keep the text files beside the library's own pictures in
 * sync with PixlStash, the way a reference folder's editor already offers.
 *
 * A dialog off the active library's overflow menu in LibrariesSection, beside
 * "Choose a layout…" and for the same reason: the routes are
 * `/server-config/captions`, which address whichever library is *open*.
 * Nothing is written until Save; turning a type on makes the server rescan
 * the library root.
 */
import { computed, ref, watch } from "vue";
import { VCheckbox, VTextField } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { getCaptionSettings, setCaptionSettings } from "../../api/serverConfig";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  open: { type: Boolean, default: false },
});
const emit = defineEmits(["close"]);

const libraries = useLibrariesStore();

const loading = ref(false);
const saving = ref(false);
const error = ref("");
const refused = ref(false);
// Save waits for the GET to have applied: after a failed read the form is a
// valid-looking "everything off, no suffixes" the PATCH would happily store.
const loaded = ref(false);

const syncTags = ref(false);
const syncDescriptions = ref(false);
const tagsSuffix = ref("");
const descriptionSuffix = ref("");
const defaults = ref({ tags: "_tags.txt", description: "_description.txt" });

const blocked = computed(
  () => libraries.hasLoadedSuccessfully && !libraries.canManage,
);
const unavailable = computed(() => blocked.value || refused.value);
const canSave = computed(() => loaded.value && !loading.value && !saving.value);
const tagsExample = computed(
  () => "image" + (tagsSuffix.value.trim() || defaults.value.tags),
);
const descriptionExample = computed(
  () => "image" + (descriptionSuffix.value.trim() || defaults.value.description),
);

function apply(body) {
  syncTags.value = Boolean(body.sync_tags);
  syncDescriptions.value = Boolean(body.sync_descriptions);
  tagsSuffix.value = body.tags_suffix || "";
  descriptionSuffix.value = body.description_suffix || "";
  defaults.value = {
    tags: body.default_tags_suffix || "_tags.txt",
    description: body.default_description_suffix || "_description.txt",
  };
}

// Close and reopen can put two GETs in flight; only the newest may touch the
// form, the same `seq` guard LibraryLayoutDialog uses.
let loadSeq = 0;

async function load() {
  const seq = ++loadSeq;
  loading.value = true;
  loaded.value = false;
  error.value = "";
  refused.value = false;
  try {
    const body = await getCaptionSettings();
    if (seq !== loadSeq) return;
    apply(body);
    loaded.value = true;
  } catch (err) {
    if (seq !== loadSeq) return;
    refused.value = err?.response?.status === 403;
    if (!refused.value) {
      error.value = errorDetail(err) || "Could not read the caption settings.";
    }
  } finally {
    if (seq === loadSeq) loading.value = false;
  }
}

async function save() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = "";
  try {
    apply(
      await setCaptionSettings({
        syncTags: syncTags.value,
        syncDescriptions: syncDescriptions.value,
        tagsSuffix: tagsSuffix.value.trim(),
        descriptionSuffix: descriptionSuffix.value.trim(),
      }),
    );
    emit("close");
  } catch (err) {
    error.value = errorDetail(err) || "Could not save the caption settings.";
  } finally {
    saving.value = false;
  }
}

// Fetch on the open transition (the house pattern); `blocked` is watched too
// because the registry read that answers it may still be in flight.
watch(
  [() => props.open, blocked],
  ([isOpen, cannot]) => {
    if (isOpen && !cannot) load();
  },
  { immediate: true },
);
</script>

<template>
  <AppDialog
    :open="open"
    :width="520"
    title="Caption files"
    @close="emit('close')"
    @accept="save"
  >
    <p v-if="unavailable" class="captions-dlg__sub">
      Caption files can only be set up on the machine running PixlStash, or over
      your local network or Tailscale, because they are written beside the
      pictures on that machine.
    </p>

    <template v-else>
      <p class="captions-dlg__sub">
        Keep text files next to each picture in this library in sync with
        PixlStash, so your tags and descriptions travel with the pictures.
        Turning a type on writes a file beside every picture that already has
        tags or a description, and where a picture already has a caption file,
        that file's text replaces what PixlStash holds. The suffix only names
        the files PixlStash creates; an empty file is never created.
      </p>

      <p v-if="error" class="captions-dlg__error" role="alert">{{ error }}</p>

      <div class="captions-dlg__row">
        <VCheckbox
          v-model="syncTags"
          label="Sync tags"
          density="compact"
          hide-details
          :disabled="loading || saving"
        />
        <div v-if="syncTags" class="captions-dlg__suffix">
          <VTextField
            v-model="tagsSuffix"
            label="Suffix for new tags files"
            :placeholder="defaults.tags"
            density="compact"
            variant="filled"
            hide-details
            :disabled="loading || saving"
          />
          <div class="captions-dlg__hint">
            e.g. <code>{{ tagsExample }}</code>
          </div>
        </div>
      </div>

      <div class="captions-dlg__row">
        <VCheckbox
          v-model="syncDescriptions"
          label="Sync descriptions"
          density="compact"
          hide-details
          :disabled="loading || saving"
        />
        <div v-if="syncDescriptions" class="captions-dlg__suffix">
          <VTextField
            v-model="descriptionSuffix"
            label="Suffix for new description files"
            :placeholder="defaults.description"
            density="compact"
            variant="filled"
            hide-details
            :disabled="loading || saving"
          />
          <div class="captions-dlg__hint">
            e.g. <code>{{ descriptionExample }}</code>
          </div>
        </div>
      </div>
    </template>

    <template v-if="!unavailable" #footer>
      <div class="captions-dlg__actions">
        <AppButton
          variant="secondary"
          size="sm"
          key-hint="esc"
          @click="emit('close')"
        >
          Cancel
        </AppButton>
        <AppButton
          variant="primary"
          size="sm"
          key-hint="enter"
          :disabled="!canSave"
          :loading="saving"
          @click="save"
        >
          Save
        </AppButton>
      </div>
    </template>
  </AppDialog>
</template>

<style scoped>
.captions-dlg__sub {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  margin: 0 0 var(--space-5);
}

.captions-dlg__error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
  margin: 0 0 var(--space-4);
}

.captions-dlg__row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}

.captions-dlg__suffix {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-left: var(--space-7);
}

.captions-dlg__hint {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.captions-dlg__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}
</style>
