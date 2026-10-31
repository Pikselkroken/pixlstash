<script setup>
import { computed, reactive, ref, watch } from "vue";
import { VSwitch } from "vuetify/components";
import { isReadOnly } from "../../utils/apiClient";
import {
  getAuthState,
  changePassword,
  listTokens,
  createToken,
  patchToken,
  deleteToken,
  setTokenLibraries,
} from "../../api/users";
import { listPictureSets } from "../../api/pictureSets";
import { listProjects } from "../../api/projects";
import { listCharacters } from "../../api/characters";
import { copyText } from "../../utils/clipboard";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import Tooltip from "../widgets/Tooltip.vue";
import SettingsSection from "./SettingsSection.vue";
import ConnectAgentDialog from "./ConnectAgentDialog.vue";
import { errorDetail } from "../../utils/apiError";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const prefs = useUserPrefsStore();

// --- Account auth state ---
const settingsUsername = ref("");
const settingsHasPassword = ref(false);
const settingsLoading = ref(false);
const settingsError = ref("");
const settingsSuccess = ref("");
const currentPassword = ref("");
const newPassword = ref("");
const showNewPassword = ref(false);

// --- Token state ---
const tokensLoading = ref(false);
const tokensError = ref("");
const tokens = ref([]);
const tokenDescription = ref("");
const newlyCreatedToken = ref("");
const tokenCopied = ref(false);
const tokenDialogOpen = ref(false);
const tokenDeleteDialogOpen = ref(false);
// Presentational-only: visibility of the "New API token" create form dialog.
// The create-token logic (createUserToken) is unchanged; this ref just controls
// whether the form is shown in a dialog instead of inline.
const createTokenDialogOpen = ref(false);
const connectAgentDialogOpen = ref(false);
const tokenToDelete = ref(null);
const tokenScope = ref("ALL");
const tokenResourceType = ref(null);
const tokenResourceId = ref(null);
const tokenExpiresAt = ref(null);
const tokenIncludeAttachments = ref(false);
const tokenWatermark = ref(false);
const shareResourceOptions = ref([]);
const shareResourceLoading = ref(false);
const shareLinkCopied = ref(false);


function resetForm() {
  settingsError.value = "";
  settingsSuccess.value = "";
  currentPassword.value = "";
  newPassword.value = "";
  showNewPassword.value = false;
  tokensError.value = "";
  tokenDescription.value = "";
  newlyCreatedToken.value = "";
  tokenDialogOpen.value = false;
  tokenDeleteDialogOpen.value = false;
  createTokenDialogOpen.value = false;
  connectAgentDialogOpen.value = false;
  tokenToDelete.value = null;
  tokenScope.value = "ALL";
  tokenResourceType.value = null;
  tokenResourceId.value = null;
  tokenExpiresAt.value = null;
  tokenIncludeAttachments.value = false;
  tokenWatermark.value = false;
  shareResourceOptions.value = [];
  shareLinkCopied.value = false;
}

async function fetchSettingsAuth() {
  settingsLoading.value = true;
  settingsError.value = "";
  try {
    const auth = await getAuthState();
    settingsUsername.value = auth?.username || "";
    settingsHasPassword.value = Boolean(auth?.has_password);
  } catch {
    settingsError.value = "Failed to load account settings.";
  } finally {
    settingsLoading.value = false;
  }
}

async function fetchUserTokens() {
  tokensLoading.value = true;
  tokensError.value = "";
  try {
    const rows = await listTokens();
    tokens.value = Array.isArray(rows) ? rows : [];
  } catch {
    tokensError.value = "Failed to load tokens.";
  } finally {
    tokensLoading.value = false;
  }
}

function formatTokenTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  // Date-only keeps the table columns compact (a full locale datetime is far
  // too wide for the dense token table).
  return date.toLocaleDateString();
}

function isTokenExpired(token) {
  if (!token.expires_at) return false;
  return new Date(token.expires_at) < new Date();
}

function formatTokenExpiry(token) {
  if (!token.expires_at) return "Never";
  const date = new Date(token.expires_at);
  if (Number.isNaN(date.getTime())) return "Never";
  if (date < new Date()) return "Expired";
  return date.toLocaleDateString();
}

