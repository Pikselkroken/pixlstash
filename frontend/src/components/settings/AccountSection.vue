<script setup>
import { computed, ref, watch } from "vue";
import { apiClient, isReadOnly } from "../../utils/apiClient";
import { copyText } from "../../utils/clipboard";

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
const tokenToDelete = ref(null);
const tokenScope = ref("ALL");
const tokenResourceType = ref(null);
const tokenResourceId = ref(null);
const tokenExpiresAt = ref(null);
const tokenIncludeAttachments = ref(false);
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
  tokenToDelete.value = null;
  tokenScope.value = "ALL";
  tokenResourceType.value = null;
  tokenResourceId.value = null;
  tokenExpiresAt.value = null;
  tokenIncludeAttachments.value = false;
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
  return date.toLocaleString();
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
  return date.toLocaleString();
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
    });
    newlyCreatedToken.value = res.data?.token || "";
    tokenDialogOpen.value = Boolean(newlyCreatedToken.value);
    tokenDescription.value = "";
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = e?.response?.data?.detail || "Failed to create token.";
  } finally {
    tokensLoading.value = false;
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
);
</script>

<template>
  <div class="settings-section">
    <div
      class="settings-section-title"
      title="Change your password or manage sign-in options."
    >
      Account
    </div>
    <div class="settings-account-meta">
      <span class="settings-account-label">Username</span>
      <span class="settings-account-value">
        {{ settingsUsername || "Not set" }}
      </span>
    </div>
    <div class="settings-form">
      <input
        v-if="settingsUsername"
        type="text"
        name="username"
        :value="settingsUsername"
        autocomplete="username"
        style="
          position: absolute;
          opacity: 0;
          height: 0;
          width: 0;
          pointer-events: none;
        "
        tabindex="-1"
      />
      <v-text-field
        v-if="settingsHasPassword"
        v-model="currentPassword"
        label="Current password"
        type="password"
        density="compact"
        variant="filled"
        hide-details
        autocomplete="current-password"
        name="current-password"
      />
      <v-text-field
        v-model="newPassword"
        label="New password"
        :type="showNewPassword ? 'text' : 'password'"
        density="compact"
        variant="filled"
        hide-details
        autocomplete="new-password"
        name="new-password"
        :append-inner-icon="showNewPassword ? 'mdi-eye-off' : 'mdi-eye'"
        @click:append-inner="showNewPassword = !showNewPassword"
      />
      <div v-if="settingsError" class="settings-error">
        {{ settingsError }}
      </div>
      <div v-if="settingsSuccess" class="settings-success">
        {{ settingsSuccess }}
      </div>
      <v-btn
        variant="outlined"
        color="primary"
        class="settings-action-btn"
        :loading="settingsLoading"
        :disabled="settingsLoading"
        @click="submitPasswordChange"
      >
        Update Password
      </v-btn>
    </div>
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div
      class="settings-section-title"
      title="Set a public URL so share links work outside your local network (e.g. a Cloudflare Tunnel address)."
    >
      Sharing
    </div>
    <div class="settings-public-url-form">
      <v-text-field
        v-model="publicUrlValue"
        label="Public base URL (optional)"
        placeholder="https://my-tunnel.example.com"
        density="compact"
        variant="underlined"
        hide-details
        :disabled="publicUrlLoading"
        @keydown.enter.prevent="savePublicUrl"
      />
      <v-btn
        variant="outlined"
        color="primary"
        class="settings-action-btn"
        :loading="publicUrlLoading"
        :disabled="publicUrlLoading"
        @click="savePublicUrl"
      >
        Save
      </v-btn>
    </div>
    <div v-if="publicUrlError" class="settings-error">
      {{ publicUrlError }}
    </div>
    <div v-if="publicUrlSuccess" class="settings-success">
      {{ publicUrlSuccess }}
    </div>
    <div class="settings-section-desc">
      Share links use your browser's current address by default. Set this to a
      public URL (e.g. a
      <a
        href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/"
        target="_blank"
        rel="noopener noreferrer"
        >Cloudflare Tunnel</a
      >
      address) so links work for people outside your network.
    </div>
    <!-- Watermark -->
    <div class="settings-watermark-row">
      <img
        v-if="watermarkPreviewUrl"
        :src="watermarkPreviewUrl"
        class="settings-watermark-preview"
        alt="Watermark preview"
      />
      <input
        ref="watermarkInputRef"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        style="display: none"
        @change="handleWatermarkUpload"
      />
      <v-btn
        size="small"
        variant="outlined"
        :loading="watermarkUploading"
        :disabled="watermarkUploading"
        @click="watermarkInputRef?.click()"
      >
        Upload watermark
      </v-btn>
      <v-btn
        size="small"
        variant="text"
        color="error"
        :disabled="watermarkUploading"
        title="Reset to default watermark"
        @click="clearWatermark"
      >
        Reset
      </v-btn>
    </div>
    <div v-if="watermarkUploadError" class="settings-error">
      {{ watermarkUploadError }}
    </div>
  </div>
  <v-divider class="settings-section-divider" />
  <div class="settings-section">
    <div
      class="settings-section-title"
      title="Manage tokens for authenticated API access."
    >
      API Tokens
    </div>
    <div class="settings-tokens">
      <v-text-field
        v-model="tokenDescription"
        label="Token description"
        density="compact"
        variant="underlined"
        class="settings-add-tag-input token-field"
        hide-details
        :disabled="tokensLoading"
        @keydown.enter.prevent="createUserToken"
      />
      <v-select
        v-model="tokenScope"
        :items="[
          { title: 'Full access', value: 'ALL' },
          { title: 'Read-only share', value: 'READ' },
        ]"
        item-title="title"
        item-value="value"
        label="Access type"
        density="compact"
        variant="underlined"
        class="token-field"
        hide-details
        :disabled="tokensLoading"
      />
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
          variant="underlined"
          class="token-field"
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
          variant="underlined"
          class="token-field"
          hide-details
          :loading="shareResourceLoading"
          :disabled="tokensLoading || shareResourceLoading"
        />
        <v-text-field
          v-model="tokenExpiresAt"
          label="Expires on (optional)"
          type="date"
          :min="
            (() => {
              const d = new Date();
              d.setDate(d.getDate() + 1);
              return d.toISOString().slice(0, 10);
            })()
          "
          :max="
            (() => {
              const d = new Date();
              d.setFullYear(d.getFullYear() + 1);
              return d.toISOString().slice(0, 10);
            })()
          "
          density="compact"
          variant="underlined"
          class="token-field"
          hide-details="auto"
          :disabled="tokensLoading"
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
      <v-btn
        variant="outlined"
        color="primary"
        class="settings-action-btn"
        :loading="tokensLoading"
        :disabled="tokensLoading"
        @click="createUserToken"
      >
        Create Token
      </v-btn>
      <div v-if="tokensError" class="settings-error">
        {{ tokensError }}
      </div>
      <div class="settings-token-list">
        <table v-if="tokens.length" class="settings-token-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Scope</th>
              <th>Created</th>
              <th>Last used</th>
              <th>Expires</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="token in tokens"
              :key="token.id"
              class="settings-token-row"
            >
              <td class="settings-token-desc">
                {{ token.description || "Token" }}
              </td>
              <td>
                <v-chip
                  v-if="token.scope"
                  size="x-small"
                  :color="token.scope === 'ALL' ? 'default' : 'info'"
                  class="settings-token-scope-chip"
                >
                  <template v-if="token.scope === 'ALL'">
                    <v-icon size="11" start>mdi-shield-account-outline</v-icon>
                    Full access
                  </template>
                  <template v-else-if="token.resource_type === 'project'">
                    <v-icon size="11" start>mdi-folder-outline</v-icon>
                    {{ token.resource_name ?? `Project #${token.resource_id}` }}
                  </template>
                  <template v-else-if="token.resource_type === 'character'">
                    <v-icon size="11" start>mdi-account-outline</v-icon>
                    {{
                      token.resource_name ?? `Character #${token.resource_id}`
                    }}
                  </template>
                  <template
                    v-else-if="token.resource_type === 'picture_set'"
                  >
                    <v-icon size="11" start>mdi-image-multiple-outline</v-icon>
                    {{ token.resource_name ?? `Set #${token.resource_id}` }}
                  </template>
                  <template v-else>Read-only</template>
                </v-chip>
              </td>
              <td class="settings-token-sub">
                {{ formatTokenTimestamp(token.created_at) }}
              </td>
              <td class="settings-token-sub">
                {{ formatTokenTimestamp(token.last_used_at) }}
              </td>
              <td
                class="settings-token-sub"
                :class="{ 'settings-token-expired': isTokenExpired(token) }"
              >
                {{ formatTokenExpiry(token) }}
              </td>
              <td class="settings-token-actions">
                <v-btn
                  icon
                  size="small"
                  density="compact"
                  variant="text"
                  class="settings-token-delete"
                  :disabled="tokensLoading"
                  @click="confirmDeleteToken(token)"
                >
                  <v-icon size="16">mdi-delete</v-icon>
                </v-btn>
              </td>
            </tr>
          </tbody>
        </table>
        <div
          v-if="!tokensLoading && !tokens.length"
          class="settings-token-empty"
        >
          No API tokens.
        </div>
      </div>
    </div>
  </div>

  <v-dialog v-model="tokenDialogOpen" max-width="520">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title">New API Token</v-card-title>
      <v-card-text class="settings-dialog-body">
        <div class="settings-token-warning">
          Copy this token now. You won't be able to see it again.
        </div>
        <div class="settings-token-value-row">
          <div class="settings-token-value">{{ newlyCreatedToken }}</div>
          <v-btn
            icon
            variant="text"
            size="small"
            class="settings-token-copy-btn"
            :title="tokenCopied ? 'Copied!' : 'Copy token'"
            @click="copyToken"
          >
            <v-icon size="18">{{
              tokenCopied ? "mdi-check" : "mdi-content-copy"
            }}</v-icon>
          </v-btn>
        </div>
        <template v-if="shareUrl">
          <div class="settings-token-warning" style="margin-top: 8px">
            Share this URL — anyone with it gets read access to the selected
            resource.
          </div>
          <div class="settings-token-value-row">
            <div
              class="settings-token-value"
              style="word-break: break-all; font-size: 11px"
            >
              {{ shareUrl }}
            </div>
            <v-btn
              icon
              variant="text"
              size="small"
              class="settings-token-copy-btn"
              :title="shareLinkCopied ? 'Copied!' : 'Copy share link'"
              @click="copyShareLink"
            >
              <v-icon size="18">{{
                shareLinkCopied ? "mdi-check" : "mdi-link"
              }}</v-icon>
            </v-btn>
          </div>
        </template>
      </v-card-text>
      <v-card-actions class="settings-dialog-actions">
        <v-spacer />
        <v-btn
          variant="outlined"
          color="primary"
          @click="tokenDialogOpen = false"
        >
          Close
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="tokenDeleteDialogOpen" max-width="420">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title">Delete token?</v-card-title>
      <v-card-text class="settings-dialog-body">
        This will permanently revoke the selected token.
      </v-card-text>
      <v-card-actions class="settings-dialog-actions">
        <v-spacer />
        <v-btn variant="text" @click="tokenDeleteDialogOpen = false">
          Cancel
        </v-btn>
        <v-btn
          color="error"
          variant="outlined"
          :loading="tokensLoading"
          @click="deleteUserToken"
        >
          Delete
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
/* Shared layout classes (duplicated from parent for scoped isolation) */
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
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  line-height: 1.4;
}

