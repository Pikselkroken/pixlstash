<template>
  <div
    v-if="progress.visible"
    class="comfyui-progress"
    :class="{ 'comfyui-progress-error': progress.status === 'failed' }"
  >
    <div class="comfyui-progress-header">
      <div class="comfyui-progress-title">
        {{ progress.message }}
      </div>
      <button
        v-if="progress.status !== 'completed' && progress.status !== 'failed'"
        class="comfyui-abort-btn"
        :disabled="isAborting"
        title="Abort ComfyUI workflow"
        @click.stop="abortComfyui"
      >
        {{ isAborting ? "…" : "✕" }}
      </button>
      <button
        v-else-if="progress.status === 'failed'"
        class="comfyui-abort-btn"
        title="Dismiss ComfyUI error"
        @click.stop="dismissComfyuiProgress"
      >
        ✕
      </button>
    </div>
    <div class="comfyui-progress-bar">
      <div
        class="comfyui-progress-fill"
        :style="{ width: `${progressPercent}%` }"
      ></div>
    </div>
  </div>
</template>

<script setup>
/**
 * ComfyUiRunner
 *
 * Headless component that manages all ComfyUI WebSocket state, progress tracking
 * and overlay refresh logic. Renders only its own progress bar overlay.
 *
 * Props:
 *   backendUrl            - Backend base URL for WS and API calls.
 *   overlayOpen           - Whether the image overlay is currently open.
 *   overlayImageId        - Currently displayed image id in the overlay.
 *   allGridImages         - Current grid image list.
 *   lastFetchedGridImages - Last successfully fetched grid image list.
 *   getPictureStackId     - Function(img) → stackId string | null.
 *   selectNewestStackMember - Function(members[]) → img | null.
 *
 * Emits:
 *   refresh-grid({ preserveScroll })   - Request a grid re-fetch.
 *   refresh-sidebar                    - Request a sidebar refresh.
 *   update:overlayImageId(id)          - Update the overlay's active image.
 *
 * Exposes:
 *   handleComfyuiRun(payload)          - Call when a comfyui-run event is received.
 *   maybeRefreshOverlayForComfyui()    - Call after each grid fetch to update overlay.
 *   clientId                           - Ref<string|null> with the current client id.
 *   progress                           - Reactive progress object { visible, status, percent, message }.
 *   progressPercent                    - Computed clamped percentage (0-100).
 */
import { ref, reactive, computed, onUnmounted, watch } from "vue";
import { apiClient } from "../utils/apiClient";
import { formatComfyuiExecutionErrorMessage } from "../utils/utils.js";

const props = defineProps({
  backendUrl: { type: String, default: "" },
  wsPluginProgress: {
    type: Object,
    default: () => ({ key: 0, payload: null }),
  },
  overlayOpen: { type: Boolean, default: false },
  overlayImageId: { default: null },
  allGridImages: { type: Array, default: () => [] },
  lastFetchedGridImages: { type: Array, default: () => [] },
  getPictureStackId: { type: Function, required: true },
  selectNewestStackMember: { type: Function, required: true },
});

const emit = defineEmits([
  "refresh-grid",
  "refresh-sidebar",
  "update:overlayImageId",
]);

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const progress = reactive({
  visible: false,
  status: "idle",
  percent: 0,
  message: "ComfyUI running...",
});

const comfyuiActivePromptIds = ref(new Set());
const comfyuiCompletedPromptIds = ref(new Set());
const comfyuiPromptPictureMap = reactive({});
const comfyuiPromptLastSeen = reactive({});
const comfyuiLastMessageAt = ref(0);
const COMFYUI_STALE_MS = 30000;
const COMFYUI_WATCHDOG_MS = 2000;
const comfyuiPendingOverlayRefresh = ref(false);
const comfyuiSourcePictureId = ref(null);
const clientId = ref(null);
const comfyuiRefreshRetryCounts = reactive({});
const isAborting = ref(false);
const comfyuiFailureLocked = ref(false);
const comfyuiWsState = reactive({
  connecting: false,
  url: "",
});
let comfyuiWs = null;
let comfyuiHideTimer = null;
let comfyuiWatchdogTimer = null;
const comfyuiRefreshRetryTimers = new Map();

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const progressPercent = computed(() => {
  const percent = Number(progress.percent) || 0;
  return Math.min(100, Math.max(0, Math.round(percent)));
});