async function copyToken() {
  const text = newlyCreatedToken.value;
  if (!text) return;
  if (await copyText(text)) {
    tokenCopied.value = true;
    setTimeout(() => {
      tokenCopied.value = false;
    }, 2000);
  }
}

async function loadShareResourceOptions(type) {
  if (!type) {
    shareResourceOptions.value = [];
    return;
  }
  shareResourceLoading.value = true;
  try {
    let items = [];
    if (type === "picture_set") {
      const rows = await listPictureSets();
      items = (rows || []).map((s) => ({ id: s.id, label: s.name }));
    } else if (type === "character") {
      const chars = await listCharacters();
      items = (chars || []).map((c) => ({ id: c.id, label: c.name }));
    } else if (type === "project") {
      const rows = await listProjects();
      items = (rows || []).map((p) => ({ id: p.id, label: p.name }));
    }
    shareResourceOptions.value = items;
  } catch {
    shareResourceOptions.value = [];
  } finally {
    shareResourceLoading.value = false;
  }
}

const shareUrl = computed(() => {
  if (!newlyCreatedToken.value || tokenScope.value !== "READ") return null;
  // Prefer the configured public URL (Settings › Privacy & sharing) so the
  // link works from outside the LAN.
  // Trimmed here too: the store holds whatever the config file says, and a
  // trailing slash there would double the one the path starts with.
  const origin =
    prefs.publicUrl?.trim().replace(/\/$/, "") || window.location.origin;
  const base = origin + window.location.pathname;
  return `${base}?token=${newlyCreatedToken.value}`;
});

async function copyShareLink() {
  if (!shareUrl.value) return;
  if (await copyText(shareUrl.value)) {
    shareLinkCopied.value = true;
    setTimeout(() => {
      shareLinkCopied.value = false;
    }, 2000);
  }
}

async function createUserToken() {
  tokensError.value = "";
  if (tokenExpiresAt.value) {
    const chosen = new Date(tokenExpiresAt.value);
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (chosen < today) {
      tokensError.value = "Expiry date must be in the future.";
      return;
    }
    const maxDate = new Date();
    maxDate.setFullYear(maxDate.getFullYear() + 1);
    if (chosen > maxDate) {
      tokensError.value = "Expiry date cannot be more than 1 year from now.";
      return;
    }
  }
  const description = tokenDescription.value.trim() || null;
  tokensLoading.value = true;
  try {
    const created = await createToken({
      description,
      scope: tokenScope.value,
      resource_type:
        tokenScope.value === "READ" ? tokenResourceType.value : null,
      resource_id: tokenScope.value === "READ" ? tokenResourceId.value : null,
      expires_at: tokenExpiresAt.value || null,
      include_attachments:
        tokenScope.value === "READ" && tokenResourceType.value === "project"
          ? tokenIncludeAttachments.value
          : false,
      watermark: tokenWatermark.value,
    });
    newlyCreatedToken.value = created?.token || "";
    tokenDialogOpen.value = Boolean(newlyCreatedToken.value);
    if (newlyCreatedToken.value) createTokenDialogOpen.value = false;
    tokenDescription.value = "";
    tokenWatermark.value = false;
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = errorDetail(e) || "Failed to create token.";
  } finally {
    tokensLoading.value = false;
  }
}

watch(tokenScope, (scope) => {
  if (scope !== "READ") tokenWatermark.value = false;
});

const watermarkUpdating = reactive(new Set());

async function updateTokenWatermark(token, value) {
  if (watermarkUpdating.has(token.id)) return;

  const previousValue = token.watermark;
  token.watermark = value;
  watermarkUpdating.add(token.id);
  try {
    await patchToken(token.id, { watermark: value });
  } catch (e) {
    tokensError.value = errorDetail(e) || "Failed to update token.";
    token.watermark = previousValue;
  } finally {
    watermarkUpdating.delete(token.id);
  }
}

// A token works in the library it was made in. With one library that is not
// worth a column; with several, the owner on the local network may let a token work
// in whichever is open (`can_manage`, the answer the Libraries tab is given).
const librariesStore = useLibrariesStore();
const severalLibraries = computed(() => librariesStore.libraries.length > 1);
const librariesUpdating = reactive(new Set());