.settings-section-divider {
  margin: 4px 0 8px;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.9em;
}

.settings-success {
  color: rgb(var(--v-theme-accent));
  font-size: 0.9em;
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

.settings-add-tag-input {
  flex: 1 1 auto;
}

/* Account-specific */
.settings-account-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0 2px;
}

.settings-account-label {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.settings-account-value {
  font-weight: 600;
}

.settings-public-url-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.settings-watermark-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.settings-watermark-preview {
  max-height: 36px;
  max-width: 120px;
  object-fit: contain;
  border-radius: 4px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  padding: 2px;
}

/* Token styles */
.settings-tokens {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.token-field {
  font-size: 0.85em;
}

.token-field :deep(.v-label) {
  font-size: 0.85em;
}

.token-field :deep(.v-field__input) {
  font-size: 0.85em;
}

.settings-token-loading {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-token-list {
  max-height: 200px;
  overflow-y: auto;
  padding-right: 4px;
}

.settings-token-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.82em;
}

.settings-token-table thead th {
  text-align: left;
  padding: 2px 8px 4px;
  font-size: 0.78em;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(var(--v-theme-on-surface), 0.5);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  white-space: nowrap;
}

.settings-token-row td {
  padding: 3px 8px;
  vertical-align: middle;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.05);
}

.settings-token-row:last-child td {
  border-bottom: none;
}

.settings-token-desc {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 140px;
}

.settings-token-sub {
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
}

.settings-token-expired {
  color: rgb(var(--v-theme-error));
  font-weight: 600;
}

.settings-token-actions {
  text-align: right;
  white-space: nowrap;
  padding-left: 0;
}

.settings-token-delete {
  color: rgba(var(--v-theme-error), 0.9);
}

.settings-token-empty {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* Token dialog styles */
.settings-token-dialog {
  padding-bottom: 8px;
}

.settings-dialog-title {
  font-weight: 700;
  font-size: 1.2rem;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.settings-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  line-height: 1;
  overflow-y: auto !important;
  flex: 1 !important;
  min-height: 0 !important;
}

.settings-dialog-actions {
  padding-top: 0;
}

.settings-token-warning {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-bottom: 6px;
}

.settings-token-value-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.settings-token-value {
  flex: 1;
  word-break: break-all;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  background: rgba(var(--v-theme-surface), 0.2);
  border-radius: 8px;
  padding: 2px 4px;
}

.settings-token-copy-btn {
  flex-shrink: 0;
  opacity: 0.7;
}

.settings-token-copy-btn:hover {
  opacity: 1;
}
</style>
