<template>
  <AppDialog
    :open="open"
    :title="rows.length === 1 ? 'Replace a missing model' : 'Replace missing models'"
    @close="close"
    @accept="apply"
  >
    <p class="rmd-note rmd-quiet">
      The model you pick is loaded in every workflow below that loads the
      missing one. They keep their pictures, and each can be undone in its
      Workflow tab. A manual workflow is changed by cloning it instead.
    </p>
    <ul class="rmd-rows" data-testid="rmd-rows">
      <li v-for="row in rows" :key="row.name" class="rmd-row">
        <div class="rmd-head">
          <v-icon size="16" class="rmd-warn" aria-hidden="true"
            >mdi-alert-outline</v-icon
          >
          <span class="rmd-file">{{ fileName(row.name) }}</span>
        </div>
        <p v-if="row.fixable.length" class="rmd-note">
          {{ row.fixable.length === 1 ? "Loaded by" : `Loaded by ${row.fixable.length} workflows:` }}
          {{ row.fixable.map((id, i) => nameOf(id) || `workflow ${i + 1}`).join(" · ") }}
        </p>
        <p v-if="row.manual.length" class="rmd-note rmd-quiet">
          {{
            row.manual.length === 1
              ? `1 manual workflow loads it${row.fixable.length ? " too" : ""}; clone it with other models instead.`
              : `${row.manual.length} manual workflows load it${row.fixable.length ? " too" : ""}; clone them with other models instead.`
          }}
        </p>
        <p v-if="row.loading" class="rmd-note rmd-quiet">
          Reading what could replace it…
        </p>
        <AppSelect
          v-else-if="row.options.length"
          v-model="choices[row.name]"
          :label="`Replace ${fileName(row.name)} with`"
          hide-label
          :options="row.options"
          :disabled="applying"
          :data-testid="`rmd-choice-${row.name}`"
        />
        <p v-else-if="row.fixable.length" class="rmd-note rmd-quiet">
          {{ row.reason }}
        </p>
        <p
          v-for="failure in failures.filter((f) => f.name === row.name)"
          :key="failure.id"
          class="rmd-note rmd-bad"
          role="alert"
        >
          {{ nameOf(failure.id) || failure.id }}: {{ failure.message }}
        </p>
      </li>
    </ul>

    <template #footer>
      <AppButton :disabled="applying" @click="close">Cancel</AppButton>
      <AppButton
        variant="primary"
        icon-left="file-replace-outline"
        :loading="applying"
        :disabled="!writes.length || applying"
        data-testid="rmd-apply"
        @click="apply"
      >
        {{ applyLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * Replace missing base models in every workflow that loads them.
 *
 * One row per missing file, from the set cards the reader picked (or the one
 * tray whose note asked). The candidates are the Workflow tab's own "Replace
 * with…" answer (`GET …/model-swap?replacing=`) for the first workflow loading
 * the file; the choice is then written to each of them through the same
 * `PUT …/model-fix`, one at a time, and every refusal is listed by workflow.
 * A manual workflow is never written: its models change by cloning.
 */
import { computed, nextTick, onScopeDispose, reactive, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { readModelSwap, setWorkflowModelFix } from "../../api/workflows";
import { useWorkflowNames } from "../../composables/useWorkflowNames";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { errorMessage } from "../../utils/apiError";
import { onSessionReset } from "../../utils/apiClient";
import { NO_REPLACEMENT_TEXT, replacementLabel } from "../../utils/workflowCard";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppSelect from "../widgets/AppSelect.vue";

// A manual workflow's id; its models change by cloning, never by a fix.
const MANUAL_PREFIX = "manual:";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** Missing set heads (`headModel`): `{names, workflowsByName, workflowIds}`. */
  heads: { type: Array, default: () => [] },
});

const emit = defineEmits(["close"]);

const store = useModelShelfStore();
const { nameOf } = useWorkflowNames();

/** One row per missing file: `{name, fixable, manual, loading, options, reason}`. */
const rows = ref([]);
/** Missing file -> the shelf filename chosen for it, "" for none. */
const choices = reactive({});
const applying = ref(false);
/** `{name, id, message}` per workflow that refused its fix. */
const failures = ref([]);
let token = 0;
/** `name\u0000now\u0000id` of every write that landed since the dialog opened: a retry skips them, and a changed choice is a new write. */
const succeeded = reactive(new Set());
// Bumped by a credential change: a loop of writes started under the old one
// stops before its next request, and does not reload the cleared shelf.
let session = 0;
const unsubscribe = onSessionReset(() => {
  session += 1;
  token += 1;
  applying.value = false;
  invoker = null;
  emit("close");
});
onScopeDispose(() => unsubscribe());
// Opened from code (a pill, a tray note), so the overlay has no activator to
// hand focus back to: the control that opened it is remembered instead.
let invoker = null;

const writeKey = (write) => `${write.name}\u0000${write.now}\u0000${write.id}`;

/** Each `{name, now, id}` an Apply would write, less what already landed. */
const writes = computed(() =>
  rows.value.flatMap((row) =>
    choices[row.name]
      ? row.fixable
          .map((id) => ({ name: row.name, now: choices[row.name], id }))
          .filter((write) => !succeeded.has(writeKey(write)))
      : [],
  ),
);

const applyLabel = computed(() => {
  const n = new Set(writes.value.map((w) => w.id)).size;
  return n > 1 ? `Replace in ${n} workflows` : "Replace";
});

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    invoker = document.activeElement;
    load();
  },
  { immediate: true },
);