// Why a row's switch cannot be used, said on hover: a greyed-out switch with
// no reason reads as broken.
function librariesLockedReason(token) {
  if (token.resource_type)
    return "A share link stays with the library it was made in";
  // A link key from before the ComfyUI scope: full access, so not widened.
  if (token.source_bound && token.scope !== "COMFYUI")
    return "Link ComfyUI again to change which libraries its key works in";
  if (!librariesStore.canManage)
    return "Sign in from the local network to change this";
  return "";
}

async function updateTokenLibraries(token, value) {
  if (librariesUpdating.has(token.id)) return;

  const previousValue = token.all_libraries;
  token.all_libraries = value;
  librariesUpdating.add(token.id);
  try {
    await setTokenLibraries(token.id, value);
  } catch (e) {
    tokensError.value = errorDetail(e) || "Failed to update token.";
    token.all_libraries = previousValue;
  } finally {
    librariesUpdating.delete(token.id);
  }
}

function confirmDeleteToken(token) {
  tokenToDelete.value = token;
  tokenDeleteDialogOpen.value = true;
}

async function deleteUserToken() {
  if (!tokenToDelete.value) {
    tokenDeleteDialogOpen.value = false;
    return;
  }
  tokensLoading.value = true;
  tokensError.value = "";
  try {
    await deleteToken(tokenToDelete.value.id);
    tokenDeleteDialogOpen.value = false;
    tokenToDelete.value = null;
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = errorDetail(e) || "Failed to delete token.";
  } finally {
    tokensLoading.value = false;
  }
}

async function submitPasswordChange() {
  settingsError.value = "";
  settingsSuccess.value = "";
  if (!newPassword.value || newPassword.value.trim().length < 8) {
    settingsError.value = "New password must be at least 8 characters long.";
    return;
  }
  if (settingsHasPassword.value && !currentPassword.value) {
    settingsError.value = "Current password is required.";
    return;
  }
  settingsLoading.value = true;
  try {
    const newPasswordValue = newPassword.value.trim();
    await changePassword({
      current_password: currentPassword.value || null,
      new_password: newPasswordValue,
    });
    settingsSuccess.value = "Password updated.";
    currentPassword.value = "";
    newPassword.value = "";
    settingsHasPassword.value = true;
    if (
      typeof window !== "undefined" &&
      "credentials" in navigator &&
      "PasswordCredential" in window &&
      settingsUsername.value &&
      newPasswordValue
    ) {
      try {
        const credential = new PasswordCredential({
          id: settingsUsername.value,
          name: settingsUsername.value,
          password: newPasswordValue,
        });
        await navigator.credentials.store(credential);
      } catch {
        // Storing credentials is best-effort; ignore failures.
      }
    }
  } catch (e) {
    settingsError.value = errorDetail(e) || "Failed to update password.";
  } finally {
    settingsLoading.value = false;
  }
}

