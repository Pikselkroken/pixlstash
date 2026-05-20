<script setup>
import { ref, watch } from 'vue'
import { apiClient } from '../utils/apiClient'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  resourceType: { type: String, default: '' },
  resourceId: { type: [Number, String], default: null },
  /** Shown in the dialog title as Share "…". If absent, defaults to "Share image". */
  resourceLabel: { type: String, default: '' },
  /** When provided the share URL is /share/${token}.${resourceFormat} (direct file link).
   *  When absent a gallery ?token= link is produced and expiry/attachments are shown. */
  resourceFormat: { type: String, default: '' },
  embedWatermark: { type: Boolean, default: false },
  backendUrl: { type: String, default: '' },
  publicUrl: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'update:embed-watermark', 'created'])

const expiresAt = ref(null)
const expiryError = ref('')
const includeAttachments = ref(false)
const watermark = ref(false)
const loading = ref(false)
const token = ref('')
const url = ref('')
const copied = ref(false)
const apiError = ref('')

function reset() {
  expiresAt.value = null
  expiryError.value = ''
  includeAttachments.value = false
  watermark.value = props.embedWatermark
  loading.value = false
  token.value = ''
  url.value = ''
  copied.value = false
  apiError.value = ''
}

watch(
  () => props.modelValue,
  (val) => { if (val) reset() },
)

const isGalleryMode = () => !props.resourceFormat

function expiryMin() {
  const d = new Date()
  d.setDate(d.getDate() + 1)
  return d.toISOString().slice(0, 10)
}

function expiryMax() {
  const d = new Date()
  d.setFullYear(d.getFullYear() + 1)
  return d.toISOString().slice(0, 10)
}

async function confirmCreate() {
  expiryError.value = ''
  apiError.value = ''

  if (isGalleryMode() && expiresAt.value) {
    const chosen = new Date(expiresAt.value)
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    if (chosen < today) {
      expiryError.value = 'Expiry date must be in the future.'
      return
    }
    const maxDate = new Date()
    maxDate.setFullYear(maxDate.getFullYear() + 1)
    if (chosen > maxDate) {
      expiryError.value = 'Expiry date cannot be more than 1 year from now.'
      return
    }
  }

  loading.value = true
  try {
    const base = props.backendUrl || ''
    const res = await apiClient.post(`${base}/users/me/token`, {
      description: props.resourceLabel
        ? `Share – ${props.resourceLabel}`
        : `Shared ${props.resourceType} #${props.resourceId}`,
      scope: 'READ',
      resource_type: props.resourceType,
      resource_id: props.resourceId,
      expires_at: isGalleryMode() ? (expiresAt.value || null) : null,
      include_attachments:
        isGalleryMode() && props.resourceType === 'project'
          ? includeAttachments.value
          : false,
      watermark: watermark.value,
    })
    const tok = res.data?.token
    if (!tok) throw new Error('No token returned')

    const origin = props.publicUrl || window.location.origin
    if (props.resourceFormat) {
      url.value = `${origin}/share/${tok}.${props.resourceFormat.toLowerCase()}`
    } else {
      url.value = `${origin}${window.location.pathname}?token=${tok}`
    }
    token.value = tok

    // Persist watermark preference if it changed
    if (watermark.value !== props.embedWatermark) {
      apiClient
        .patch(`${base}/users/me/config`, { embed_watermark: watermark.value })
        .catch(() => {})
      emit('update:embed-watermark', watermark.value)
    }

    emit('created')
  } catch {
    apiError.value = 'Failed to create share link. Please try again.'
  } finally {
    loading.value = false
  }
}

async function copyUrl() {
  try {
    await navigator.clipboard.writeText(url.value)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // clipboard not available
  }
}
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="460"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="share-dialog-card">
      <v-card-title class="share-dialog-title">
        <v-icon size="18" class="share-dialog-title-icon">mdi-share-variant-outline</v-icon>
        {{ resourceLabel ? `Share "${resourceLabel}"` : 'Share image' }}
      </v-card-title>

      <v-card-text class="share-dialog-body">
        <!-- Step 1: configure link -->
        <template v-if="!token">
          <p class="share-dialog-hint">
            <template v-if="resourceFormat">
              Creates a direct image link. Anyone with the link can view the
              full-resolution file.
            </template>
            <template v-else>
              This will create a sharable read-only link that can be emailed or
              posted online.
            </template>
          </p>

          <template v-if="isGalleryMode()">
            <p class="share-dialog-hint">Optionally set an expiry date.</p>
            <v-text-field
              v-model="expiresAt"
              label="Expires on (optional)"
              type="date"
              :min="expiryMin()"
              :max="expiryMax()"
              density="compact"
              variant="outlined"
              hide-details="auto"
              :error-messages="expiryError ? [expiryError] : []"
              class="share-dialog-date"
            />
            <v-checkbox
              v-if="resourceType === 'project'"
              v-model="includeAttachments"
              label="Include project attachments"
              density="compact"
              hide-details
              class="share-dialog-attachments-cb"
            />
          </template>

          <v-checkbox
            v-model="watermark"
            label="Embed watermark"
            density="compact"
            hide-details
            class="share-dialog-attachments-cb"
          />

          <p v-if="apiError" class="share-dialog-error">{{ apiError }}</p>
        </template>

        <!-- Step 2: show link -->
        <template v-else>
          <p class="share-dialog-hint">
            Copy this link. Anyone with it gets read-only access.
          </p>
          <div class="share-dialog-url-row">
            <div class="share-dialog-url">{{ url }}</div>
            <v-btn
              icon
              variant="text"
              size="small"
              :title="copied ? 'Copied!' : 'Copy link'"
              @click="copyUrl"
            >
              <v-icon size="18">{{ copied ? 'mdi-check' : 'mdi-content-copy' }}</v-icon>
            </v-btn>
          </div>
        </template>
      </v-card-text>

      <v-card-actions class="share-dialog-actions">
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">
          {{ token ? 'Close' : 'Cancel' }}
        </v-btn>
        <v-btn
          v-if="!token"
          variant="flat"
          color="primary"
          :loading="loading"
          @click="confirmCreate"
        >
          Create Link
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.share-dialog-card {
  background: rgb(var(--v-theme-surface));
}
.share-dialog-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 1rem;
  font-weight: 600;
  padding: 16px 20px 8px;
}
.share-dialog-title-icon {
  opacity: 0.8;
}
.share-dialog-body {
  padding: 8px 20px 4px;
}
.share-dialog-hint {
  font-size: 0.85rem;
  opacity: 0.75;
  margin-bottom: 14px;
}
.share-dialog-date {
  margin-bottom: 4px;
}
.share-dialog-url-row {
  display: flex;
  align-items: center;
  gap: 8px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 6px;
  padding: 8px 10px;
}
.share-dialog-url {
  flex: 1;
  font-size: 11px;
  word-break: break-all;
  opacity: 0.9;
  font-family: monospace;
}
.share-dialog-actions {
  padding: 8px 16px 16px;
}
.share-dialog-error {
  font-size: 0.82rem;
  color: rgb(var(--v-theme-error));
  margin-top: 8px;
}
</style>
