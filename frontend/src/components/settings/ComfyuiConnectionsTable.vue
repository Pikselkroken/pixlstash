<script setup>
// Every saved ComfyUI connection as a row (#2194): Run on (a radio), the
// connection, its status and card, the ComfyUI-PixlStash nodes, its own link
// key, then Test and a ⋯ menu. Each connection keeps its own key, so the radio
// only chooses where runs go and never touches a key.
//
// The row's own changes (switch, test, rename, edit address, unlink, remove)
// are made here and the new list goes up as `changed`; linking opens the Link
// dialog, which the section owns, through `link`.
//
// Overlays here are opened programmatically, so Vuetify does not hand focus
// back: after a menu action, an inline edit or a confirm, focus goes to the
// row's ⋯ button by hand (`focusMore`).
import { computed, nextTick, reactive, ref } from "vue";
import { VIcon, VMenu, VProgressCircular } from "vuetify/components";
import {
  probeComfyui,
  removeComfyuiConnection,
  unlinkComfyuiConnection,
  updateComfyuiConnection,
} from "../../api/comfyui";
import { patchUserConfig } from "../../api/config";
import { useFilterStore } from "../../stores/useFilterStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { errorMessage } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { formatUserDay } from "../../utils/utils";
import { withRef } from "../../utils/withRef.js";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import Segmented from "../widgets/Segmented.vue";

const props = defineProps({
  // `GET /comfyui/connections`' `connections` and `current`.
  connections: { type: Array, required: true },
  current: { type: String, default: null },
  // The row whose Link dialog is open: its controls wait for it.
  linking: { type: String, default: null },
});

const emit = defineEmits(["changed", "link"]);

const SCHEME_OPTIONS = [
  { id: "http", label: "http" },
  { id: "https", label: "https" },
];

const userPrefs = useUserPrefsStore();

// This session's Test answers by id, for the reason a row is not answering;
// the stored status has no reason.
const answers = reactive({});
const testing = reactive({});
const menuOpen = reactive({});
const moreButtons = {};
const radios = {};
// The row being renamed or re-addressed: `{id, kind: "name" | "address"}`.
const editing = ref(null);
const nameField = ref("");
const scheme = ref("http");
const host = ref("");
const port = ref("");
const editError = ref("");
const saving = ref(false);
// The row whose Remove is being confirmed, and why it could not be removed.
const confirmingRemove = ref(null);
const removeError = ref("");
const removing = ref(false);
// Bumped to redraw the radios after a switch that did not save.
const radioKey = ref(0);
const announcement = ref("");
const alertMessage = ref("");

function gpuLabel(gpu, vramMb) {
  if (!gpu) return "";
  const name = gpu.replace(/^NVIDIA (GeForce )?/, "");
  return vramMb ? `${name} · ${Math.round(vramMb / 1024)} GB` : name;
}

function hostPort(url) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

const rows = computed(() =>
  props.connections.map((entry) => {
    const link = entry.link || { linked: false };
    return {
      ...entry,
      link,
      isCurrent: entry.id === props.current,
      card: gpuLabel(entry.gpu, entry.vram_mb),
      detail: answers[entry.id]?.detail || null,
      linkedOn: formatUserDay(link.linked_at, userPrefs.dateFormat),
    };
  }),
);

// The connection that takes over Run on when the current one goes: the first
// one left, as the server picks it.
function successor(row) {
  return props.connections.find((c) => c.id !== row.id) || null;
}

function say(text) {
  alertMessage.value = "";
  announcement.value = text;
}

function fail(text) {
  announcement.value = "";
  alertMessage.value = text;
}

async function focusMore(id) {
  await nextTick();
  const target =
    moreButtons[id] ||
    moreButtons[props.connections[0]?.id] ||
    null;
  target?.focus();
}

// ── Run on ─────────────────────────────────────────────────────────────────