watch(tokenResourceType, (type) => {
  tokenResourceId.value = null;
  loadShareResourceOptions(type);
});

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      resetForm();
      if (!isReadOnly.value) {
        fetchSettingsAuth();
        fetchUserTokens();
      }
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="account-pane">
    <!-- ── Account ───────────────────────────────────────────────────── -->
    <SettingsSection title="Account" first>
      <div class="account-meta">
        <span class="account-meta__label">Username</span>
        <span class="account-meta__value">
          {{ settingsUsername || "Not set" }}
        </span>
      </div>
      <!-- Hidden username field helps password managers associate the change. -->
      <input
        v-if="settingsUsername"
        type="text"
        name="username"
        :value="settingsUsername"
        autocomplete="username"
        class="account-hidden-username"
        tabindex="-1"
      />
      <div class="account-password-grid">
        <AppInput
          v-if="settingsHasPassword"
          v-model="currentPassword"
          label="Current password"
          type="password"
          :disabled="settingsLoading"
        />
        <AppInput
          v-model="newPassword"
          label="New password"
          :type="showNewPassword ? 'text' : 'password'"
          :disabled="settingsLoading"
          @enter="submitPasswordChange"
        />
        <AppButton
          variant="primary"
          :disabled="settingsLoading"
          @click="submitPasswordChange"
        >
          Update
        </AppButton>
      </div>
      <div v-if="settingsError" class="account-error">
        {{ settingsError }}
      </div>
      <div v-if="settingsSuccess" class="account-success">
        {{ settingsSuccess }}
      </div>
    </SettingsSection>

    <!-- ── API Tokens ────────────────────────────────────────────────── -->
    <SettingsSection title="API Tokens" class="account-tokens-section">
      <template #action>
        <AppButton
          variant="secondary"
          size="sm"
          icon-left="robot-outline"
          :disabled="tokensLoading"
          @click="connectAgentDialogOpen = true"
        >
          Connect AI agent
        </AppButton>
        <AppButton
          variant="primary"
          size="sm"
          icon-left="plus"
          :disabled="tokensLoading"
          @click="createTokenDialogOpen = true"
        >
          New token
        </AppButton>
      </template>

      <div v-if="tokensError" class="account-error account-tokens-error">
        {{ tokensError }}
      </div>

      <div v-if="tokens.length" class="account-token-table-wrap">
        <table class="account-token-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Scope</th>
              <th>Created</th>
              <th>Used</th>
              <th>Expires</th>
              <th v-if="severalLibraries" class="account-token-th-wm">
                Every library
              </th>
              <th class="account-token-th-wm">Mark</th>
              <th class="account-token-th-actions"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="token in tokens" :key="token.id">
              <td class="account-token-name">
                {{ token.description || "Token" }}
              </td>
              <td>
                <span
                  v-if="token.scope"
                  class="account-token-pill"
                  :class="{
                    'account-token-pill--read': token.scope !== 'ALL',
                  }"
                >
                  <template v-if="token.scope === 'ALL'">
                    <v-icon size="11">mdi-shield-account-outline</v-icon>
                    Full access
                  </template>
                  <template v-else-if="token.resource_type === 'project'">
                    <v-icon size="11">mdi-folder-outline</v-icon>
                    {{ token.resource_name ?? `Project #${token.resource_id}` }}
                  </template>
                  <template v-else-if="token.resource_type === 'character'">
                    <v-icon size="11">mdi-account-outline</v-icon>
                    {{
                      token.resource_name ?? `Character #${token.resource_id}`
                    }}
                  </template>
                  <template v-else-if="token.resource_type === 'picture_set'">
                    <v-icon size="11">mdi-image-multiple-outline</v-icon>
                    {{ token.resource_name ?? `Set #${token.resource_id}` }}
                  </template>
                  <template v-else-if="token.scope === 'COMFYUI'"
                    >ComfyUI nodes</template
                  >
                  <template v-else>Read-only</template>
                </span>
              </td>
              <td class="account-token-sub">
                {{ formatTokenTimestamp(token.created_at) }}
              </td>
              <td class="account-token-sub">
                {{ formatTokenTimestamp(token.last_used_at) }}
              </td>
              <td
                class="account-token-sub"
                :class="{ 'account-token-expired': isTokenExpired(token) }"
              >
                {{ formatTokenExpiry(token) }}
              </td>
              <td v-if="severalLibraries" class="account-token-wm">
                <!-- Off: the token works only in the library it was made in.
                     A share link stays off for good, because its set,
                     character or project is a different one in every other
                     library. -->
                <Tooltip
                  :text="librariesLockedReason(token)"
                  activator="parent"
                />
                <v-switch
                  :model-value="token.all_libraries"
                  color="primary"
                  density="compact"
                  hide-details
                  class="account-token-wm-switch"
                  :aria-label="`${token.description || 'Token'} works in every library`"
                  :disabled="
                    tokensLoading ||
                    librariesUpdating.has(token.id) ||
                    Boolean(librariesLockedReason(token))
                  "
                  @update:model-value="updateTokenLibraries(token, $event)"
                />
              </td>
              <td class="account-token-wm">
                <v-switch
                  :model-value="token.watermark"
                  color="primary"
                  density="compact"
                  hide-details
                  class="account-token-wm-switch"
                  :disabled="
                    tokensLoading ||
                    watermarkUpdating.has(token.id) ||
                    token.scope !== 'READ'
                  "
                  @update:model-value="updateTokenWatermark(token, $event)"
                />
              </td>
              <td class="account-token-actions">
                <AppButton
                  variant="ghost"
                  size="sm"
                  icon-left="delete"
                  icon-only
                  tooltip="Revoke"
                  :disabled="tokensLoading"
                  @click="confirmDeleteToken(token)"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="!tokensLoading" class="account-token-empty">
        No API tokens yet.
      </div>
    </SettingsSection>
  </div>

  <!-- ── Connect an AI agent (MCP) ───────────────────────────────────── -->
  <ConnectAgentDialog
    :open="connectAgentDialogOpen"
    :offer-every-library="severalLibraries && librariesStore.canManage"
    @close="connectAgentDialogOpen = false"
    @created="fetchUserTokens"
  />

  <!-- ── New API token dialog (create form) ──────────────────────────── -->
  <AppDialog
    :open="createTokenDialogOpen"
    title="New API token"
    @close="createTokenDialogOpen = false"
  >
    <div class="account-token-form">
      <AppInput
        v-model="tokenDescription"
        label="Token description"
        placeholder="e.g. CI pipeline"
        :disabled="tokensLoading"
        @enter="createUserToken"
      />
      <AppSelect
        v-model="tokenScope"
        label="Access type"
        :disabled="tokensLoading"
        :options="[
          { label: 'Full access', value: 'ALL' },
          { label: 'Read-only share', value: 'READ' },
        ]"
      />
      <!-- Read-only-scope controls preserved from the original form. These keep
           their Vuetify chrome on purpose: the resource-type/resource models
           carry a real null (clearable) and numeric ids that a native <select>
           would coerce to strings. -->
      <template v-if="tokenScope === 'READ'">
        <v-select
          v-model="tokenResourceType"
          :items="[
            { title: 'Picture Set', value: 'picture_set' },
            { title: 'Character', value: 'character' },
            { title: 'Project', value: 'project' },
          ]"
          item-title="title"
          item-value="value"
          label="Resource type"
          density="compact"
          variant="outlined"
          hide-details
          clearable
          :disabled="tokensLoading"
        />
        <v-select
          v-if="tokenResourceType"
          v-model="tokenResourceId"
          :items="shareResourceOptions"
          item-title="label"
          item-value="id"
          label="Resource"
          density="compact"
          variant="outlined"
          hide-details
          :loading="shareResourceLoading"
          :disabled="tokensLoading || shareResourceLoading"
        />
        <v-checkbox
          v-if="tokenResourceType === 'project'"
          v-model="tokenIncludeAttachments"
          label="Include project attachments"
          density="compact"
          hide-details
          :disabled="tokensLoading"
        />
      </template>
      <AppInput
        v-model="tokenExpiresAt"
        label="Expires on (optional)"
        type="date"
        :disabled="tokensLoading"
      />
      <v-switch
        v-if="tokenScope === 'READ'"
        v-model="tokenWatermark"
        color="primary"
        density="compact"
        hide-details
        label="Apply watermark"
        :disabled="tokensLoading"
      />
      <div v-if="tokensError" class="account-error">{{ tokensError }}</div>
    </div>
    <template #footer>
      <AppButton
        variant="secondary"
        :disabled="tokensLoading"
        @click="createTokenDialogOpen = false"
      >
        Cancel
      </AppButton>
      <AppButton
        variant="primary"
        icon-left="key-plus"
        :disabled="tokensLoading"
        @click="createUserToken"
      >
        Create token
      </AppButton>
    </template>
  </AppDialog>

  <!-- ── Created-token display dialog (token value + share URL) ──────── -->
  <AppDialog
    :open="tokenDialogOpen"
    title="New API token"
    @close="tokenDialogOpen = false"
  >
    <div class="account-token-reveal">
      <div class="account-token-warning">
        Copy this token now. You won't be able to see it again.
      </div>
      <div class="account-token-value-row">
        <div class="account-token-value">{{ newlyCreatedToken }}</div>
        <AppButton
          variant="ghost"
          size="sm"
          :icon-left="tokenCopied ? 'check' : 'content-copy'"
          icon-only
          :tooltip="tokenCopied ? 'Copied!' : 'Copy token'"
          @click="copyToken"
        />
      </div>
      <template v-if="shareUrl">
        <div class="account-token-warning">
          Share this URL - anyone with it gets read access to the selected
          resource.
        </div>
        <div class="account-token-value-row">
          <div class="account-token-value account-token-value--url">
            {{ shareUrl }}
          </div>
          <AppButton
            variant="ghost"
            size="sm"
            :icon-left="shareLinkCopied ? 'check' : 'link'"
            icon-only
            :tooltip="shareLinkCopied ? 'Copied!' : 'Copy share link'"
            @click="copyShareLink"
          />
        </div>
      </template>
    </div>
    <template #footer>
      <AppButton variant="secondary" @click="tokenDialogOpen = false">
        Close
      </AppButton>
    </template>
  </AppDialog>

  <!-- ── Delete-token confirm dialog ─────────────────────────────────── -->
  <AppDialog
    :open="tokenDeleteDialogOpen"
    title="Delete token?"
    size="sm"
    @close="tokenDeleteDialogOpen = false"
  >
    <div class="account-token-confirm">
      This will permanently revoke the selected token.
    </div>
    <template #footer>
      <AppButton
        variant="secondary"
        :disabled="tokensLoading"
        @click="tokenDeleteDialogOpen = false"
      >
        Cancel
      </AppButton>
      <AppButton
        variant="danger"
        icon-left="delete-outline"
        :disabled="tokensLoading"
        @click="deleteUserToken"
      >
        Delete
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.account-pane {
  /* Fill the fixed-height Settings content box and lay the sections out as a
     column, so the API Tokens list can flex into the leftover space and scroll
     internally instead of pushing the whole pane past the content area. */
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* ── Account ──────────────────────────────────────────────────────────── */
.account-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-1) 0 var(--space-4);
}

