<template>
  <!-- What leaves the machine with a shared picture: the generation data on an
       export (#2417), the address a share link names, and the watermark a
       marked share link stamps on. Under Privacy, in the same pane, because
       all three are about what goes out. -->
  <SettingsSection title="Sharing">
    <SettingsRow
      icon="mdi-tray-arrow-up"
      label="Keep the workflow when exporting"
      sub="Export, Export to Folder and the Share panel keep the ComfyUI workflow, prompt and A1111 parameters. Copy and drag-out always keep them."
    >
      <span class="sh__state">{{
        prefs.keepWorkflowOnExport ? "Keeps" : "Strips"
      }}</span>
      <v-switch
        :model-value="prefs.keepWorkflowOnExport"
        aria-label="Keep the workflow when exporting"
        color="primary"
        density="compact"
        hide-details
        @update:model-value="saveKeepWorkflow"
      />
    </SettingsRow>
    <p v-if="error" class="sh__error" role="alert">{{ error }}</p>

    <div class="sh__out">
      <div class="sh__url">
        <AppInput
          v-model="publicUrlValue"
          label="Public base URL (optional)"
          placeholder="https://my-tunnel.example.com"
          :disabled="publicUrlLoading"
          @enter="savePublicUrl"
          @blur="savePublicUrl"
        />
        <div class="sh__desc">
          Share links use your browser's current address. Set a public URL (e.g.
          a
          <a
            href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/"
            target="_blank"
            rel="noopener noreferrer"
            >Cloudflare Tunnel</a
          >
          address) so links work for people outside your network.
        </div>
        <span v-if="publicUrlError" class="sh__error">{{ publicUrlError }}</span>
        <span v-else-if="publicUrlSuccess" class="sh__saved">{{
          publicUrlSuccess
        }}</span>
      </div>

      <!-- WatermarkDrop: the thumbnail IS the control. Click opens the file
           picker, a reset overlay clears it. -->
      <div class="sh__wm">
        <FieldLabel>Watermark</FieldLabel>
        <div class="wm-drop">
          <button
            type="button"
            class="wm-drop__target"
            :class="{ 'wm-drop__target--has-image': watermarkPreviewUrl }"
            :style="
              watermarkPreviewUrl
                ? { backgroundImage: `url(${watermarkPreviewUrl})` }
                : null
            "
            aria-label="Upload watermark"
            :disabled="watermarkUploading"
            @click="watermarkInputRef?.click()"
          >
            <Tooltip
              text="Upload watermark"
              activator="parent"
              :describe="false"
            />
            <template v-if="!watermarkPreviewUrl">
              <v-icon size="26">mdi-image-plus-outline</v-icon>
              <span class="wm-drop__hint">Click or drop an image</span>
            </template>
          </button>
          <button
            v-if="watermarkPreviewUrl"
            type="button"
            class="wm-drop__reset"
            aria-label="Reset to default watermark"
            :disabled="watermarkUploading"
            @click="clearWatermark"
          >
            <Tooltip
              text="Reset to default watermark"
              activator="parent"
              :describe="false"
            />
            <v-icon size="15">mdi-close</v-icon>
          </button>
          <input
            ref="watermarkInputRef"
            type="file"
            accept="image/png,image/jpeg,image/webp"
            class="wm-drop__input"
            @change="handleWatermarkUpload"
          />
        </div>
        <div v-if="watermarkUploadError" class="sh__error">
          {{ watermarkUploadError }}
        </div>
      </div>
    </div>
  </SettingsSection>
</template>

<script setup>
import { ref, watch } from "vue";
import { VSwitch } from "vuetify/components";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { patchUserConfig } from "../../api/config";
import { uploadWatermark, deleteWatermark } from "../../api/users";
import AppInput from "../widgets/AppInput.vue";
import FieldLabel from "../widgets/FieldLabel.vue";
import Tooltip from "../widgets/Tooltip.vue";
import SettingsSection from "./SettingsSection.vue";
import SettingsRow from "./SettingsRow.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const prefs = useUserPrefsStore();
const error = ref("");

async function saveKeepWorkflow(value) {
  error.value = "";
  if (!(await prefs.saveKeepWorkflowOnExport(value))) {
    error.value =
      "Could not save what leaves with exported files. Your previous choice was kept.";
  }
}

