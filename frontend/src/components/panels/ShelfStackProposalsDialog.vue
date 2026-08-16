<template>
  <AppDialog
    :open="open"
    title="Group adapters"
    :subtitle="subtitle"
    :width="640"
    @close="emit('close')"
  >
    <p v-if="loading" class="ssp-note" role="status">Looking for groups…</p>
    <p v-else-if="error" class="ssp-note" role="alert">{{ error }}</p>
    <p v-else-if="!proposals.length" class="ssp-note" role="status">
      Nothing on the shelf looks like an adapter that is not already grouped.
      PixlStash only groups files whose names differ by a training step or a
      version (<code>v2</code>, <code>v2.1</code>), and only within one folder.
    </p>

    <template v-else>
      <!-- The dry run. Every group is listed with what it would collapse
           BEFORE anything is written, because detection proposes and never
           applies — the same promise the folder scan and the ai-toolkit run
           listing make. -->
      <p class="ssp-note">
        Files differing only by a training step are one run, and open ticked.
        Groups that span <strong>versions</strong> are a judgement — they merge
        separate trainings — so they open unticked and you choose them. Nothing
        has been changed yet, and Ungroup takes any of it back.
      </p>

      <ul class="ssp-list">
        <li v-for="proposal in proposals" :key="proposal.key" class="ssp-group">
          <label class="ssp-head">
            <input
              v-model="chosen"
              type="checkbox"
              :value="proposal.key"
              :disabled="working"
            />
            <span class="ssp-name">{{ proposal.name }}</span>
            <span class="ssp-count">{{ stepCount(proposal) }}</span>
            <!-- The tier is drawn, not merely carried. It is what decides
                 whether the row opened ticked, so a reader who is about to
                 merge two trainings can see WHICH rows those are. -->
            <span v-if="spansVersions(proposal)" class="ssp-tier"
              >merges versions</span
            >
            <span class="ssp-size">{{
              formatModelSize(proposal.total_size)
            }}</span>
          </label>
          <!-- Cover first, and said out loud: the file that will represent the
               group on the shelf is the one decision a reader might disagree
               with, and it is not obvious from a list of steps. -->
          <p class="ssp-cover">
            Shown as <strong>{{ coverLabel(proposal) }}</strong>
          </p>
        </li>
      </ul>
    </template>

    <template #footer>
      <AppButton variant="ghost" key-hint="esc" @click="emit('close')">
        Cancel
      </AppButton>
      <AppButton
        variant="primary"
        key-hint="enter"
        :loading="working"
        :disabled="!canSubmit"
        @click="submit"
      >
        {{ confirmLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
// The stack dry run (shelf plan F5).
//
// One dry run, then one press for whatever is ticked. Prefix grouping —
// `JimmyVehicle` beside `JimmyVehicle2` — needs per-group adjudication with
// counter-evidence and is not offered here, because the backend does not
// propose it.
//
// **Step groups open ticked; version groups do not.** A `step_group` is not in
// doubt — the reader is confirming a batch, not assembling one, and making those
// opt in one at a time would be an adjudication surface applied to the case that
// does not need it. A `version_group` is the opposite: it merges trainings the
// owner may have kept apart deliberately. Ungroup takes it back now, so this is
// no longer about a one-way door — but pre-ticking would still turn one press
// into a merge of every versioned run on the shelf, which is a judgement nobody
// made and a lot of undoing.
//
// That is what `tier` is FOR. It is a wire field with a job, not a label.

import { computed, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { createStack, listStackProposals } from "../../api/modelStacks";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { formatModelSize, stackReceipt } from "../../utils/modelShelf";

const props = defineProps({
  open: { type: Boolean, default: false },
});
const emit = defineEmits(["close"]);

const shelf = useModelShelfStore();

const proposals = ref([]);
const chosen = ref([]);
const loading = ref(false);
const working = ref(false);
const error = ref("");

const subtitle = computed(() =>
  proposals.value.length
    ? `${proposals.value.length.toLocaleString()} ${proposals.value.length === 1 ? "group" : "groups"} found`
    : "",
);

const canSubmit = computed(
  () => !working.value && !loading.value && chosen.value.length > 0,
);

const confirmLabel = computed(() => {
  const n = chosen.value.length;
  if (!n) return "Group them";
  // "Group 3 selected", not "Group 3 groups": the verb and the noun are the
  // same word here, and a group can span training runs now so "runs" is false.
  return `Group ${n.toLocaleString()} selected`;
});

function stepCount(proposal) {
  const n = proposal.members?.length ?? 0;
  return `${n.toLocaleString()} ${n === 1 ? "file" : "files"}`;
}

/** Whether this group would merge separate trainings. */
function spansVersions(proposal) {
  return proposal.tier === "version_group";
}

/** What the group will be shown as once it is one row.
 *
 * The version leads when the group has one, because that is the whole decision
 * in a `version_group`: `Foxglove` and `Foxglove_v2` are one subject and the
 * reader is agreeing that v2 is what "Foxglove" now means.
 */
function coverLabel(proposal) {
  const cover = proposal.members?.[0];
  if (!cover) return "";
  const step = cover.step === null ? "the final file" : `step ${cover.step}`;
  return cover.version ? `${cover.version}, ${step}` : step;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    proposals.value = await listStackProposals();
    // Step groups only. A version group is an irreversible merge of separate
    // trainings and has to be chosen, not un-chosen.
    chosen.value = proposals.value
      .filter((p) => !spansVersions(p))
      .map((p) => p.key);
  } catch (err) {
    error.value = errorDetail(err) || "Could not look for groups.";
    proposals.value = [];
    chosen.value = [];
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    working.value = false;
    load();
  },
  { immediate: true },
);

/**
 * Apply the ticked groups, one call each, then say what landed.
 *
 * One call per group because a group is one stack: the route creates one
 * `adapter_stack` and points its members at it. A group refused in the meantime
 * (409 — something stacked its rows first) is counted rather than thrown, so
 * one stale group does not discard the others.
 */
async function submit() {
  if (!canSubmit.value) return;
  const notices = useNoticeStore();
  const picked = proposals.value.filter((p) => chosen.value.includes(p.key));
  working.value = true;

  const results = await Promise.allSettled(
    picked.map((p) =>
      createStack(
        p.members.map((m) => m.model_id),
        p.name,
      ),
    ),
  );
  working.value = false;

  const failures = results.filter((r) => r.status === "rejected");
  const grouped = results.length - failures.length;
  if (failures.length) {
    console.warn(
      `[modelStacks] ${failures.length} group(s) could not be applied:`,
      failures.map((f) => errorDetail(f.reason) || f.reason),
    );
  }
  await shelf.fetchRows();
  notices.push({
    level: grouped ? "success" : "error",
    text: stackReceipt(grouped, failures.length),
  });
  if (grouped) emit("close");
}
</script>

<style scoped>
.ssp-note {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}

.ssp-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 46vh;
  overflow-y: auto;
}

.ssp-group {
  padding: var(--space-2) 0;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}

.ssp-group:last-child {
  border-bottom: none;
}

.ssp-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  cursor: pointer;
}

.ssp-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
}

.ssp-count,
.ssp-size {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

/* The one row state a reader has to notice before pressing, so it carries the
   warning role rather than the muted one the counts use. */
.ssp-tier {
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-warning));
}

.ssp-size {
  margin-left: auto;
}

.ssp-cover {
  margin: var(--space-1) 0 0 var(--space-6);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}
</style>
