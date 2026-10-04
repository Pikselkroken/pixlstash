<template>
  <AppDialog
    :open="open"
    title="Export recipe"
    size="md"
    @close="emit('close')"
    @accept="exportTheRecipe"
  >
    <p v-if="loading" class="exr-note">Reading what this file would say…</p>
    <p v-else-if="loadError" class="exr-note exr-note--bad" role="alert">
      {{ loadError }}
    </p>

    <template v-else-if="payload">
      <p class="exr-lede">
        This file lets anyone make pictures like yours. It contains:
      </p>

      <!-- The warning surface, and every line of it comes from the server's
           own `shares`. The dialog does not compose that list: the file is
           written by `GET /recipes/{id}/export` and only that route knows what
           went into it, so a list assembled here would drift the day the
           export changes. -->
      <div class="exr-shares">
        <v-icon class="exr-glyph" size="18">mdi-alert-circle-outline</v-icon>
        <div>
          <p v-for="share in payload.shares" :key="share" class="exr-share">
            {{ share }}
          </p>
          <p v-if="!payload.shares.length" class="exr-share exr-quiet">
            This recipe is empty, so the file says nothing about you.
          </p>
        </div>
      </div>

      <p class="exr-note exr-quiet">
        Want to share the workflow without the look?
        <b>Export workflow</b> leaves the prompt blank and the recipe LoRA
        slots empty.
      </p>
      <p v-if="exportError" class="exr-note exr-note--bad" role="alert">
        {{ exportError }}
      </p>
    </template>

    <template #footer>
      <AppButton :disabled="!!busy" @click="emit('close')">Cancel</AppButton>
      <AppButton
        v-if="payload?.recipe?.workflow_id"
        icon-left="sitemap-outline"
        :loading="busy === 'workflow'"
        :disabled="!!busy"
        @click="exportTheWorkflow"
      >
        Export workflow
      </AppButton>
      <AppButton
        ref="primaryButton"
        variant="primary"
        icon-left="export"
        :loading="busy === 'recipe'"
        :disabled="!payload || !!busy"
        @click="exportTheRecipe"
      >
        Export recipe
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * Export recipe (v1.12 F6) - the consent step before a recipe leaves the
 * machine.
 *
 * A recipe IS the prompt and the LoRA names, so nothing can be withheld and
 * still make anything; the dialog's whole job is to say exactly what the file
 * gives away before it is written, and to offer **Export workflow** beside it
 * as the share that keeps the look back.
 */
import { nextTick, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { exportSavedRecipe } from "../../api/recipes";
import { exportWorkflow } from "../../api/workflows";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorMessage } from "../../utils/apiError";
import { saveJsonAs } from "../../utils/downloadFile";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The saved recipe to export. */
  recipeId: { type: Number, default: null },
});

const emit = defineEmits(["close"]);

const notices = useNoticeStore();

const payload = ref(null);
const loading = ref(false);
const loadError = ref("");
const exportError = ref("");
/**
 * Whether the workflow export is out.
 *
 * Only that one: exporting the recipe writes a payload this dialog already
 * holds, so it is over within the click and has no in-flight state to show.
 */
/** The export in flight, `"recipe"` or `"workflow"`, or `""` for none. */
const busy = ref("");
/** The primary, focused once there is something to agree to. */
const primaryButton = ref(null);

async function load() {
  payload.value = null;
  loadError.value = "";
  exportError.value = "";
  if (!props.open || !props.recipeId) return;
  loading.value = true;
  const wanted = props.recipeId;
  try {
    const body = await exportSavedRecipe(wanted);
    // Late answer for a recipe the dialog has since moved off: dropped rather
    // than shown, so the list never belongs to a different file than the
    // button would write.
    if (wanted !== props.recipeId) return;
    payload.value = {
      filename: body?.filename || "recipe.json",
      recipe: body?.recipe || {},
      shares: Array.isArray(body?.shares) ? body.shares : [],
    };
  } catch (err) {
    if (wanted === props.recipeId) {
      loadError.value = errorMessage(err, "Could not read this recipe.");
    }
  } finally {
    if (wanted === props.recipeId) loading.value = false;
  }
}

watch(() => [props.open, props.recipeId], load, { immediate: true });

// Focus lands on the action once the list it is agreeing to is on screen.
// Before that there is nothing to agree to, so focusing it would offer the
// gesture ahead of the sentence it depends on.
watch(payload, async (ready) => {
  if (!ready) return;
  await nextTick();
  primaryButton.value?.$el?.focus?.();
});

async function exportTheRecipe() {
  if (!payload.value || busy.value) return;
  busy.value = "recipe";
  exportError.value = "";
  try {
    if (!(await saveJsonAs(payload.value.recipe, payload.value.filename))) {
      return;
    }
  } catch (err) {
    console.warn(`[recipes] could not save ${payload.value.filename}`, err);
    exportError.value = errorMessage(err, "Could not save that recipe.");
    return;
  } finally {
    busy.value = "";
  }
  emit("close");
}

async function exportTheWorkflow() {
  const key = payload.value?.recipe?.workflow_id;
  if (!key || busy.value) return;
  busy.value = "workflow";
  exportError.value = "";
  // Which step failed decides the fallback text: the server's export, or
  // writing the file the owner picked.
  let saving = false;
  try {
    const body = await exportWorkflow(key);
    saving = true;
    const saved = await saveJsonAs(
      body?.workflow || {},
      body?.filename || "workflow.json",
    );
    // Cancelled in the Save dialog: the dialog stays, nothing was written.
    if (!saved) return;
    // What the scrub took, said out loud. This dialog exists to tell the
    // owner what a file does and does not carry, so the safe alternative
    // cannot be the one that goes out silently.
    const removed = Array.isArray(body?.removed) ? body.removed : [];
    notices.push({
      level: "success",
      text: removed.length
        ? `Exported the workflow. Left out: ${removed.join(", ")}.`
        : "Exported the workflow, with nothing left out.",
    });
    emit("close");
  } catch (err) {
    console.warn(`[recipes] could not export workflow ${key}`, err);
    exportError.value = errorMessage(
      err,
      saving
        ? "Could not save that workflow."
        : "Could not export that workflow on its own.",
    );
  } finally {
    busy.value = "";
  }
}
</script>

<style scoped>
.exr-lede,
.exr-note,
.exr-share {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.exr-note {
  font-size: var(--text-xs);
}

.exr-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.exr-note--bad {
  color: rgb(var(--v-theme-surface-error));
}

/* The warning surface of the design: the glyph and the rail carry the hue and
   the sentences stay text, which is the app's notice shape. */
.exr-shares {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-left: var(--rail-w) solid rgb(var(--v-theme-surface-warning));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.exr-glyph {
  color: rgb(var(--v-theme-surface-warning));
}

.exr-share + .exr-share {
  margin-top: var(--space-2);
}
</style>
