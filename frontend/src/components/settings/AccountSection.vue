<script setup>
import { computed, reactive, ref, watch } from "vue";
import { VSwitch } from "vuetify/components";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import { copyText } from "../../utils/clipboard";
import AppDialog from "../widgets/AppDialog.vue";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import SettingsSection from "./SettingsSection.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["update:public-url"]);

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

// --- Public URL / watermark state ---
const publicUrlValue = ref("");
const publicUrlLoading = ref(false);
const publicUrlError = ref("");
const publicUrlSuccess = ref("");
const watermarkPreviewUrl = ref("");
const watermarkInputRef = ref(null);
const watermarkUploading = ref(false);
const watermarkUploadError = ref("");

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
  tokenToDelete.value = null;
  tokenScope.value = "ALL";
  tokenResourceType.value = null;
  tokenResourceId.value = null;
  tokenExpiresAt.value = null;
  tokenIncludeAttachments.value = false;
  tokenWatermark.value = false;
  shareResourceOptions.value = [];
  shareLinkCopied.value = false;
  publicUrlValue.value = "";
  publicUrlError.value = "";
  publicUrlSuccess.value = "";
  watermarkPreviewUrl.value = "";
  watermarkUploadError.value = "";
}

async function fetchSettingsAuth() {
  settingsLoading.value = true;
  settingsError.value = "";
  try {
    const res = await apiClient.get("/users/me/auth");
    settingsUsername.value = res.data?.username || "";
    settingsHasPassword.value = Boolean(res.data?.has_password);
  } catch (e) {
    settingsError.value = "Failed to load account settings.";
  } finally {
    settingsLoading.value = false;
  }
}

async function fetchUserTokens() {
  tokensLoading.value = true;
  tokensError.value = "";
  try {
    const res = await apiClient.get("/users/me/token");
    tokens.value = Array.isArray(res.data) ? res.data : [];
  } catch (e) {
    tokensError.value = "Failed to load tokens.";
  } finally {
    tokensLoading.value = false;
  }
}

async function fetchPublicUrl() {
  publicUrlLoading.value = true;
  try {
    const res = await apiClient.get("/users/me/config");
    publicUrlValue.value = res.data?.public_url || "";
  } catch (_) {
    /* silent */
  } finally {
    publicUrlLoading.value = false;
  }
}

function refreshWatermarkPreview() {
  watermarkPreviewUrl.value = `/api/v1/users/me/watermark?cb=${Date.now()}`;
}

async function savePublicUrl() {
  publicUrlError.value = "";
  publicUrlSuccess.value = "";
  const trimmed = publicUrlValue.value.trim().replace(/\/$/, "");
  publicUrlValue.value = trimmed;
  publicUrlLoading.value = true;
  try {
    await apiClient.patch("/users/me/config", {
      public_url: trimmed || null,
    });
    emit("update:public-url", trimmed || null);
    publicUrlSuccess.value = "Saved.";
    setTimeout(() => {
      publicUrlSuccess.value = "";
    }, 2500);
  } catch (_) {
    publicUrlError.value = "Failed to save.";
  } finally {
    publicUrlLoading.value = false;
  }
}