/** The same switch as the Workflows sidebar's Run on menu. */
// Bumped on every switch: only the newest one's answer is acted on, so two
// quick picks cannot leave the sidebar on the one that answered last.
let switchRun = 0;

async function switchTo(row) {
  if (row.isCurrent) return;
  const run = ++switchRun;
  const before = rows.value.find((r) => r.isCurrent);
  try {
    const body = await patchUserConfig({ comfyui_url: row.url });
    if (run !== switchRun) return;
    useFilterStore().comfyuiUrl = body?.config?.comfyui_url || row.url;
    emit("changed", {
      connections: props.connections,
      current: row.id,
    });
    say(`Run on: ${row.name}`);
  } catch (e) {
    console.warn(`[settings] could not switch ComfyUI to ${row.url}`, e);
    if (run !== switchRun) return;
    radioKey.value += 1;
    fail(
      `Could not switch Run on to ${row.name}: ${errorMessage(e, "the server did not answer.")}` +
        (before ? ` ${before.name} is still current.` : ""),
    );
    // The remount took the focused radio with it; keep focus in the group.
    await nextTick();
    radios[before?.id ?? row.id]?.focus();
  }
}

// ── Test ───────────────────────────────────────────────────────────────────

async function test(row) {
  testing[row.id] = true;
  try {
    answers[row.id] = await probeComfyui(row.url);
    const got = answers[row.id];
    say(
      got.reachable
        ? [`${row.name} connected`, gpuLabel(got.gpu, got.vram_mb)]
            .filter(Boolean)
            .join(", ")
        : `${row.name} is not answering`,
    );
    // The probe noted its answer, status and nodes, on the connection.
    emit("changed", null);
  } catch (e) {
    console.warn(`[settings] could not test the ComfyUI at ${row.url}`, e);
    fail(errorMessage(e, `Could not test ${row.name}.`));
  } finally {
    testing[row.id] = false;
  }
}

// ── Rename and edit address, in place ──────────────────────────────────────

function startRename(row) {
  menuOpen[row.id] = false;
  editError.value = "";
  nameField.value = row.name;
  editing.value = { id: row.id, kind: "name" };
}

function startEditAddress(row) {
  menuOpen[row.id] = false;
  editError.value = "";
  try {
    const parsed = new URL(row.url);
    const https = parsed.protocol === "https:";
    scheme.value = https ? "https" : "http";
    // URL keeps an IPv6 host in brackets; the field takes it bare.
    host.value = parsed.hostname.replace(/^\[|\]$/g, "");
    port.value = parsed.port || (https ? "443" : "80");
  } catch {
    scheme.value = "http";
    host.value = "";
    port.value = "8188";
  }
  editing.value = { id: row.id, kind: "address" };
}

// Leaving the field cancels too, without taking focus from where it went.
function cancelEdit(refocus = true) {
  const id = editing.value?.id;
  editing.value = null;
  editError.value = "";
  if (id && refocus) focusMore(id);
}

async function saveName(row) {
  // Enter in a form's only field also submits it: one save, not two.
  if (saving.value) return;
  const name = nameField.value.trim();
  if (!name) {
    editError.value = "A name cannot be empty.";
    return;
  }
  if (name === row.name) {
    cancelEdit();
    return;
  }
  saving.value = true;
  try {
    const body = await updateComfyuiConnection(row.id, { name });
    editing.value = null;
    emit("changed", body);
    say(`Renamed to ${name}`);
    focusMore(row.id);
  } catch (e) {
    console.warn(`[settings] could not rename ComfyUI connection ${row.id}`, e);
    editError.value = errorMessage(e, "Could not rename it.");
  } finally {
    saving.value = false;
  }
}