// ── Public base URL ─────────────────────────────────────────────────────────
// The store holds the saved value (read at start-up, and what share links
// use), so the field starts from it and writes back to it.
const publicUrlValue = ref("");
const publicUrlLoading = ref(false);
const publicUrlError = ref("");
const publicUrlSuccess = ref("");

async function savePublicUrl() {
  publicUrlError.value = "";
  publicUrlSuccess.value = "";
  const trimmed = publicUrlValue.value.trim().replace(/\/$/, "");
  publicUrlValue.value = trimmed;
  if ((trimmed || null) === (prefs.publicUrl || null)) return;
  publicUrlLoading.value = true;
  try {
    await patchUserConfig({ public_url: trimmed || null });
    prefs.publicUrl = trimmed || null;
    publicUrlSuccess.value = "Saved.";
    setTimeout(() => {
      publicUrlSuccess.value = "";
    }, 2500);
  } catch (e) {
    console.error("Failed to save public_url:", e);
    publicUrlError.value = "Failed to save.";
  } finally {
    publicUrlLoading.value = false;
  }
}

// ── Watermark ───────────────────────────────────────────────────────────────
const watermarkPreviewUrl = ref("");
const watermarkInputRef = ref(null);
const watermarkUploading = ref(false);
const watermarkUploadError = ref("");

function refreshWatermarkPreview() {
  watermarkPreviewUrl.value = `/api/v1/users/me/watermark?cb=${Date.now()}`;
}

async function handleWatermarkUpload(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  watermarkUploadError.value = "";
  watermarkUploading.value = true;
  try {
    await uploadWatermark(file);
    refreshWatermarkPreview();
  } catch (e) {
    console.error("Watermark upload failed:", e);
    watermarkUploadError.value = "Upload failed.";
  } finally {
    watermarkUploading.value = false;
    if (watermarkInputRef.value) watermarkInputRef.value.value = "";
  }
}

async function clearWatermark() {
  watermarkUploadError.value = "";
  watermarkUploading.value = true;
  try {
    await deleteWatermark();
    refreshWatermarkPreview();
  } catch (e) {
    console.error("Watermark reset failed:", e);
    watermarkUploadError.value = "Failed to remove watermark.";
  } finally {
    watermarkUploading.value = false;
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    publicUrlValue.value = prefs.publicUrl || "";
    publicUrlError.value = "";
    publicUrlSuccess.value = "";
    watermarkUploadError.value = "";
    refreshWatermarkPreview();
  },
  { immediate: true },
);
</script>

<style scoped>
.sh__state {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-right: var(--space-1);
  white-space: nowrap;
}

.sh__error {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
}

.sh__saved {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-success));
}

/* The public URL beside the watermark tile: one row for both ways a shared
   picture names or marks its owner. */
.sh__out {
  display: flex;
  gap: var(--space-6);
  align-items: stretch;
  margin-top: var(--space-3);
}

.sh__url {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.sh__desc {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-snug);
}

.sh__desc a {
  color: rgb(var(--v-theme-accent));
}

.sh__wm {
  flex-shrink: 0;
  width: 184px;
  display: flex;
  flex-direction: column;
}

.wm-drop {
  position: relative;
  width: 184px;
  flex: 1;
  min-height: 72px;
}

.wm-drop__target {
  width: 100%;
  height: 100%;
  min-height: 72px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1.5px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  color: rgba(var(--v-theme-on-surface), 0.6);
  transition:
    border-color var(--dur-1) var(--ease-standard),
    background var(--dur-1) var(--ease-standard);
}

/* `background-color`, not the shorthand: the shorthand would reset the
   watermark preview's `background-size` while hovered. */
.wm-drop__target:hover:not(:disabled) {
  color: rgb(var(--v-theme-on-surface));
  background-color: var(--hover-wash);
}

.wm-drop__target:disabled {
  cursor: default;
  opacity: 0.6;
}

.wm-drop__target--has-image {
  background-size: contain;
  background-repeat: no-repeat;
  background-position: center;
}

.wm-drop__hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  text-align: center;
}

.wm-drop__reset {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  width: 24px;
  height: 24px;
  border-radius: var(--radius-pill);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* The one allowed exception: a translucent black scrim with a white icon. */
  background: rgba(var(--v-theme-scrim), 0.6);
  color: #fff;
  backdrop-filter: blur(2px);
}

.wm-drop__reset:disabled {
  cursor: default;
  opacity: 0.6;
}

.wm-drop__input {
  display: none;
}
</style>