async function handleWatermarkUpload(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  watermarkUploadError.value = "";
  watermarkUploading.value = true;
  try {
    const form = new FormData();
    form.append("file", file);
    await apiClient.post("/users/me/watermark", form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    refreshWatermarkPreview();
  } catch (_) {
    watermarkUploadError.value = "Upload failed.";
  } finally {
    watermarkUploading.value = false;
    if (watermarkInputRef.value) watermarkInputRef.value.value = "";
  }
}

async function clearWatermark() {
  watermarkUploadError.value = "";
  watermarkUploading.value = true;
  try {
    await apiClient.delete("/users/me/watermark");
    refreshWatermarkPreview();
  } catch (_) {
    watermarkUploadError.value = "Failed to remove watermark.";
  } finally {
    watermarkUploading.value = false;
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
      const res = await apiClient.get("/picture_sets");
      items = (res.data || []).map((s) => ({ id: s.id, label: s.name }));
    } else if (type === "character") {
      const res = await apiClient.get("/characters");
      items = (res.data || []).map((c) => ({ id: c.id, label: c.name }));
    } else if (type === "project") {
      const res = await apiClient.get("/projects");
      items = (res.data || []).map((p) => ({ id: p.id, label: p.name }));
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
  // Prefer the configured public URL so the link works from outside the LAN.
  const origin =
    publicUrlValue.value?.trim().replace(/\/$/, "") || window.location.origin;
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
    const res = await apiClient.post("/users/me/token", {
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
    newlyCreatedToken.value = res.data?.token || "";
    tokenDialogOpen.value = Boolean(newlyCreatedToken.value);
    if (newlyCreatedToken.value) createTokenDialogOpen.value = false;
    tokenDescription.value = "";
    tokenWatermark.value = false;
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = e?.response?.data?.detail || "Failed to create token.";
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
    await apiClient.patch(`/users/me/token/${token.id}`, { watermark: value });
  } catch (e) {
    tokensError.value = e?.response?.data?.detail || "Failed to update token.";
    token.watermark = previousValue;
  } finally {
    watermarkUpdating.delete(token.id);
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
    await apiClient.delete(`/users/me/token/${tokenToDelete.value.id}`);
    tokenDeleteDialogOpen.value = false;
    tokenToDelete.value = null;
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = e?.response?.data?.detail || "Failed to delete token.";
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
    await apiClient.post("/users/me/auth", {
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
    settingsError.value =
      e?.response?.data?.detail || "Failed to update password.";
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
        fetchPublicUrl();
        refreshWatermarkPreview();
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
          variant="primary_green"
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

    <!-- ── Sharing + Watermark ───────────────────────────────────────── -->
    <SettingsSection>
      <div class="account-share-heads">
        <div class="account-share-heads__main">Sharing</div>
        <div class="account-share-heads__wm">Watermark</div>
      </div>
      <div class="account-share-row">
        <div class="account-share-left">
          <div class="account-share-desc">
            Share links use your browser's current address. Set this to a public
            URL (e.g. a
            <a
              href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/"
              target="_blank"
              rel="noopener noreferrer"
              >Cloudflare Tunnel</a
            >
            address) so links work for people outside your network.
          </div>
          <AppInput
            v-model="publicUrlValue"
            label="Public base URL (optional)"
            placeholder="https://my-tunnel.example.com"
            :disabled="publicUrlLoading"
            @enter="savePublicUrl"
            @blur="savePublicUrl"
          />
          <div
            v-if="publicUrlError || publicUrlSuccess"
            class="account-share-status"
          >
            <span v-if="publicUrlError" class="account-error">
              {{ publicUrlError }}
            </span>
            <span v-else-if="publicUrlSuccess" class="account-success">
              {{ publicUrlSuccess }}
            </span>
          </div>
        </div>
        <!-- WatermarkDrop: 184px click-or-drop target with reset overlay.
             Logic (file input ref, change handler, preview src, clear) is
             unchanged from the script. -->
        <div class="account-share-right">
          <div class="wm-drop">
            <button
              type="button"
              class="wm-drop__target"
              :class="{ 'wm-drop__target--has-image': watermarkPreviewUrl }"
              :style="
                watermarkPreviewUrl
                  ? { backgroundImage: `url(${watermarkPreviewUrl})` }
                  : null
              "
              title="Upload watermark"
              :disabled="watermarkUploading"
              @click="watermarkInputRef?.click()"
            >
              <template v-if="!watermarkPreviewUrl">
                <v-icon size="26">mdi-image-plus-outline</v-icon>
                <span class="wm-drop__hint">Click or drop an image</span>
              </template>
            </button>
            <button
              v-if="watermarkPreviewUrl"
              type="button"
              class="wm-drop__reset"
              title="Reset to default watermark"
              :disabled="watermarkUploading"
              @click="clearWatermark"
            >
              <v-icon size="15">mdi-close</v-icon>
            </button>
            <input
              ref="watermarkInputRef"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              class="wm-drop__input"
              @change="handleWatermarkUpload"
            />
          </div>
          <div v-if="watermarkUploadError" class="account-error">
            {{ watermarkUploadError }}
          </div>
        </div>
      </div>
    </SettingsSection>

    <!-- ── API Tokens ────────────────────────────────────────────────── -->
    <SettingsSection title="API Tokens" class="account-tokens-section">
      <template #action>
        <AppButton
          variant="primary_green"
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
              <td class="account-token-wm">
                <v-switch
                  :model-value="token.watermark"
                  color="accent"
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
                  title="Revoke"
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

  <!-- ── New API token dialog (create form) ──────────────────────────── -->
  <AppDialog
    :open="createTokenDialogOpen"
    title="New API token"
    :width="460"
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
        color="accent"
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
        variant="primary_green"
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
    :width="520"
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
          :title="tokenCopied ? 'Copied!' : 'Copy token'"
          @click="copyToken"
        />
      </div>
      <template v-if="shareUrl">
        <div class="account-token-warning">
          Share this URL — anyone with it gets read access to the selected
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
            :title="shareLinkCopied ? 'Copied!' : 'Copy share link'"
            @click="copyShareLink"
          />
        </div>
      </template>
    </div>
    <template #footer>
      <AppButton variant="primary_green" @click="tokenDialogOpen = false">
        Close
      </AppButton>
    </template>
  </AppDialog>

  <!-- ── Delete-token confirm dialog ─────────────────────────────────── -->
  <AppDialog
    :open="tokenDeleteDialogOpen"
    title="Delete token?"
    :width="420"
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
  color: rgb(var(--v-theme-error));
}

.account-success {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-accent));
}

/* ── Sharing + Watermark ──────────────────────────────────────────────── */
.account-share-heads {
  display: flex;
  gap: var(--space-6);
  margin-bottom: var(--space-3);
}

.account-share-heads__main {
  flex: 1;
  min-width: 0;
  font-weight: var(--weight-semibold);
  font-size: var(--text-base);
}

.account-share-heads__wm {
  flex-shrink: 0;
  width: 184px;
  font-weight: var(--weight-semibold);
  font-size: var(--text-base);
}

.account-share-row {
  display: flex;
  gap: var(--space-6);
  align-items: stretch;
}

.account-share-left {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.account-share-desc {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: var(--leading-snug);
}

.account-share-desc a {
  color: rgb(var(--v-theme-accent));
}

.account-share-status {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: 1em;
}

.account-share-right {
  flex-shrink: 0;
  width: 184px;
  display: flex;
  flex-direction: column;
}

/* WatermarkDrop — the thumbnail IS the control: click opens the file picker,
   drop sets the image, a reset overlay clears it. */
.wm-drop {
  position: relative;
  width: 184px;
  flex: 1;
  min-height: 96px;
}

.wm-drop__target {
  width: 100%;
  height: 100%;
  min-height: 96px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1.5px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  color: rgba(var(--v-theme-on-surface), 0.6);
  cursor: pointer;
  transition:
    border-color var(--dur-1) var(--ease-standard),
    background var(--dur-1) var(--ease-standard);
}

.wm-drop__target:hover:not(:disabled) {
  border-color: rgb(var(--v-theme-accent));
  background: var(--hover-wash);
}

.wm-drop__target:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.wm-drop__target:disabled {
  cursor: default;
  opacity: 0.6;
}

.wm-drop__target--has-image {
  background-size: contain;
  background-repeat: no-repeat;
  background-position: center;
}

.wm-drop__hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  text-align: center;
}

.wm-drop__reset {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  width: 24px;
  height: 24px;
  border: none;
  border-radius: var(--radius-pill);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  /* The one allowed exception: a translucent black scrim with a white icon. */
  background: rgba(var(--v-theme-scrim), 0.6);
  color: #fff;
  backdrop-filter: blur(2px);
}

.wm-drop__reset:disabled {
  cursor: default;
  opacity: 0.6;
}

.wm-drop__input {
  display: none;
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
  z-index: 1;
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
  padding: 2px var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  color: rgb(var(--v-theme-on-surface));
  white-space: nowrap;
}

.account-token-pill--read {
  color: rgb(var(--v-theme-info));
}

.account-token-sub {
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
}

.account-token-expired {
  color: rgb(var(--v-theme-error));
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