// ---------------------------------------------------------------------------
// Debug helpers
// ---------------------------------------------------------------------------

function logComfyuiDebug(_message, _details = {}) {
  // Intentionally no-op; keep calls in place for easy temporary diagnostics.
}

// ---------------------------------------------------------------------------
// Client id
// ---------------------------------------------------------------------------

function getComfyuiClientId() {
  if (!clientId.value) {
    clientId.value = `pixlstash-${Math.random().toString(36).slice(2, 10)}`;
  }
  return clientId.value;
}

// ---------------------------------------------------------------------------
// Progress / hide timer
// ---------------------------------------------------------------------------

function clearComfyuiHideTimer() {
  if (comfyuiHideTimer) {
    clearTimeout(comfyuiHideTimer);
    comfyuiHideTimer = null;
  }
}

function scheduleComfyuiHide() {
  clearComfyuiHideTimer();
  logComfyuiDebug("schedule-hide", {
    activeCount: comfyuiActivePromptIds.value.size,
    percent: progress.percent,
    status: progress.status,
  });
  comfyuiHideTimer = setTimeout(() => {
    progress.visible = false;
    progress.status = "idle";
    progress.percent = 0;
    progress.message = "ComfyUI running...";
    logComfyuiDebug("hide-complete");
  }, 1200);
}

function finalizeComfyuiProgress({ refresh = true } = {}) {
  logComfyuiDebug("finalize", {
    refresh,
    activeCount: comfyuiActivePromptIds.value.size,
    percent: progress.percent,
  });
  progress.percent = 100;
  progress.visible = true;
  progress.status = "completed";
  progress.message = "ComfyUI complete";
  if (refresh) {
    emit("refresh-grid", { preserveScroll: true });
    emit("refresh-sidebar");
  }
  if (comfyuiActivePromptIds.value.size === 0) {
    scheduleComfyuiHide();
  }
}

// ---------------------------------------------------------------------------
// Refresh retry
// ---------------------------------------------------------------------------

function clearComfyuiRefreshRetries() {
  for (const timer of comfyuiRefreshRetryTimers.values()) {
    clearTimeout(timer);
  }
  comfyuiRefreshRetryTimers.clear();
  Object.keys(comfyuiRefreshRetryCounts).forEach((key) => {
    delete comfyuiRefreshRetryCounts[key];
  });
}

function startComfyuiWatchdog() {
  if (comfyuiWatchdogTimer) return;
  comfyuiWatchdogTimer = setInterval(() => {
    if (!comfyuiActivePromptIds.value.size) return;
    pruneStaleComfyuiPrompts(Date.now());
  }, COMFYUI_WATCHDOG_MS);
}

function stopComfyuiWatchdog() {
  if (!comfyuiWatchdogTimer) return;
  clearInterval(comfyuiWatchdogTimer);
  comfyuiWatchdogTimer = null;
}

function recordComfyuiActivity(promptKey) {
  const now = Date.now();
  comfyuiLastMessageAt.value = now;
  if (promptKey) {
    comfyuiPromptLastSeen[promptKey] = now;
  }
  return now;
}

function pruneStaleComfyuiPrompts(now = Date.now()) {
  const active = comfyuiActivePromptIds.value;
  if (!active.size) return;
  const lastAny = comfyuiLastMessageAt.value || now;
  const stale = [];
  for (const promptKey of active.values()) {
    const lastSeen = comfyuiPromptLastSeen[promptKey] || lastAny;
    if (now - lastSeen > COMFYUI_STALE_MS) {
      stale.push(promptKey);
    }
  }
  for (const promptKey of stale) {
    logComfyuiDebug("prompt-stale-timeout", { promptKey });
    markComfyuiPromptFailed(
      promptKey,
      "stale-timeout",
      "ComfyUI run aborted before completion. Check ComfyUI logs for details.",
    );
  }
}