function rowsFor(heads) {
  const byName = new Map();
  for (const head of heads) {
    for (const name of head.names ?? [head.name]) {
      const ids = head.workflowsByName?.[name] ?? head.workflowIds ?? [];
      byName.set(name, new Set([...(byName.get(name) ?? []), ...ids]));
    }
  }
  return [...byName].map(([name, ids]) => {
    const sorted = [...ids].sort();
    return {
      name,
      fixable: sorted.filter((id) => !id.startsWith(MANUAL_PREFIX)),
      manual: sorted.filter((id) => id.startsWith(MANUAL_PREFIX)),
      loading: false,
      options: [],
      reason: "",
    };
  });
}

async function load() {
  const mine = ++token;
  failures.value = [];
  succeeded.clear();
  for (const key of Object.keys(choices)) delete choices[key];
  rows.value = rowsFor(props.heads);
  await Promise.all(
    rows.value.map(async (row) => {
      choices[row.name] = "";
      if (!row.fixable.length) return;
      row.loading = true;
      try {
        const body = await readModelSwap(row.fixable[0], {
          replacing: row.name,
          slotKind: "checkpoint",
        });
        if (mine !== token) return;
        const models = body.replacements ?? [];
        row.options = models.length
          ? [
              { value: "", label: "Replace with…" },
              ...models.map((model) => ({
                value: model.filename,
                label: replacementLabel(model),
              })),
            ]
          : [];
        row.reason =
          NO_REPLACEMENT_TEXT[body.replacements_reason] ||
          "Nothing on your shelf can replace it.";
      } catch (err) {
        console.warn(`[models] could not read what could replace ${row.name}`, err);
        if (mine !== token) return;
        row.reason = NO_REPLACEMENT_TEXT.unread;
      } finally {
        if (mine === token) row.loading = false;
      }
    }),
  );
}

/**
 * Write every chosen replacement, one workflow at a time.
 *
 * Sequential on purpose: each fix re-keys that workflow's cards, and two
 * workflows can share a topology. A refusal is listed and the rest go on.
 */
async function apply() {
  if (!writes.value.length || applying.value) return;
  applying.value = true;
  failures.value = [];
  const done = [];
  const mine = session;
  for (const write of [...writes.value]) {
    if (mine !== session) return;
    try {
      await setWorkflowModelFix(write.id, {
        was: write.name,
        now: write.now,
        slot_kind: "checkpoint",
      });
      succeeded.add(writeKey(write));
      done.push(write);
    } catch (err) {
      console.warn(
        `[models] could not replace ${write.name} with ${write.now} in workflow ${write.id}`,
        err,
      );
      failures.value.push({
        name: write.name,
        id: write.id,
        message: errorMessage(err, "Could not replace that model."),
      });
    }
  }
  if (mine !== session) return;
  applying.value = false;
  // The sets are read again: pictures made with a fixed model join its set.
  // `loadWorkflowSets` reports its own failure on the grid (`setsError`).
  if (done.length) await store.loadWorkflowSets({ force: true });
  if (mine === session && !failures.value.length) close();
}

function close() {
  if (applying.value) return;
  token += 1;
  emit("close");
  const back = invoker;
  invoker = null;
  // The tray note's button is gone once its set is fixed; the shelf is not.
  nextTick(() =>
    (back?.isConnected ? back : document.querySelector(".shelf"))?.focus?.(),
  );
}

/** A recorded model value as a person looks for it: the file, no folders. */
function fileName(value) {
  return String(value).split(/[\\/]/).pop();
}
</script>

<style scoped>
.rmd-rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  margin: 0;
  padding: 0;
  list-style: none;
}

.rmd-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.rmd-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 600;
}

.rmd-warn {
  color: rgb(var(--v-theme-surface-warning));
}

.rmd-file {
  overflow-wrap: anywhere;
}

.rmd-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-body);
}

.rmd-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.rmd-bad {
  color: rgb(var(--v-theme-surface-error));
}
</style>