// An address is saved only once a ComfyUI has answered there, as on Connect.
async function saveAddress(row) {
  if (saving.value) return;
  editError.value = "";
  const h = host.value.trim().replace(/^\[(.*)\]$/, "$1");
  const p = Number(String(port.value).trim());
  if (!h) {
    editError.value = "Enter the host ComfyUI runs on.";
    return;
  }
  if (!Number.isInteger(p) || p < 1 || p > 65535) {
    editError.value = "Port must be between 1 and 65535.";
    return;
  }
  const hostPart = h.includes(":") ? `[${h}]` : h;
  saving.value = true;
  try {
    const probe = await probeComfyui(`${scheme.value}://${hostPart}:${p}/`);
    if (!probe?.reachable) {
      editError.value = probe?.detail || `Nothing answered at ${hostPart}:${p}.`;
      return;
    }
    // As the server matches addresses: case and a trailing slash aside.
    const urlKey = (url) => url.replace(/\/+$/, "").toLowerCase();
    if (urlKey(probe.url) === urlKey(row.url)) {
      cancelEdit();
      return;
    }
    const body = await updateComfyuiConnection(row.id, { url: probe.url });
    editing.value = null;
    answers[row.id] = probe;
    if (row.isCurrent) useFilterStore().comfyuiUrl = probe.url;
    emit("changed", body);
    // Its key was bound to the old address and the server revoked it: link
    // it at the new one straight away.
    const after = body.connections.find((c) => c.id === row.id);
    if (row.link.linked && !after?.link?.linked) {
      // The server's row: a name made from the old address has followed it.
      emit("link", after || { ...row, url: probe.url });
    } else {
      say(`${row.name} moved to ${hostPart}:${p}`);
      focusMore(row.id);
    }
  } catch (e) {
    console.warn(`[settings] could not move ComfyUI connection ${row.id}`, e);
    editError.value = errorMessage(e, "Could not save that address.");
  } finally {
    saving.value = false;
  }
}

// ── Link, unlink, remove ───────────────────────────────────────────────────

function link(row) {
  menuOpen[row.id] = false;
  emit("link", row);
}

async function unlink(row) {
  menuOpen[row.id] = false;
  try {
    await unlinkComfyuiConnection(row.id);
    emit("changed", null);
    say(`Unlinked ${row.name}. Link again to give it a new key.`);
  } catch (e) {
    console.warn(`[settings] could not unlink ComfyUI connection ${row.id}`, e);
    fail(errorMessage(e, `Could not unlink ${row.name}.`));
  }
  focusMore(row.id);
}

/** Removing costs something only when runs go there or it holds a key. */
function askRemove(row) {
  menuOpen[row.id] = false;
  removeError.value = "";
  if (row.isCurrent || row.link.linked) {
    confirmingRemove.value = row.id;
  } else {
    remove(row);
  }
}

function cancelRemove(row) {
  // The request cannot be called back once sent: the row would go anyway.
  if (removing.value) return;
  confirmingRemove.value = null;
  removeError.value = "";
  focusMore(row.id);
}

async function remove(row) {
  const index = props.connections.findIndex((c) => c.id === row.id);
  removing.value = true;
  try {
    const body = await removeComfyuiConnection(row.id);
    confirmingRemove.value = null;
    delete moreButtons[row.id];
    const next = body.connections.find((c) => c.id === body.current);
    if (row.isCurrent) useFilterStore().comfyuiUrl = next?.url || "";
    emit("changed", body);
    say(
      `Removed ${row.name}.` + (row.isCurrent && next ? ` Run on is now ${next.name}.` : ""),
    );
    const neighbour =
      body.connections[Math.min(index, body.connections.length - 1)];
    if (neighbour) focusMore(neighbour.id);
  } catch (e) {
    console.warn(`[settings] could not remove ComfyUI connection ${row.id}`, e);
    // The key could not be revoked, so the row stays.
    const why = errorMessage(e, `Could not remove ${row.name}.`);
    if (confirmingRemove.value === row.id) removeError.value = why;
    else fail(why);
  } finally {
    removing.value = false;
  }
}

function onRowKeydown(event, row) {
  if (event.key === "F2" && !editing.value) {
    event.preventDefault();
    startRename(row);
  }
}

defineExpose({ focusMore });