function scheduleComfyuiRefreshRetry(promptKey, pictureId, attempt = 1) {
  if (!pictureId) return;
  if (attempt > 8) {
    const key = promptKey || `pic:${pictureId}`;
    if (comfyuiRefreshRetryCounts[key]) {
      delete comfyuiRefreshRetryCounts[key];
    }
    logComfyuiDebug("refresh-retry-abandon", {
      promptKey: key,
      pictureId,
    });
    if (String(comfyuiSourcePictureId.value || "") === String(pictureId)) {
      comfyuiPendingOverlayRefresh.value = false;
    }
    return;
  }
  const key = promptKey || `pic:${pictureId}`;
  const delay = 2000 * attempt;
  const existing = comfyuiRefreshRetryTimers.get(key);
  if (existing) {
    clearTimeout(existing);
  }
  comfyuiRefreshRetryCounts[key] = attempt;
  const timer = setTimeout(() => {
    comfyuiRefreshRetryTimers.delete(key);
    if (!comfyuiPendingOverlayRefresh.value) return;
    logComfyuiDebug("refresh-retry", {
      promptKey: key,
      pictureId,
      attempt,
    });
    comfyuiSourcePictureId.value = pictureId;
    emit("refresh-grid", { preserveScroll: true });
    emit("refresh-sidebar");
    scheduleComfyuiRefreshRetry(promptKey, pictureId, attempt + 1);
  }, delay);
  comfyuiRefreshRetryTimers.set(key, timer);
}

function hasComfyuiRefreshRetry(pictureId) {
  if (!pictureId) return false;
  const key = `pic:${pictureId}`;
  if (comfyuiRefreshRetryCounts[key]) return true;
  return Object.keys(comfyuiRefreshRetryCounts).length > 0;
}

// ---------------------------------------------------------------------------
// Stack / overlay helpers
// ---------------------------------------------------------------------------

async function fetchStackIdForPicture(pictureId) {
  if (!pictureId || !props.backendUrl) return null;
  try {
    const res = await apiClient.get(
      `${props.backendUrl}/pictures/${pictureId}/metadata`,
    );
    const stackId = res.data?.stack_id ?? res.data?.stackId ?? null;
    return stackId != null ? String(stackId) : null;
  } catch (err) {
    logComfyuiDebug("stack-id-fetch-failed", {
      pictureId,
      error: err?.message || String(err),
    });
    return null;
  }
}

async function fetchStackMembersForOverlay(stackId) {
  if (!stackId || !props.backendUrl) return [];
  try {
    const res = await apiClient.get(
      `${props.backendUrl}/stacks/${stackId}/pictures`,
      { params: { fields: "grid" } },
    );
    return Array.isArray(res.data) ? res.data : [];
  } catch (err) {
    logComfyuiDebug("stack-members-fetch-failed", {
      stackId,
      error: err?.message || String(err),
    });
    return [];
  }
}

// ---------------------------------------------------------------------------
// Prompt tracking
// ---------------------------------------------------------------------------

function markComfyuiPromptComplete(promptKey, reason) {
  if (comfyuiFailureLocked.value) {
    logComfyuiDebug("prompt-complete-ignored-failure-locked", {
      promptKey,
      reason,
    });
    return;
  }
  if (!promptKey) return;
  const completed = comfyuiCompletedPromptIds.value;
  if (completed.has(promptKey)) return;
  completed.add(promptKey);
  comfyuiCompletedPromptIds.value = new Set(completed);
  const active = comfyuiActivePromptIds.value;
  if (active.has(promptKey)) {
    active.delete(promptKey);
    comfyuiActivePromptIds.value = new Set(active);
  }
  const pictureId = comfyuiPromptPictureMap[promptKey] || null;
  logComfyuiDebug("prompt-complete", {
    promptKey,
    reason,
    pictureId,
    activeCount: comfyuiActivePromptIds.value.size,
  });
  if (pictureId != null) {
    comfyuiSourcePictureId.value = pictureId;
    comfyuiPendingOverlayRefresh.value = true;
  }
  emit("refresh-grid", { preserveScroll: true });
  emit("refresh-sidebar");
  scheduleComfyuiRefreshRetry(promptKey, pictureId, 1);
  clearComfyuiHideTimer();
  progress.visible = false;
  progress.status = "idle";
  progress.percent = 0;
  progress.message = "ComfyUI running...";
  if (comfyuiActivePromptIds.value.size === 0) {
    stopComfyuiWatchdog();
    finalizeComfyuiProgress({ refresh: false });
  }
}