.account-meta__label {
  font-size: var(--text-2xs);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.account-meta__value {
  font-weight: var(--weight-semibold);
}

.account-hidden-username {
  position: absolute;
  opacity: 0;
  height: 0;
  width: 0;
  pointer-events: none;
}

.account-password-grid {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: var(--space-4);
  align-items: flex-end;
}

.account-error {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-surface-error));
}

.account-success {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
}

/* ── API Tokens table ─────────────────────────────────────────────────── */
.account-tokens-error {
  margin-bottom: var(--space-3);
}

/* The API Tokens section is the flexible one: it grows to consume the pane's
   leftover height. Its header stays put while the table below scrolls inside it. */
.account-tokens-section {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

.account-token-table-wrap {
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  /* Fill the section's leftover height and scroll internally, so the Settings
     content area itself never grows a (global) scrollbar. */
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.account-token-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
}

.account-token-table thead th {
  text-align: left;
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.6);
  background: rgb(var(--v-theme-input-background));
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  white-space: nowrap;
  /* Stay visible while the list scrolls under it. */
  position: sticky;
  top: 0;
  z-index: var(--z-raised);
}

.account-token-th-actions {
  text-align: right;
}

.account-token-table tbody tr {
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.account-token-table tbody tr:last-child {
  border-bottom: none;
}

.account-token-table td {
  padding: var(--space-1) var(--space-2);
  vertical-align: middle;
}

.account-token-name {
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100px;
}

.account-token-th-wm {
  text-align: center;
}

.account-token-pill {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-2xs);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  color: rgb(var(--v-theme-on-surface));
  white-space: nowrap;
}