function onMenuKey(event, row) {
  if (event.key === "Escape") {
    menuOpen[row.id] = false;
    focusMore(row.id);
    return;
  }
  onMenuKeydown(event);
}
</script>

<template>
  <div class="cct">
    <div class="cct-wrap">
      <table class="cct-table">
        <thead>
          <tr>
            <th class="cct-rad">Run on</th>
            <th>Connection</th>
            <th>Status</th>
            <th>Nodes</th>
            <th>Link</th>
            <th class="cct-actions"><span class="visually-hidden">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in rows" :key="row.id">
            <tr
              class="cct-row"
              :class="{ 'cct-row--parent': row.status === 'offline' || row.link.refused_from || (row.link.linked && !row.link.bound_address) || confirmingRemove === row.id }"
              :data-testid="`comfyui-connection-${row.id}`"
              @keydown="onRowKeydown($event, row)"
            >
              <td class="cct-rad">
                <input
                  :key="radioKey"
                  :ref="(el) => (radios[row.id] = el)"
                  type="radio"
                  name="comfyui-run-on"
                  class="cct-radio"
                  :checked="row.isCurrent"
                  :aria-label="`Run on ${row.name}`"
                  :disabled="linking === row.id"
                  :data-testid="`comfyui-run-on-${row.id}`"
                  @change="switchTo(row)"
                />
              </td>
              <td class="cct-conn">
                <form
                  v-if="editing?.id === row.id && editing.kind === 'name'"
                  class="cct-edit"
                  @submit.prevent="saveName(row)"
                  @keydown.esc.stop.prevent="cancelEdit"
                >
                  <AppInput
                    v-model="nameField"
                    :aria-label="`Name of ${row.name}`"
                    autofocus
                    autoselect
                    :error="!!editError"
                    :disabled="saving"
                    data-testid="comfyui-rename-field"
                    @enter="saveName(row)"
                    @blur="!saving && cancelEdit(false)"
                  />
                </form>
                <template v-else>
                  <span class="cct-name">{{ row.name }}</span>
                  <span class="cct-url">{{ row.url }}</span>
                </template>
              </td>
              <td>
                <span v-if="testing[row.id]" class="cct-st">
                  <v-progress-circular indeterminate size="14" width="2" />
                  Testing
                </span>
                <span
                  v-else-if="row.status === 'connected'"
                  class="cct-st cct-st--ok"
                  ><v-icon size="16" aria-hidden="true">mdi-check-circle</v-icon
                  >Connected</span
                >
                <span
                  v-else-if="row.status === 'offline'"
                  class="cct-st cct-st--bad"
                  ><v-icon size="16" aria-hidden="true"
                    >mdi-alert-rhombus-outline</v-icon
                  >Not answering</span
                >
                <span v-else class="cct-st cct-muted">Not checked</span>
                <span v-if="row.status === 'connected' && row.card" class="cct-sub">{{
                  row.card
                }}</span>
              </td>
              <td>
                <span v-if="row.nodes === true" class="cct-st cct-st--ok"
                  ><v-icon size="16" aria-hidden="true">mdi-check-circle</v-icon
                  >Installed</span
                >
                <span v-else-if="row.nodes === false" class="cct-st cct-st--warn"
                  ><v-icon size="16" aria-hidden="true">mdi-alert-outline</v-icon
                  >Missing</span
                >
                <span v-else class="cct-muted" aria-label="Not known">—</span>
              </td>
              <td>
                <template v-if="row.link.linked">
                  <span class="cct-keyed" :data-testid="`comfyui-linked-${row.id}`"
                    ><v-icon size="16" aria-hidden="true">mdi-key-variant</v-icon
                    >Linked</span
                  >
                  <span class="cct-sub">{{ row.linkedOn }}</span>
                </template>
                <AppButton
                  v-else
                  variant="outline"
                  size="sm"
                  icon-left="mdi-key-variant"
                  :disabled="linking === row.id"
                  :data-testid="`comfyui-link-${row.id}`"
                  @click="link(row)"
                >
                  Link
                </AppButton>
              </td>
              <td class="cct-actions">
                <AppButton
                  variant="ghost"
                  size="sm"
                  icon-only
                  icon-left="mdi-refresh"
                  :tooltip="`Test ${row.name}`"
                  :loading="testing[row.id]"
                  :disabled="linking === row.id"
                  :data-testid="`comfyui-test-${row.id}`"
                  @click="test(row)"
                />
                <v-menu v-model="menuOpen[row.id]" location="bottom end" :offset="4">
                  <template #activator="{ props: menuProps }">
                    <AppButton
                      v-bind="withRef(menuProps, (el) => (moreButtons[row.id] = el))"
                      variant="ghost"
                      size="sm"
                      icon-only
                      icon-left="mdi-dots-horizontal"
                      :tooltip="`More for ${row.name}`"
                      :disabled="linking === row.id"
                      aria-haspopup="menu"
                      :data-testid="`comfyui-more-${row.id}`"
                    />
                  </template>
                  <div
                    class="ctx-menu"
                    role="menu"
                    :aria-label="`More for ${row.name}`"
                    tabindex="-1"
                    :data-testid="`comfyui-menu-${row.id}`"
                    @keydown="onMenuKey($event, row)"
                  >
                    <button
                      type="button"
                      class="ctx-item"
                      role="menuitem"
                      data-testid="comfyui-menu-rename"
                      @click="startRename(row)"
                    >
                      <v-icon class="ctx-icon" aria-hidden="true">mdi-pencil-outline</v-icon>
                      <span class="ctx-label-text">Rename</span>
                      <span class="ctx-shortcut">F2</span>
                    </button>
                    <button
                      type="button"
                      class="ctx-item"
                      role="menuitem"
                      data-testid="comfyui-menu-edit-address"
                      @click="startEditAddress(row)"
                    >
                      <v-icon class="ctx-icon" aria-hidden="true">mdi-web</v-icon>
                      <span class="ctx-label-text">Edit address…</span>
                    </button>
                    <template v-if="row.link.linked">
                      <div class="ctx-sep" role="separator"></div>
                      <button
                        type="button"
                        class="ctx-item"
                        role="menuitem"
                        data-testid="comfyui-menu-relink"
                        @click="link(row)"
                      >
                        <v-icon class="ctx-icon" aria-hidden="true">mdi-key-variant</v-icon>
                        <span class="ctx-label-text">Link again</span>
                      </button>
                      <button
                        type="button"
                        class="ctx-item"
                        role="menuitem"
                        data-testid="comfyui-menu-unlink"
                        @click="unlink(row)"
                      >
                        <v-icon class="ctx-icon" aria-hidden="true">mdi-key-remove</v-icon>
                        <span class="ctx-label-text">Unlink</span>
                      </button>
                    </template>
                    <div class="ctx-sep" role="separator"></div>
                    <button
                      type="button"
                      class="ctx-item ctx-item--danger"
                      role="menuitem"
                      data-testid="comfyui-menu-remove"
                      @click="askRemove(row)"
                    >
                      <v-icon class="ctx-icon" aria-hidden="true">mdi-delete-outline</v-icon>
                      <span class="ctx-label-text">Remove…</span>
                    </button>
                  </div>
                </v-menu>
              </td>
            </tr>

            <!-- Edit address: today's form, in place under the row. -->
            <tr
              v-if="editing?.id === row.id && editing.kind === 'address'"
              class="cct-note"
            >
              <td></td>
              <td colspan="5">
                <form
                  class="cct-address"
                  data-testid="comfyui-edit-address"
                  @submit.prevent="saveAddress(row)"
                  @keydown.esc.stop.prevent="cancelEdit"
                >
                  <div class="cct-fields">
                    <Segmented v-model="scheme" :options="SCHEME_OPTIONS" aria-label="Scheme" />
                    <AppInput
                      v-model="host"
                      :aria-label="`Host of ${row.name}`"
                      placeholder="e.g. 127.0.0.1"
                      mono
                      autofocus
                      :error="!!editError"
                      :disabled="saving"
                      data-testid="comfyui-edit-host"
                      @enter="saveAddress(row)"
                    />
                    <AppInput
                      v-model="port"
                      :aria-label="`Port of ${row.name}`"
                      placeholder="8188"
                      mono
                      :error="!!editError"
                      :disabled="saving"
                      data-testid="comfyui-edit-port"
                      @enter="saveAddress(row)"
                    />
                  </div>
                  <p v-if="row.link.linked" class="cct-hint" data-testid="comfyui-edit-relink-note">
                    Its key works only from the old address, so saving revokes it
                    and links {{ row.name }} again at the new one.
                  </p>
                  <p
                    v-if="editError"
                    class="cct-error"
                    role="alert"
                    data-testid="comfyui-edit-error"
                  >
                    {{ editError }}
                  </p>
                  <div class="cct-buttons">
                    <AppButton variant="secondary" :disabled="saving" @click="cancelEdit">
                      Cancel
                    </AppButton>
                    <AppButton
                      variant="primary"
                      :loading="saving"
                      data-testid="comfyui-edit-save"
                      @click="saveAddress(row)"
                    >
                      Save
                    </AppButton>
                  </div>
                </form>
              </td>
            </tr>
            <tr
              v-else-if="editing?.id === row.id && editError"
              class="cct-note"
            >
              <td></td>
              <td colspan="5">
                <p class="cct-error" role="alert" data-testid="comfyui-edit-error">
                  {{ editError }}
                </p>
              </td>
            </tr>

            <!-- Remove, confirmed only when it costs something. -->
            <tr v-if="confirmingRemove === row.id" class="cct-note">
              <td></td>
              <td colspan="5">
                <div class="cct-confirm" data-testid="comfyui-remove-confirm">
                  <p class="cct-confirm__title">Remove {{ row.name }}?</p>
                  <p class="cct-small">
                    <template v-if="row.isCurrent && row.link.linked">
                      Workflows run on it and it is linked.
                    </template>
                    <template v-else-if="row.isCurrent">Workflows run on it.</template>
                    <template v-else>It is linked.</template>
                    <template v-if="row.link.linked">
                      Its key is revoked first, in ComfyUI and here.
                    </template>
                    <template v-if="row.isCurrent && successor(row)">
                      {{ successor(row).name }} takes over Run on<template
                        v-if="successor(row).link?.linked"
                      >
                        and keeps its own key</template
                      >.
                    </template>
                    <template v-else-if="row.isCurrent">
                      No ComfyUI is left to run workflows on.
                    </template>
                  </p>
                  <p v-if="removeError" class="cct-error" role="alert">
                    {{ removeError }}
                  </p>
                  <div class="cct-buttons">
                    <AppButton
                      variant="secondary"
                      autofocus
                      :disabled="removing"
                      data-testid="comfyui-remove-cancel"
                      @click="cancelRemove(row)"
                      @keydown.esc.prevent="cancelRemove(row)"
                    >
                      Cancel
                    </AppButton>
                    <AppButton
                      variant="danger"
                      icon-left="mdi-delete-outline"
                      :loading="removing"
                      data-testid="comfyui-remove-confirm-button"
                      @click="remove(row)"
                    >
                      Remove
                    </AppButton>
                  </div>
                </div>
              </td>
            </tr>

            <!-- Problems, under the row they belong to. -->
            <tr
              v-else-if="row.status === 'offline'"
              class="cct-note"
              :data-testid="`comfyui-note-offline-${row.id}`"
            >
              <td></td>
              <td colspan="5">
                <p class="cct-problem cct-problem--bad">
                  {{ row.detail || `Nothing answered at ${hostPort(row.url)}.` }}
                  If ComfyUI is running there, start it with
                  <code>--listen</code>, or edit the address.
                  <template v-if="row.link.linked">Its key stays linked.</template>
                </p>
              </td>
            </tr>
            <tr
              v-else-if="row.link.refused_from"
              class="cct-note"
              :data-testid="`comfyui-note-refused-${row.id}`"
            >
              <td></td>
              <td colspan="5">
                <p class="cct-problem cct-problem--bad">
                  PixlStash refused {{ row.name }}'s key from
                  {{ row.link.refused_from }}. It only works from
                  {{ row.link.bound_address }}, where ComfyUI was when it was
                  linked. If its address changed, link it again. If
                  {{ row.link.refused_from }} is not that computer, someone else
                  has the key: linking again replaces it.
                </p>
              </td>
            </tr>
            <tr
              v-else-if="row.link.linked && !row.link.bound_address"
              class="cct-note"
              :data-testid="`comfyui-note-unbound-${row.id}`"
            >
              <td></td>
              <td colspan="5">
                <p class="cct-problem">
                  This link was made before PixlStash tied ComfyUI's key to its
                  address, so the key works from any computer. Link again to tie
                  it.
                </p>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <div role="status" class="visually-hidden" data-testid="comfyui-table-status">
      {{ announcement }}
    </div>
    <p
      v-if="alertMessage"
      class="cct-error"
      role="alert"
      data-testid="comfyui-table-error"
    >
      <v-icon size="16" aria-hidden="true">mdi-alert-rhombus-outline</v-icon>
      <span>{{ alertMessage }}</span>
    </p>
  </div>