function markComfyuiPromptFailed(promptKey, reason, errorMessage) {
  const message = String(errorMessage || "").trim() || "ComfyUI failed";
  const currentMessage = String(progress.message || "").trim();
  const nextLower = message.toLowerCase();
  const currentLower = currentMessage.toLowerCase();

  // Keep the first failure sticky, only allowing an upgrade to a clearer OOM cause.
  if (comfyuiFailureLocked.value && progress.status === "failed") {
    const currentIsOom = currentLower.includes("out of memory");
    const nextIsOom = nextLower.includes("out of memory");
    if (!currentIsOom && nextIsOom) {
      progress.message = message;
      logComfyuiDebug("prompt-failed-upgraded", {
        promptKey,
        reason,
        message,
      });
    }
    return;
  }

  comfyuiFailureLocked.value = true;

  if (promptKey) {
    const completed = comfyuiCompletedPromptIds.value;
    if (!completed.has(promptKey)) {
      completed.add(promptKey);
      comfyuiCompletedPromptIds.value = new Set(completed);
    }
    const active = comfyuiActivePromptIds.value;
    if (active.has(promptKey)) {
      active.delete(promptKey);
      comfyuiActivePromptIds.value = new Set(active);
    }
  }

  // Fail-fast behavior: any ComfyUI error terminates the current tracked run.
  comfyuiActivePromptIds.value = new Set();
  Object.keys(comfyuiPromptLastSeen).forEach(
    (key) => delete comfyuiPromptLastSeen[key],
  );

  progress.visible = true;
  progress.status = "failed";
  progress.percent = 0;
  progress.message = message;

  logComfyuiDebug("prompt-failed", {
    promptKey,
    reason,
    message,
    activeCount: comfyuiActivePromptIds.value.size,
  });

  clearComfyuiHideTimer();

  emit("refresh-grid", { preserveScroll: true });
  emit("refresh-sidebar");
  stopComfyuiWatchdog();
}

