<script setup>
/**
 * Caption files - keep the text files beside the library's own pictures in
 * sync with PixlStash, the way a reference folder's editor already offers.
 *
 * A dialog opened from the active library's overflow menu in LibrariesSection,
 * beside "Choose a layout…", and for the same reason: the routes are
 * `/server-config/captions`, which address whichever library is *open*.
 *
 * Nothing is written until Save. Turning a type on makes the server rescan
 * the library root, which reads every existing caption file of that type in
 * and writes one beside every picture that has content but no file yet.
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

const syncTags = ref(false);
const syncDescriptions = ref(false);
const tagsSuffix = ref("");
const descriptionSuffix = ref("");
const defaults = ref({ tags: "_tags.txt", description: "_description.txt" });

const blocked = computed(
  () => libraries.hasLoadedSuccessfully && !libraries.canManage,
);
const unavailable = computed(() => blocked.value || refused.value);

function example(suffix, fallback) {
  return "image" + (String(suffix || "").trim() || fallback);
}
const tagsExample = computed(() => example(tagsSuffix.value, defaults.value.tags));
const descriptionExample = computed(() =>
  example(descriptionSuffix.value, defaults.value.description),
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

async function load() {
  loading.value = true;
  error.value = "";
  refused.value = false;
  try {
    apply(await getCaptionSettings());
  } catch (err) {
    refused.value = err?.response?.status === 403;
    if (!refused.value) {
      error.value = errorDetail(err) || "Could not read the caption settings.";
    }
  } finally {
    loading.value = false;
  }
}

async function save() {
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

watch(
  () => props.open,
  (open) => {
    if (open) load();
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
  >
    <p v-if="unavailable" class="captions-dlg__sub">
      Caption files can only be set up on the machine running PixlStash, or over
      your local network or Tailscale, because they are written beside the
      pictures on that machine.
    </p>

    <template v-else>
      <p class="captions-dlg__sub">
        Keep text files next to each picture in this library in sync with
        PixlStash. Tags and descriptions are separate files: a change made here
        is written out, and a file edited outside PixlStash is read back in on
        the next scan. A picture that already has a caption file keeps it,
        whatever it is called. The suffix below only names the files PixlStash
        creates for pictures that have none; an empty file is never created.
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
        <AppButton variant="secondary" size="sm" @click="emit('close')">
          Cancel
        </AppButton>
        <AppButton
          variant="primary"
          size="sm"
          :disabled="loading"
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
  margin: 0 0 var(--space-5);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), 0.72);
}

.captions-dlg__error {
  margin: 0 0 var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  font-size: var(--text-sm);
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
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.captions-dlg__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}
</style>
