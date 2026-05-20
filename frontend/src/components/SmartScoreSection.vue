<script setup>
import { onMounted, ref, watch } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const smartScorePenalisedTags = ref([]);
const smartScoreTagInput = ref("");
const smartScoreTagsLoading = ref(false);
const smartScoreTagsError = ref("");
const smartScoreTagsSuccess = ref("");

const smartScoreImportanceOptions = [
  { value: 1, label: "Mild" },
  { value: 2, label: "Low" },
  { value: 3, label: "Moderate" },
  { value: 4, label: "High" },
  { value: 5, label: "Severe" },
];

function clampImportance(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 3;
  return Math.min(5, Math.max(1, Math.round(num)));
}

function SmartScoreTags(tags) {
  const d = new Map();
  if (Array.isArray(tags)) {
    for (const item of tags) {
      if (item == null) continue;
      if (typeof item === "object") {
        const clean = String(item.tag || "")
          .trim()
          .toLowerCase();
        if (!clean) continue;
        d.set(clean, clampImportance(item.weight));
      } else {
        const clean = String(item).trim().toLowerCase();
        if (!clean) continue;
        d.set(clean, 3);
      }
    }
  } else if (tags && typeof tags === "object") {
    for (const [tag, weight] of Object.entries(tags)) {
      if (tag == null) continue;
      const clean = String(tag).trim().toLowerCase();
      if (!clean) continue;
      const nextWeight = clampImportance(weight);
      const existing = d.get(clean);
      if (existing == null || nextWeight > existing) {
        d.set(clean, nextWeight);
      }
    }
  }
  return Array.from(d.entries())
    .map(([tag, weight]) => ({ tag, weight }))
    .sort((a, b) => a.tag.localeCompare(b.tag));
}

function serializeSmartScoreTags(entries) {
  const d = SmartScoreTags(entries);
  const payload = {};
  for (const entry of d) {
    payload[entry.tag] = clampImportance(entry.weight);
  }
  return { d, payload };
}

async function fetchData() {
  smartScoreTagsLoading.value = true;
  smartScoreTagsError.value = "";
  smartScoreTagInput.value = "";
  smartScoreTagsSuccess.value = "";
  try {
    const res = await apiClient.get("/users/me/config");
    smartScorePenalisedTags.value = SmartScoreTags(
      res.data?.smart_score_penalised_tags,
    );
  } catch (_) {
    smartScoreTagsError.value = "Failed to load smart score settings.";
  } finally {
    smartScoreTagsLoading.value = false;
  }
}

async function saveSmartScoreTags(nextTags) {
  smartScoreTagsLoading.value = true;
  smartScoreTagsError.value = "";
  smartScoreTagsSuccess.value = "";
  try {
    const { d, payload } = serializeSmartScoreTags(nextTags);
    await apiClient.patch("/users/me/config", {
      smart_score_penalised_tags: payload,
    });
    smartScorePenalisedTags.value = d;
    smartScoreTagsSuccess.value = "Saved.";
  } catch (e) {
    smartScoreTagsError.value =
      e?.response?.data?.detail || "Failed to update smart score tags.";
  } finally {
    smartScoreTagsLoading.value = false;
    if (smartScoreTagsSuccess.value) {
      setTimeout(() => {
        smartScoreTagsSuccess.value = "";
      }, 2000);
    }
  }
}

async function addSmartScoreTag() {
  const trimmed = smartScoreTagInput.value.trim().toLowerCase();
  if (!trimmed) return;
  const next = SmartScoreTags([
    ...smartScorePenalisedTags.value,
    { tag: trimmed, weight: 3 },
  ]);
  smartScoreTagInput.value = "";
  await saveSmartScoreTags(next);
}

async function removeSmartScoreTag(tag) {
  const next = SmartScoreTags(
    smartScorePenalisedTags.value.filter((t) => t.tag !== tag),
  );
  await saveSmartScoreTags(next);
}

async function updateSmartScoreTagWeight(tag, weight) {
  const next = SmartScoreTags(
    smartScorePenalisedTags.value.map((entry) =>
      entry.tag === tag ? { ...entry, weight: clampImportance(weight) } : entry,
    ),
  );
  await saveSmartScoreTags(next);
}

onMounted(fetchData);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) fetchData();
  },
);
</script>