</template>

<style scoped>
.cct {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.cct-wrap {
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  overflow-x: auto;
}

.cct-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

.cct-table th {
  text-align: left;
  padding: var(--space-3);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  white-space: nowrap;
}

.cct-table td {
  padding: var(--space-3);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  vertical-align: middle;
}

.cct-table tbody tr:last-child td {
  border-bottom: none;
}

/* A row with a note under it shares its rule with the note. */
.cct-row--parent td {
  border-bottom: none;
}

.cct-note td {
  padding-top: 0;
}

.cct-rad {
  width: 56px;
}

.cct-radio {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: rgb(var(--v-theme-selected-ink));
  cursor: pointer;
}

.cct-conn {
  min-width: 0;
  max-width: 220px;
}

.cct-name,
.cct-url {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cct-name {
  font-weight: var(--weight-medium);
}

.cct-url {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cct-st,
.cct-keyed {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
}

.cct-keyed {
  font-weight: var(--weight-medium);
}

.cct-st--ok :deep(.v-icon) {
  color: rgb(var(--v-theme-surface-success));
}

.cct-st--bad :deep(.v-icon) {
  color: rgb(var(--v-theme-surface-error));
}

.cct-st--warn :deep(.v-icon) {
  color: rgb(var(--v-theme-surface-warning));
}

.cct-sub {
  display: block;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  white-space: nowrap;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cct-muted {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cct-actions {
  text-align: right;
  white-space: nowrap;
}

.cct-problem {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-left: 3px solid rgb(var(--v-theme-surface-warning));
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.cct-problem--bad {
  border-left-color: rgb(var(--v-theme-surface-error));
}

.cct-problem code,
.cct-hint code {
  font-family: var(--font-mono);
}

.cct-edit {
  max-width: 220px;
}

.cct-address,
.cct-confirm {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--hover-wash);
  border-radius: var(--radius-sm);
}

/* Scheme | host | port, as on Connect. */
.cct-fields {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) 96px;
  gap: var(--space-4);
  align-items: end;
}

.cct-confirm__title {
  margin: 0;
  font-weight: var(--weight-semibold);
}

.cct-small,
.cct-hint {
  margin: 0;
  font-size: var(--text-sm);
}

.cct-hint {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cct-buttons {
  display: flex;
  gap: var(--space-3);
}

.cct-error {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-error));
}
</style>
