<template>
  <!-- No `role` of its own: several of these render at once for a mixed batch,
       and five simultaneous alerts is five interruptions for one fact. The
       dialogs wrap the list in a single live region instead. -->
  <div class="rrn" :class="{ 'rrn--notice': !read.blocking }">
    <v-icon class="rrn-glyph" size="18">
      {{ read.blocking ? "mdi-alert-circle-outline" : "mdi-alert-outline" }}
    </v-icon>
    <div class="rrn-body">
      <p class="rrn-text">
        <!-- Only a refusal gets the lead: "can't run" over a run that IS
             about to happen would be the opposite of what the notice says. -->
        <b v-if="subject && read.blocking">{{ subject }} can't run.</b>
        <b v-else-if="subject">{{ subject }}:</b>
        {{ read.text }}
      </p>
      <ul v-if="read.files.length" class="rrn-files">
        <li v-for="file in read.files" :key="file.file">
          <span class="rrn-mono">{{ file.file }}</span>
          <template v-if="file.reason"> — {{ file.reason }}</template>
          <template v-if="file.folder">
            , expected in <span class="rrn-mono">models/{{ file.folder }}</span>
          </template>
          <ModelDownloadButton v-if="file.downloadable" :file="file.file" />
        </li>
      </ul>
      <!-- No catch-all "learn more" link. A refusal with no fix has its whole
           answer in the sentence above - which node is missing, which file,
           what the graph does that PixlStash will not run - and a link to a
           general page cannot say more about THIS run than that sentence
           already does. An action that goes somewhere unhelpful is worse than
           no action, because it has to be tried before it can be dismissed. -->
      <div v-if="hasAction" class="rrn-acts">
        <AppButton
          v-if="read.fix === FIX_SETTINGS"
          size="sm"
          :on-dark="onDark"
          @click="emit('settings')"
        >
          Settings › ComfyUI
        </AppButton>
        <!-- Beside Settings, not instead of it: the fix happens in another
             dialog stacked over this one, and nothing here is told when it is
             done. Without this the refusal outlived the thing it described. -->
        <AppButton
          v-if="read.retry"
          size="sm"
          icon-left="refresh"
          :loading="busy"
          :on-dark="onDark"
          @click="emit('retry')"
        >
          Retry
        </AppButton>
        <AppButton
          v-else-if="read.fix === FIX_DROP_LORA"
          size="sm"
          :on-dark="onDark"
          @click="emit('drop-lora')"
        >
          Run without the LoRA
        </AppButton>
        <!-- A recipe LoRA with no loader to go in (#1478): adding one is a
             workflow edit, so the fix is the dialog that makes it, on the
             workflow this notice is about. -->
        <AppButton
          v-else-if="read.fix === FIX_EDIT_LORAS"
          size="sm"
          :on-dark="onDark"
          @click="emit('edit-loras', reason.workflowId || '')"
        >
          Edit LoRAs…
        </AppButton>
        <!-- A pack PixlStash knows (#2054). The ellipsis is honest: the press
             asks first, in place, rather than installing. -->
        <template v-else-if="read.fix === FIX_INSTALL_PACK && offering">
          <AppButton
            v-for="pack in packs"
            :key="pack.id"
            size="sm"
            :on-dark="onDark"
            @click="ask(pack)"
          >
            Install {{ pack.name || pack.id }}…
          </AppButton>
        </template>
      </div>
      <!-- Inline, not a dialog over the Run popup: the warning is about the
           pack this sentence names, and a second modal would hide it. -->
      <template v-if="install.phase === 'confirm' || install.phase === 'installing'">
        <p class="rrn-text">
          This installs third-party code ({{ install.pack.id }}
          {{ install.pack.version }}, from the Comfy Registry) into your
          ComfyUI, where it runs with ComfyUI's full access. ComfyUI restarts
          afterwards if it is idle; a job it is running is never interrupted.
          Want a different version? Install it yourself from ComfyUI Manager.
        </p>
        <div class="rrn-acts">
          <AppButton
            size="sm"
            variant="primary"
            :loading="install.phase === 'installing'"
            :on-dark="onDark"
            @click="installPack"
          >
            Install and restart
          </AppButton>
          <AppButton
            size="sm"
            :disabled="install.phase === 'installing'"
            :on-dark="onDark"
            @click="cancel"
          >
            Cancel
          </AppButton>
        </div>
      </template>
      <p
        v-else-if="installNote"
        class="rrn-text"
        aria-live="polite"
      >
        {{ installNote }}
      </p>
      <div
        v-if="install.phase === 'manual' || install.phase === 'timeout'"
        class="rrn-acts"
      >
        <AppButton
          size="sm"
          icon-left="refresh"
          :loading="busy"
          :on-dark="onDark"
          @click="emit('retry')"
        >
          Retry
        </AppButton>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * One reason a run would not happen, with the thing to do about it (F5).
 *
 * The notice surface's error shape: a `--rail-w` rail in `--surface-error`
 * down the left edge, the glyph in the same hue, and the sentence left as
 * ordinary text (docs/design/notice-surface.md).
 *
 * The same shape in `--surface-warning` for an entry that is NOT a refusal
 * (#1463: a LoRA this ComfyUI does not have, whose loader the run leaves out).
 * Two hues rather than two components, because the owner reads one list and a
 * second surface would only make them work out which list to trust.
 */
import { computed, onBeforeUnmount, ref } from "vue";
import { VIcon } from "vuetify/components";

import { getNodePack, installNodePack } from "../../api/comfyui";
import { errorMessage } from "../../utils/apiError";
import {
  FIX_DROP_LORA,
  FIX_EDIT_LORAS,
  FIX_INSTALL_PACK,
  FIX_SETTINGS,
  readReason,
} from "../../utils/runReasons";
import AppButton from "../widgets/AppButton.vue";
import ModelDownloadButton from "../widgets/ModelDownloadButton.vue";

const props = defineProps({
  /**
   * One entry of a pre-flight group's `reasons`, or the notice
   * `runReasons.repairNotices` builds: a code plus its payload.
   */
  reason: { type: Object, required: true },
  /** Which card this is about; omitted when there is only the one. */
  subject: { type: String, default: "" },
  /** A retry is in flight. */
  busy: { type: Boolean, default: false },
  /** On a dark surface (the lightbox's Edit tab): the buttons follow it. */
  onDark: { type: Boolean, default: false },
});

const emit = defineEmits(["settings", "retry", "drop-lora", "edit-loras"]);

const read = computed(() => readReason(props.reason));

/** Whether anything here can actually be pressed; see the template. */
const hasAction = computed(
  () =>
    read.value.retry ||
    [FIX_SETTINGS, FIX_DROP_LORA, FIX_EDIT_LORAS].includes(read.value.fix) ||
    (read.value.fix === FIX_INSTALL_PACK && offering.value),
);

// ── Installing a node pack (#2054) ──────────────────────────────────────────
// The notice owns the whole wait, so the Run popup, Make More and the Edit tab
// need nothing of their own. It must NOT loop on `retry`: while ComfyUI
// restarts, the parent's pre-flight turns this reason into
// `comfyui_unreachable`, this notice unmounts, and a loop living here dies with
// it. So `retry` is emitted once, after the pack's nodes are seen to load.

const POLL_EVERY_MS = 3000;
const POLL_FOR_MS = 120000;

const packs = computed(() =>
  (props.reason?.packs || []).filter((pack) => pack?.id),
);

/**
 * `phase`: `idle`, `confirm` (the warning is up), `installing`, `waiting` (for
 * ComfyUI to come back with the nodes), `loaded`, `manual` (restart it
 * yourself), `timeout` or `failed`.
 */
const install = ref({ phase: "idle", pack: null, detail: "" });

/** Whether the Install buttons are offered: before an install, or after one failed. */
const offering = computed(() =>
  ["idle", "failed"].includes(install.value.phase),
);

const installNote = computed(() => {
  const { phase, pack, detail } = install.value;
  const name = pack?.name || pack?.id || "";
  if (phase === "waiting") return `Installed ${name}. Restarting ComfyUI…`;
  if (phase === "loaded") return `${name} is installed.`;
  if (phase === "manual") return `Installed ${name}. ${detail || "Restart ComfyUI yourself."}`;
  if (phase === "timeout") {
    return `ComfyUI has not come back with ${name} yet. If it did not restart, restart it yourself, then Retry.`;
  }
  if (phase === "failed") return detail;
  return "";
});

let pollTimer = null;
// Bumped on every stop, so an answer still in flight when the notice unmounts
// (or a new install begins) cannot act.
let pollRun = 0;

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
  pollRun += 1;
}

function ask(pack) {
  install.value = { phase: "confirm", pack, detail: "" };
}

function cancel() {
  stopPolling();
  install.value = { phase: "idle", pack: null, detail: "" };
}

async function installPack() {
  const { pack } = install.value;
  stopPolling();
  install.value = { phase: "installing", pack, detail: "" };
  const run = pollRun;
  try {
    const got = await installNodePack(pack.id);
    if (run !== pollRun) return;
    if (got?.restart === "manual") {
      install.value = { phase: "manual", pack, detail: got.detail || "" };
      return;
    }
    install.value = { phase: "waiting", pack, detail: "" };
    pollForNodes(pack);
  } catch (e) {
    if (run !== pollRun) return;
    install.value = {
      phase: "failed",
      pack,
      // The loopback policy's refusal, said as what to do about it.
      detail:
        e?.response?.status === 403
          ? "Install the pack from PixlStash on the computer it runs on, or from ComfyUI Manager."
          : errorMessage(e, `Could not install ${pack.name || pack.id}.`),
    };
  }
}

function pollForNodes(pack) {
  stopPolling();
  const run = pollRun;
  const deadline = Date.now() + POLL_FOR_MS;
  const tick = async () => {
    let loaded = false;
    try {
      loaded = (await getNodePack(pack.id))?.loaded === true;
    } catch (e) {
      console.debug("ComfyUI not answering yet while it restarts:", e);
    }
    if (run !== pollRun) return;
    if (loaded) {
      install.value = { phase: "loaded", pack, detail: "" };
      emit("retry");
    } else if (Date.now() >= deadline) {
      install.value = { phase: "timeout", pack, detail: "" };
    } else {
      pollTimer = setTimeout(tick, POLL_EVERY_MS);
    }
  };
  pollTimer = setTimeout(tick, POLL_EVERY_MS);
}

onBeforeUnmount(stopPolling);
</script>

<style scoped>
.rrn {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-left: var(--rail-w) solid rgb(var(--v-theme-surface-error));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.rrn--notice {
  border-left-color: rgb(var(--v-theme-surface-warning));
}

.rrn-glyph {
  color: rgb(var(--v-theme-surface-error));
}

.rrn--notice .rrn-glyph {
  color: rgb(var(--v-theme-surface-warning));
}

.rrn-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.rrn-text {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.rrn-files {
  margin: 0;
  padding-left: var(--space-5);
  font-size: var(--text-xs);
}

.rrn-mono {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.rrn-acts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

</style>
