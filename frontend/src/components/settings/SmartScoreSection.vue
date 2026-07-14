<script setup>
import { onMounted, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";
import { VSwitch } from "vuetify/components";
import SettingsSection from "./SettingsSection.vue";
import SettingsChipGrid from "./SettingsChipGrid.vue";
import SettingsChip from "./SettingsChip.vue";
import SettingsAddTagRow from "./SettingsAddTagRow.vue";
import AppSelect from "../widgets/AppSelect.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["update:hidden-tags", "update:apply-tag-filter"]);

// ── Tag filter (hidden tags) — moved here from Behaviour ──────────────────────
const hiddenTags = ref([]);
const hiddenTagInput = ref("");
const hiddenTagsLoading = ref(false);
const hiddenTagsError = ref("");
const hiddenTagsSuccess = ref("");
const applyTagFilter = ref(false);
const applyTagFilterLoading = ref(false);

function normalizeHiddenTags(tags) {
  const values = Array.isArray(tags)
    ? tags
    : tags && typeof tags === "object"
      ? Object.keys(tags)
      : [];
  const seen = new Set();
  const cleaned = [];
  for (const tag of values) {
    if (tag == null) continue;
    const clean = String(tag).trim().toLowerCase();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    cleaned.push(clean);
  }
  return cleaned.sort((a, b) => a.localeCompare(b));
}

async function saveHiddenTags(nextTags) {
  hiddenTagsLoading.value = true;
  hiddenTagsError.value = "";
  hiddenTagsSuccess.value = "";
  try {
    const normalized = normalizeHiddenTags(nextTags);
    await apiClient.patch("/users/me/config", {
      hidden_tags: normalized,
    });
    hiddenTags.value = normalized;
    emit("update:hidden-tags", hiddenTags.value);
    hiddenTagsSuccess.value = "Saved.";
  } catch (e) {
    hiddenTagsError.value =
      e?.response?.data?.detail || "Failed to update hidden tags.";
  } finally {
    hiddenTagsLoading.value = false;
    if (hiddenTagsSuccess.value) {
      setTimeout(() => {
        hiddenTagsSuccess.value = "";
      }, 2000);
    }
  }
}

async function addHiddenTag() {
  const trimmed = hiddenTagInput.value.trim().toLowerCase();
  if (!trimmed) return;
  const next = normalizeHiddenTags([...hiddenTags.value, trimmed]);
  hiddenTagInput.value = "";
  await saveHiddenTags(next);
}

async function removeHiddenTag(tag) {
  const next = normalizeHiddenTags(
    hiddenTags.value.filter((entry) => entry !== tag),
  );
  await saveHiddenTags(next);
}

async function setApplyTagFilter(value) {
  applyTagFilterLoading.value = true;
  hiddenTagsError.value = "";
  try {
    const nextValue = Boolean(value);
    await apiClient.patch("/users/me/config", {
      apply_tag_filter: nextValue,
    });
    applyTagFilter.value = nextValue;
    emit("update:apply-tag-filter", applyTagFilter.value);
  } catch (e) {
    hiddenTagsError.value =
      e?.response?.data?.detail || "Failed to update tag filter.";
  } finally {
    applyTagFilterLoading.value = false;
  }
}

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
    hiddenTags.value = normalizeHiddenTags(res.data?.hidden_tags);
    emit("update:hidden-tags", hiddenTags.value);
    applyTagFilter.value = Boolean(res.data?.apply_tag_filter);
    emit("update:apply-tag-filter", applyTagFilter.value);
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
  <div>
    <SettingsSection
      title="Penalised Tags"
      desc="Tags listed here reduce Smart Score when present on a picture. Adjust the importance to control how much they hurt the score."
      first
    >
      <SettingsChipGrid empty="No penalised tags yet.">
        <SettingsChip
          v-for="entry in smartScorePenalisedTags"
          :key="entry.tag"
          :label="entry.tag"
          @remove="removeSmartScoreTag(entry.tag)"
        >
          <div class="smart-score-importance">
            <AppSelect
              compact
              :model-value="entry.weight"
              :options="smartScoreImportanceOptions"
              :disabled="smartScoreTagsLoading"
              @update:model-value="
                (value) => updateSmartScoreTagWeight(entry.tag, value)
              "
            />
          </div>
        </SettingsChip>
      </SettingsChipGrid>

      <SettingsAddTagRow
        v-model="smartScoreTagInput"
        placeholder="Add penalised tag"
        btn="Add tag"
        @add="addSmartScoreTag"
      />

      <div
        v-if="smartScoreTagsError"
        class="smart-score-status smart-score-status--error"
      >
        {{ smartScoreTagsError }}
      </div>
      <div
        v-else-if="smartScoreTagsSuccess"
        class="smart-score-status smart-score-status--success"
      >
        {{ smartScoreTagsSuccess }}
      </div>
    </SettingsSection>

    <SettingsSection
      title="Tag Filter"
      desc="Tags listed here are filtered from the GUI entirely."
    >
      <div class="smart-score-filter-toggle">
        <v-switch
          v-model="applyTagFilter"
          color="accent"
          density="compact"
          hide-details
          :disabled="applyTagFilterLoading"
          label="Apply tag filter to all pictures and videos"
          @update:model-value="setApplyTagFilter"
        />
      </div>
      <SettingsChipGrid empty="No hidden tags yet.">
        <SettingsChip
          v-for="tag in hiddenTags"
          :key="tag"
          :label="tag"
          @remove="removeHiddenTag(tag)"
        />
      </SettingsChipGrid>
      <SettingsAddTagRow
        v-model="hiddenTagInput"
        placeholder="Add tag filter"
        btn="Add tag"
        @add="addHiddenTag"
      />
      <div
        v-if="hiddenTagsError"
        class="smart-score-status smart-score-status--error"
      >
        {{ hiddenTagsError }}
      </div>
      <div
        v-else-if="hiddenTagsSuccess"
        class="smart-score-status smart-score-status--success"
      >
        {{ hiddenTagsSuccess }}
      </div>
    </SettingsSection>
  </div>
</template>

<style scoped>
/* Fixed-width wrapper for the inline importance select inside each chip,
   matching the proposal's 96px slot. */
.smart-score-importance {
  width: 96px;
  flex-shrink: 0;
}

.smart-score-filter-toggle {
  margin-bottom: var(--space-4);
}

.smart-score-status {
  font-size: var(--text-xs);
  margin-top: var(--space-2);
}

.smart-score-status--error {
  color: rgb(var(--v-theme-error));
}

.smart-score-status--success {
  color: rgb(var(--v-theme-accent));
}
</style>