<template>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div class="settings-section-title">Penalised Tags</div>
    <div class="settings-section-desc">
      Tags listed here reduce Smart Score when present on a picture.
      Adjust the importance to control how much they hurt the score.
    </div>
    <div class="settings-tag-list">
      <div
        v-for="entry in smartScorePenalisedTags"
        :key="entry.tag"
        class="settings-tag-chip settings-tag-chip--row"
      >
        <v-tooltip :text="entry.tag" location="top">
          <template #activator="{ props: tooltipProps }">
            <span class="settings-tag-label" v-bind="tooltipProps">{{
              entry.tag
            }}</span>
          </template>
        </v-tooltip>
        <v-select
          class="settings-tag-importance"
          :items="smartScoreImportanceOptions"
          item-title="label"
          item-value="value"
          density="compact"
          variant="plain"
          hide-details
          :disabled="smartScoreTagsLoading"
          :model-value="entry.weight"
          @update:model-value="(value) => updateSmartScoreTagWeight(entry.tag, value)"
        />
        <v-btn
          icon
          variant="text"
          class="settings-tag-delete"
          :disabled="smartScoreTagsLoading"
          @click="removeSmartScoreTag(entry.tag)"
        >
          <v-icon size="16">mdi-close</v-icon>
        </v-btn>
      </div>
      <div
        v-if="!smartScoreTagsLoading && !smartScorePenalisedTags.length"
        class="settings-token-empty"
      >
        No penalised tags yet.
      </div>
    </div>
    <div class="settings-form">
      <div class="settings-add-tag-row">
        <v-text-field
          v-model="smartScoreTagInput"
          label="Add penalised tag"
          density="compact"
          variant="filled"
          class="settings-add-tag-input"
          :disabled="smartScoreTagsLoading"
          @keydown.enter.prevent="addSmartScoreTag"
        />
        <v-btn
          variant="outlined"
          color="primary"
          class="settings-action-btn"
          :loading="smartScoreTagsLoading"
          :disabled="smartScoreTagsLoading"
          @click="addSmartScoreTag"
        >
          Add Tag
        </v-btn>
      </div>
      <div v-if="smartScoreTagsError" class="settings-error">
        {{ smartScoreTagsError }}
      </div>
      <div v-else-if="smartScoreTagsSuccess" class="settings-success">
        {{ smartScoreTagsSuccess }}
      </div>
      <div v-else class="settings-success">
        {{ "&nbsp;" }}
      </div>
    </div>
  </div>
  <v-divider class="settings-section-divider" />
</template>

<style scoped>
.settings-section-divider {
  margin: 4px 0 8px;
}

.settings-section {
  display: flex;
  line-height: 1;
  flex-direction: column;
  gap: 6px;
}

.settings-section-title {
  font-weight: 600;
}

.settings-section-desc {
  font-size: 0.92em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-add-tag-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.settings-add-tag-input {
  flex: 1 1 auto;
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.9em;
}

.settings-success {
  color: rgb(var(--v-theme-accent));
  font-size: 0.9em;
}

.settings-tag-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.settings-tag-list .settings-token-empty {
  grid-column: 1 / -1;
}

.settings-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  border-radius: 6px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.settings-tag-chip--row {
  width: 100%;
  justify-content: space-between;
  padding-right: 4px;
}

.settings-tag-importance {
  flex: 0 1 90px;
  min-width: 0;
  max-width: 90px;
  overflow: hidden;
}

:deep(.settings-tag-importance .v-field) {
  min-height: 28px;
  height: 28px;
  padding-top: 0;
  padding-bottom: 0;
  font-size: 0.9em;
  background: transparent;
  box-shadow: none;
  border: none;
}

:deep(.settings-tag-importance .v-field__input) {
  min-height: 28px;
  height: 28px;
  padding-top: 0;
  padding-bottom: 0;
  padding-right: 4px;
  font-size: 0.85rem;
  min-width: 0;
  overflow: hidden;
}

:deep(.settings-tag-importance .v-field__append-inner) {
  align-self: center;
  margin-left: 2px;
  padding-top: 0;
  padding-bottom: 0;
  height: 28px;
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

:deep(.settings-tag-importance .v-field__overlay),
:deep(.settings-tag-importance .v-field__underlay),
:deep(.settings-tag-importance .v-field__outline) {
  opacity: 0;
}

:deep(.settings-tag-importance .v-select__selection-text) {
  font-size: 0.85rem;
  line-height: 1.1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  display: block;
}

:deep(.settings-tag-importance .v-field__input input) {
  font-size: 0.85rem;
}

.settings-tag-label {
  font-size: 1em;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: default;
}

.settings-tag-delete {
  color: rgba(var(--v-theme-on-surface), 0.65);
  min-width: 0;
  height: 12px;
  width: 12px;
  padding: 2;
}

.settings-tag-delete:hover {
  color: rgba(var(--v-theme-error), 0.9);
  min-width: 0;
  padding: 2;
}

.settings-token-empty {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.settings-action-btn {
  align-self: flex-start;
  background-color: rgb(var(--v-theme-primary)) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}

.settings-action-btn:hover {
  background-color: rgb(var(--v-theme-accent)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}
</style>