.account-token-pill--read {
  color: rgb(var(--v-theme-surface-info));
}

.account-token-sub {
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
}

.account-token-expired {
  color: rgb(var(--v-theme-surface-error));
  font-weight: var(--weight-semibold);
}

.account-token-wm {
  text-align: center;
  white-space: nowrap;
}

.account-token-wm-switch {
  display: inline-flex;
  justify-content: center;
  /* Shrink the Vuetify switch so it doesn't drive the row height. */
  transform: scale(0.8);
  transform-origin: center;
  margin: -6px 0;
}

.account-token-wm-switch :deep(.v-input__control) {
  flex: none;
}

.account-token-wm-switch :deep(.v-selection-control) {
  min-height: 0;
}

.account-token-actions {
  text-align: right;
  white-space: nowrap;
}

.account-token-empty {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  padding: var(--space-3) var(--space-1);
}

/* ── Dialog bodies ────────────────────────────────────────────────────── */
.account-token-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.account-token-check {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  cursor: pointer;
}

.account-token-reveal {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.account-token-warning {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.account-token-value-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.account-token-value {
  flex: 1;
  min-width: 0;
  word-break: break-all;
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
}

.account-token-value--url {
  font-size: var(--text-xs);
}

.account-token-confirm {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
  line-height: var(--leading-snug);
}
</style>