function resolveErrorPromptKey(promptKey, activePromptIds) {
  if (promptKey) return promptKey;
  const activeList = Array.from(activePromptIds || []);
  if (activeList.length === 1) return activeList[0];
  return null;
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

function buildComfyuiWsUrl(baseUrl) {
  const trimmed = String(baseUrl || "")
    .trim()
    .replace(/\/+$/, "");
  if (!trimmed) return "";
  const wsBase = trimmed.startsWith("https")
    ? trimmed.replace(/^https/, "wss")
    : trimmed.replace(/^http/, "ws");
  const cid = getComfyuiClientId();
  return `${wsBase}/ws/comfyui?clientId=${encodeURIComponent(cid)}`;
}

function normalizeComfyuiPercent(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return null;
  if (num <= 0) return 0;
  if (num <= 1) return num * 100;
  if (num <= 100) return num;
  return Math.min(100, num);
}

function percentFromRange(value, max) {
  const v = Number(value);
  const m = Number(max);
  if (!Number.isFinite(v) || !Number.isFinite(m) || m <= 0) return null;
  return (v / m) * 100;
}

function extractOverallComfyuiPercent(payload) {
  const data = payload?.data || {};
  const status = data?.status || payload?.status || {};
  const execInfo = status?.exec_info || status?.execInfo || {};
  const candidates = [
    payload?.percent,
    data?.percent,
    execInfo?.progress,
    execInfo?.percent,
    execInfo?.percentage,
    status?.progress,
    status?.percent,
    data?.progress?.percent,
    data?.progress?.percentage,
    data?.progress_state?.percent,
    data?.total_progress,
    data?.totalProgress,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeComfyuiPercent(candidate);
    if (normalized != null) return normalized;
  }

  const rangeCandidates = [
    [execInfo?.current, execInfo?.total],
    [execInfo?.completed, execInfo?.total],
    [status?.current, status?.total],
    [data?.current, data?.total],
    [data?.value, data?.max],
    [data?.progress?.value, data?.progress?.max],
    [data?.progress?.current, data?.progress?.total],
    [data?.progress_state?.value, data?.progress_state?.max],
    [data?.progress_state?.current, data?.progress_state?.total],
  ];
  for (const [value, max] of rangeCandidates) {
    const computed = percentFromRange(value, max);
    if (computed != null) return computed;
  }
  return null;
}

function parseComfyuiPayload(raw) {
  try {
    return JSON.parse(raw || "{}");
  } catch {
    logComfyuiDebug("message-parse-error", {
      raw: String(raw || "").slice(0, 200),
    });
    return null;
  }
}

function handleComfyuiPayload(payload) {
  if (!payload || typeof payload !== "object") return;
  const type = payload?.type;
  const data = payload?.data || {};
  const promptId =
    data?.prompt_id || data?.promptId || payload?.prompt_id || null;
  const active = comfyuiActivePromptIds.value;
  const promptKey = promptId != null ? String(promptId) : null;
  const isRelevant = !promptKey || active.has(promptKey);
  const now = recordComfyuiActivity(promptKey);
  logComfyuiDebug("message", {
    type,
    promptKey,
    activeCount: active.size,
    status: progress.status,
    percent: progress.percent,
    relevant: isRelevant,
  });
  if (!isRelevant) {
    pruneStaleComfyuiPrompts(now);
    return;
  }

  if (comfyuiFailureLocked.value && progress.status === "failed") {
    pruneStaleComfyuiPrompts(now);
    return;
  }

  if (
    type === "execution_error" ||
    type === "execution_failed" ||
    type === "error"
  ) {
    const resolvedPromptKey = resolveErrorPromptKey(promptKey, active);
    const message = formatComfyuiExecutionErrorMessage(
      payload,
      "ComfyUI failed",
    );
    markComfyuiPromptFailed(resolvedPromptKey, type || "error", message);
    pruneStaleComfyuiPrompts(now);
    return;
  }

  if (
    progress.status === "failed" &&
    !promptKey &&
    (type === "executing" || type === "status" || type === "progress")
  ) {
    pruneStaleComfyuiPrompts(now);
    return;
  }

  const overallPercent = extractOverallComfyuiPercent(payload);
  if (overallPercent != null) {
    progress.percent = overallPercent;
    progress.visible = true;
    progress.status = "running";
    progress.message = "ComfyUI running...";
    if (overallPercent >= 100) {
      if (promptKey) {
        logComfyuiDebug("finalize-from-percent", {
          promptKey,
          overallPercent,
        });
        markComfyuiPromptComplete(promptKey, "overall-percent");
        pruneStaleComfyuiPrompts(now);
        return;
      }
      if (active.size <= 1) {
        active.clear();
        comfyuiActivePromptIds.value = new Set(active);
        finalizeComfyuiProgress({ refresh: true });
        pruneStaleComfyuiPrompts(now);
        return;
      }
    }
  }

  if (type === "progress") {
    const value = Number(data?.value ?? data?.current ?? 0);
    const max = Number(data?.max ?? data?.total ?? 0);
    if (max > 0 && overallPercent == null) {
      progress.percent = (value / max) * 100;
      progress.visible = true;
      progress.status = "running";
      progress.message = "ComfyUI running...";
      if (promptKey && value >= max) {
        markComfyuiPromptComplete(promptKey, "progress-max");
      }
    }
    pruneStaleComfyuiPrompts(now);
    return;
  }

  if (type === "progress_state") {
    const value = Number(data?.value ?? data?.current ?? 0);
    const max = Number(data?.max ?? data?.total ?? 0);
    if (promptKey && max > 0 && value >= max) {
      markComfyuiPromptComplete(promptKey, "progress-state-max");
      pruneStaleComfyuiPrompts(now);
      return;
    }
  }

  if (type === "status" && overallPercent != null) {
    pruneStaleComfyuiPrompts(now);
    return;
  }

  if (type === "executing") {
    if (data?.node == null) {
      if (promptKey) {
        logComfyuiDebug("finalize-from-executing", { promptKey });
        markComfyuiPromptComplete(promptKey, "executing-null");
        pruneStaleComfyuiPrompts(now);
      } else {
        active.clear();
        comfyuiActivePromptIds.value = new Set(active);
        finalizeComfyuiProgress({ refresh: true });
        pruneStaleComfyuiPrompts(now);
      }
    } else {
      progress.visible = true;
      progress.status = "running";
      if (progress.percent <= 0) {
        progress.percent = 1;
      }
    }
    pruneStaleComfyuiPrompts(now);
    return;
  }

  if (type === "execution_success" && promptKey) {
    markComfyuiPromptComplete(promptKey, "execution-success");
    pruneStaleComfyuiPrompts(now);
    return;
  }

  if (type === "executed" || type === "execution_cached") {
    progress.visible = true;
    progress.status = "running";
    if (progress.percent <= 0) {
      progress.percent = 1;
    }
  }
  pruneStaleComfyuiPrompts(now);
}

function handleComfyuiWsMessage(event) {
  const raw = event?.data;
  if (raw instanceof Blob) {
    raw
      .text()
      .then((text) => {
        const payload = parseComfyuiPayload(text);
        if (payload) handleComfyuiPayload(payload);
      })
      .catch(() => {
        logComfyuiDebug("message-parse-error", { raw: "[blob]" });
      });
    return;
  }
  if (raw instanceof ArrayBuffer) {
    const decoder =
      typeof TextDecoder !== "undefined" ? new TextDecoder() : null;
    const text = decoder ? decoder.decode(raw) : "";
    const payload = parseComfyuiPayload(text);
    if (payload) handleComfyuiPayload(payload);
    return;
  }
  const payload = parseComfyuiPayload(raw);
  if (payload) handleComfyuiPayload(payload);
}

async function ensureComfyuiSocket() {
  if (comfyuiWsState.connecting) return;
  if (
    comfyuiWs &&
    (comfyuiWs.readyState === WebSocket.OPEN ||
      comfyuiWs.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  comfyuiWsState.connecting = true;
  const wsUrl = buildComfyuiWsUrl(props.backendUrl);
  comfyuiWsState.url = wsUrl;
  try {
    if (!wsUrl) {
      return;
    }
    comfyuiWs = new WebSocket(wsUrl);
    comfyuiWs.onmessage = handleComfyuiWsMessage;
    comfyuiWs.onclose = () => {
      const hadActive = comfyuiActivePromptIds.value.size > 0;
      comfyuiWs = null;
      if (hadActive) {
        comfyuiActivePromptIds.value = new Set();
        Object.keys(comfyuiPromptLastSeen).forEach(
          (key) => delete comfyuiPromptLastSeen[key],
        );
        markComfyuiPromptFailed(
          null,
          "ws-closed",
          "ComfyUI connection closed before completion.",
        );
      }
    };
    comfyuiWs.onerror = () => {
      comfyuiWs = null;
    };
  } catch {
    comfyuiWs = null;
  } finally {
    comfyuiWsState.connecting = false;
  }
}

// ---------------------------------------------------------------------------
// Abort
// ---------------------------------------------------------------------------

async function abortComfyui() {
  if (isAborting.value) return;
  isAborting.value = true;
  try {
    await apiClient.post(`${props.backendUrl}/comfyui/abort`);
  } catch (err) {
    // Best-effort abort — ignore errors and still reset local state
    logComfyuiDebug("abort-error", { error: err?.message || String(err) });
  } finally {
    isAborting.value = false;
  }
  // Clear all tracked state so the UI resets cleanly
  comfyuiActivePromptIds.value = new Set();
  comfyuiCompletedPromptIds.value = new Set();
  Object.keys(comfyuiPromptPictureMap).forEach(
    (k) => delete comfyuiPromptPictureMap[k],
  );
  Object.keys(comfyuiPromptLastSeen).forEach(
    (k) => delete comfyuiPromptLastSeen[k],
  );
  stopComfyuiWatchdog();
  clearComfyuiHideTimer();
  clearComfyuiRefreshRetries();
  progress.visible = true;
  progress.status = "failed";
  progress.percent = 0;
  progress.message = "Aborted";
}

function dismissComfyuiProgress() {
  clearComfyuiHideTimer();
  stopComfyuiWatchdog();
  comfyuiActivePromptIds.value = new Set();
  comfyuiFailureLocked.value = false;
  progress.visible = false;
  progress.status = "idle";
  progress.percent = 0;
  progress.message = "ComfyUI running...";
}

// ---------------------------------------------------------------------------
// Public API: handleComfyuiRun
// ---------------------------------------------------------------------------

function handleComfyuiRun(payload) {
  const prompts = Array.isArray(payload?.prompts) ? payload.prompts : [];
  const ids = prompts
    .map((entry) => entry?.prompt_id || entry?.promptId)
    .filter((id) => id != null)
    .map((id) => String(id));
  if (!ids.length) return;
  comfyuiFailureLocked.value = false;
  const next = new Set(comfyuiActivePromptIds.value);
  for (const id of ids) {
    next.add(id);
    const entry = prompts.find(
      (item) => String(item?.prompt_id || item?.promptId) === id,
    );
    const pictureId = entry?.picture_id ?? payload?.pictureId ?? null;
    if (pictureId != null) {
      comfyuiPromptPictureMap[id] = pictureId;
    }
    comfyuiPromptLastSeen[id] = Date.now();
  }
  comfyuiActivePromptIds.value = next;
  logComfyuiDebug("run-queued", {
    promptIds: ids,
    activeCount: next.size,
    pictureId: payload?.pictureId ?? null,
  });
  progress.visible = true;
  progress.status = "queued";
  progress.percent = 0;
  progress.message = "ComfyUI queued...";
  clearComfyuiHideTimer();
  comfyuiSourcePictureId.value = payload?.pictureId ?? null;
  comfyuiPendingOverlayRefresh.value = Boolean(comfyuiSourcePictureId.value);
  startComfyuiWatchdog();
  void ensureComfyuiSocket();
}

// Promote backend ComfyUI failure progress events into the ComfyUI runner banner
// so an errored run fails immediately even when ComfyUI WS emits no explicit error.
watch(
  () => props.wsPluginProgress,
  (wrapped) => {
    if (!wrapped || typeof wrapped !== "object") return;
    const payload = wrapped.payload;
    if (!payload || typeof payload !== "object") return;

    const pluginName = String(payload.plugin || "")
      .trim()
      .toLowerCase();
    if (pluginName !== "comfyui") return;

    const status = String(payload.status || "")
      .trim()
      .toLowerCase();
    if (status !== "failed") return;

    const runId = String(payload.run_id || payload.runId || "").trim();
    const promptKey = runId.toLowerCase().startsWith("comfyui-")
      ? runId.slice("comfyui-".length)
      : null;
    const resolvedPromptKey = resolveErrorPromptKey(
      promptKey,
      comfyuiActivePromptIds.value,
    );
    const message = String(payload.message || "").trim() || "ComfyUI failed";
    markComfyuiPromptFailed(
      resolvedPromptKey,
      "plugin-progress-failed",
      message,
    );
  },
  { deep: true },
);

// ---------------------------------------------------------------------------
// Public API: maybeRefreshOverlayForComfyui
// ---------------------------------------------------------------------------

function findImageById(imageId, primary, fallback) {
  if (!imageId) return null;
  const id = String(imageId);
  const lists = [primary, fallback].filter(Array.isArray);
  for (const list of lists) {
    const found = list.find(
      (item) => item?.id != null && String(item.id) === id,
    );
    if (found) return found;
  }
  return null;
}

async function maybeRefreshOverlayForComfyui() {
  if (!props.overlayOpen || !comfyuiPendingOverlayRefresh.value) return;
  const sourceId = comfyuiSourcePictureId.value;
  if (!sourceId) {
    if (!hasComfyuiRefreshRetry(sourceId)) {
      comfyuiPendingOverlayRefresh.value = false;
    }
    return;
  }
  const source = findImageById(
    sourceId,
    props.lastFetchedGridImages,
    props.allGridImages,
  );
  let sourceStackId = props.getPictureStackId(source);
  if (!sourceStackId) {
    sourceStackId = await fetchStackIdForPicture(sourceId);
  }
  if (!sourceStackId) {
    logComfyuiDebug("overlay-refresh-missing-stack", {
      sourceId,
    });
    if (!hasComfyuiRefreshRetry(sourceId)) {
      comfyuiPendingOverlayRefresh.value = false;
    }
    return;
  }
  const overlayImage = findImageById(
    props.overlayImageId,
    props.lastFetchedGridImages,
    props.allGridImages,
  );
  const overlayStackId = props.getPictureStackId(overlayImage);
  if (
    overlayStackId &&
    overlayStackId !== sourceStackId &&
    String(props.overlayImageId || "") !== String(sourceId)
  ) {
    logComfyuiDebug("overlay-refresh-skip", {
      sourceId,
      sourceStackId,
      overlayStackId,
      overlayImageId: props.overlayImageId,
    });
    if (!hasComfyuiRefreshRetry(sourceId)) {
      comfyuiPendingOverlayRefresh.value = false;
    }
    return;
  }
  let members = Array.isArray(props.lastFetchedGridImages)
    ? props.lastFetchedGridImages.filter(
        (item) => props.getPictureStackId(item) === sourceStackId,
      )
    : [];
  if (!members.length) {
    members = await fetchStackMembersForOverlay(sourceStackId);
  }
  if (!members.length) {
    logComfyuiDebug("overlay-refresh-no-members", {
      sourceId,
      sourceStackId,
    });
    if (!hasComfyuiRefreshRetry(sourceId)) {
      comfyuiPendingOverlayRefresh.value = false;
      clearComfyuiRefreshRetries();
    }
    return;
  }
  const newest = props.selectNewestStackMember(members);
  const currentOverlayId =
    props.overlayImageId != null ? String(props.overlayImageId) : null;
  const nextOverlayId = newest?.id != null ? String(newest.id) : null;
  logComfyuiDebug("overlay-refresh-apply", {
    sourceId,
    sourceStackId,
    memberCount: members.length,
    selectedId: newest?.id ?? null,
    currentOverlayId,
  });
  if (!nextOverlayId) {
    if (!hasComfyuiRefreshRetry(sourceId)) {
      comfyuiPendingOverlayRefresh.value = false;
      clearComfyuiRefreshRetries();
    }
    return;
  }
  if (currentOverlayId && currentOverlayId === nextOverlayId) {
    if (!hasComfyuiRefreshRetry(sourceId)) {
      comfyuiPendingOverlayRefresh.value = false;
      clearComfyuiRefreshRetries();
    }
    return;
  }
  emit("update:overlayImageId", newest.id);
  comfyuiPendingOverlayRefresh.value = false;
  clearComfyuiRefreshRetries();
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onUnmounted(() => {
  stopComfyuiWatchdog();
  clearComfyuiHideTimer();
  clearComfyuiRefreshRetries();
  if (comfyuiWs) {
    comfyuiWs.close();
    comfyuiWs = null;
  }
});

// ---------------------------------------------------------------------------
// Expose
// ---------------------------------------------------------------------------

defineExpose({
  handleComfyuiRun,
  maybeRefreshOverlayForComfyui,
  clientId,
  progress,
  progressPercent,
  comfyuiPendingOverlayRefresh,
  abortComfyui,
});
</script>

<style scoped>
.comfyui-progress {
  position: absolute;
  bottom: 12px;
  right: 12px;
  z-index: 120;
  background: rgba(var(--v-theme-dark-surface), 0.75);
  color: rgb(var(--v-theme-on-dark-surface));
  padding: 8px 10px;
  border-radius: 8px;
  min-width: 180px;
  box-shadow: 0 4px 12px rgba(var(--v-theme-shadow), 0.25);
  backdrop-filter: blur(6px);
  max-width: min(85vw, 560px);
}

.comfyui-progress.comfyui-progress-error {
  background: rgba(120, 18, 18, 0.96);
  color: rgb(var(--v-theme-on-dark-surface));
  border: 1px solid rgba(255, 130, 130, 0.72);
  box-shadow: 0 10px 26px rgba(90, 10, 10, 0.45);
}

.comfyui-progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 6px;
}

.comfyui-progress-title {
  font-size: 0.8em;
  flex: 1;
  white-space: normal;
  line-height: 1.35;
  max-height: 7.2em;
  overflow: hidden;
  word-break: break-word;
}

.comfyui-abort-btn {
  flex-shrink: 0;
  background: rgba(var(--v-theme-on-dark-surface), 0.15);
  border: none;
  border-radius: 4px;
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: pointer;
  font-size: 0.75em;
  line-height: 1;
  padding: 2px 5px;
  opacity: 0.7;
  transition:
    opacity 0.15s,
    background 0.15s;
}

.comfyui-abort-btn:hover:not(:disabled) {
  opacity: 1;
  background: rgba(220, 60, 60, 0.7);
}

.comfyui-abort-btn:disabled {
  cursor: default;
  opacity: 0.4;
}

.comfyui-progress-bar {
  width: 100%;
  height: 6px;
  background: rgba(var(--v-theme-on-dark-surface), 0.2);
  border-radius: 999px;
  overflow: hidden;
}

.comfyui-progress-fill {
  height: 100%;
  background: rgb(var(--v-theme-accent));
  width: 0;
  transition: width 0.2s ease;
}

.comfyui-progress.comfyui-progress-error .comfyui-progress-bar {
  background: rgba(255, 255, 255, 0.22);
}

.comfyui-progress.comfyui-progress-error .comfyui-progress-fill {
  background: rgb(255, 128, 128);
}
</style>
