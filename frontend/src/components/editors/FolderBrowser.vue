<template>
  <v-dialog :model-value="open" max-width="720" @update:model-value="!$event && emit('close')">
    <v-card class="browser-card">
      <v-card-title class="browser-header">Browse for Folder</v-card-title>
      <v-card-text style="padding: 0">
        <div class="browse-path-bar">
          <v-icon size="16" style="opacity: 0.6; margin-right: 4px">mdi-folder</v-icon>
          <span class="browse-path-text">{{ browsePath }}</span>
          <v-checkbox
            v-model="browseShowHidden"
            label="Show hidden"
            density="compact"
            hide-details
            class="browse-hidden-toggle"
          />
        </div>
        <div class="browse-entries">
          <div v-if="browseLoading" class="browse-loading">
            <v-progress-circular indeterminate size="24" />
          </div>
          <div v-else-if="browseError" class="browse-error">
            {{ browseError }}
          </div>
          <template v-else>
            <div
              v-if="browsePath && browsePath !== '/'"
              class="browse-entry browse-entry--up"
              @click="browseUp"
            >
              <v-icon size="16">mdi-arrow-up</v-icon>
              <span class="browse-entry-name">..</span>
            </div>
            <div
              v-for="entry in browseEntries"
              :key="entry.path"
              class="browse-entry"
              :class="{ 'browse-entry--disabled': !!entryDisabledReason(entry.path) }"
              :title="entryDisabledReason(entry.path) || entry.path"
              @click="entryClick(entry)"
            >
              <v-icon size="16">{{
                entry.is_file ? "mdi-file-outline" : "mdi-folder"
              }}</v-icon>
              <span class="browse-entry-name">{{ entry.name }}</span>
              <span
                v-if="entryDisabledReason(entry.path)"
                class="browse-entry-reason"
              >
                {{ entryDisabledReason(entry.path) }}
              </span>
            </div>
          </template>
        </div>
      </v-card-text>
      <v-card-actions class="browse-footer">
        <v-spacer></v-spacer>
        <v-btn variant="text" @click="emit('close')">Cancel</v-btn>
        <v-btn
          variant="flat"
          color="primary"
          :disabled="!browsePath"
          @click="selectPath"
        >
          Select "{{ selectedName }}"
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";

const props = defineProps({
  open: { type: Boolean, default: false },
  registeredPaths: { type: Array, default: () => [] },
  imageRoot: { type: String, default: null },
  alreadyRegisteredLabel: { type: String, default: "Already registered" },
});

const emit = defineEmits(["select", "close"]);

const browsePath = ref("");
const browseEntries = ref([]);
const browseLoading = ref(false);
const browseError = ref("");
const browseShowHidden = ref(false);

const selectedName = computed(() => {
  if (!browsePath.value) return "";
  const parts = browsePath.value.replace(/\/$/, "").split("/");
  return parts[parts.length - 1] || "/";
});

function entryDisabledReason(entryPath) {
  const norm = entryPath.replace(/\/$/, "");
  if (props.imageRoot) {
    const root = props.imageRoot.replace(/\/$/, "");
    if (norm === root) return "PixlStash data folder";
  }
  for (const registered of props.registeredPaths) {
    if (norm === registered.replace(/\/$/, "")) return props.alreadyRegisteredLabel;
  }
  return null;
}

async function browseDir(path) {
  browseLoading.value = true;
  browseError.value = "";
  try {
    const res = await apiClient.get("/filesystem/browse", {
      params: { path: path ?? undefined, show_hidden: browseShowHidden.value },
    });
    browseEntries.value = res.data?.entries ?? [];
    browsePath.value = res.data?.path ?? path ?? "/";
  } catch (error) {
    browseError.value =
      error?.response?.data?.detail || "Cannot browse this directory.";
    browseEntries.value = [];
  } finally {
    browseLoading.value = false;
  }
}

watch(browseShowHidden, () => {
  if (props.open) browseDir(browsePath.value || null);
});

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    browseError.value = "";
    browseEntries.value = [];
    browsePath.value = "";
    browseShowHidden.value = false;
    browseDir(null);
  },
);

function entryClick(entry) {
  if (entry.is_file) return;
  if (entryDisabledReason(entry.path)) return;
  browseDir(entry.path);
}

function browseUp() {
  if (!browsePath.value || browsePath.value === "/") return;
  const parent =
    browsePath.value.replace(/\/$/, "").split("/").slice(0, -1).join("/") || "/";
  browseDir(parent);
}

function selectPath() {
  if (!browsePath.value) return;
  emit("select", browsePath.value);
  emit("close");
}
</script>

<style scoped>
.browser-card {
  overflow: hidden;
}

.browser-header {
  font-size: 1.1rem;
  font-weight: 600;
  padding: 20px 20px 8px;
}

.browse-path-bar {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.2);
  gap: 4px;
  font-size: 0.82rem;
}

.browse-path-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
  opacity: 0.9;
}

.browse-hidden-toggle {
  flex-shrink: 0;
  font-size: 0.78rem;
}

.browse-entries {
  max-height: 360px;
  overflow-y: auto;
  padding: 4px 0;
}

.browse-loading {
  display: flex;
  justify-content: center;
  padding: 24px;
}

.browse-error {
  color: rgb(var(--v-theme-error));
  padding: 12px 16px;
  font-size: 0.85rem;
}

.browse-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  cursor: pointer;
  font-size: 0.88rem;
  transition: background 0.15s;
}

.browse-entry:hover:not(.browse-entry--disabled) {
  background: rgba(var(--v-theme-primary), 0.06);
}

.browse-entry--up {
  opacity: 0.7;
  font-style: italic;
}

.browse-entry--disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.browse-entry-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.browse-entry-reason {
  font-size: 0.74rem;
  opacity: 0.6;
  font-style: italic;
  flex-shrink: 0;
}

.browse-footer {
  padding: 12px 20px 18px;
}
</style>
