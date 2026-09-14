<script setup>
import { ref, watch } from 'vue'
import { createToken } from '../../api/users'
import { patchUserConfig } from '../../api/config'
import { API_BASE_URL } from "../../utils/apiClient";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
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
  backendUrl: { type: String, default: () => API_BASE_URL },
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
    const created = await createToken({
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
    }, { baseUrl: base })
    const tok = created?.token
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
      // Fire-and-forget: the share link is already made, so a failed
      // preference save must not fail the dialog. Log it rather than drop it.
      patchUserConfig({ embed_watermark: watermark.value }, { baseUrl: base })
        .catch((e) => {
          console.warn('Failed to persist the embed-watermark preference:', e)
        })
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
  <AppDialog
    :open="modelValue"
    :title="resourceLabel ? `Share &quot;${resourceLabel}&quot;` : 'Share image'"
    @close="emit('update:modelValue', false)"
    @accept="!token && !loading && confirmCreate()"
  >
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
        <AppInput
          v-model="expiresAt"
          label="Expires on (optional)"
          type="date"
          :min="expiryMin()"
          :max="expiryMax()"
          :error="expiryError"
        />
        <v-checkbox
          v-if="resourceType === 'project'"
          v-model="includeAttachments"
          label="Include project attachments"
          density="compact"
          hide-details
        />
      </template>

      <v-checkbox
        v-model="watermark"
        label="Embed watermark"
        density="compact"
        hide-details
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
        <AppButton
          variant="ghost"
          icon-only
          :icon-left="copied ? 'check' : 'content-copy'"
          :tooltip="copied ? 'Copied!' : 'Copy link'"
          aria-label="Copy link"
          @click="copyUrl"
        />
      </div>
    </template>

    <template #footer>
      <AppButton variant="secondary" @click="emit('update:modelValue', false)">
        {{ token ? 'Close' : 'Cancel' }}
      </AppButton>
      <AppButton
        v-if="!token"
        variant="primary"
        :loading="loading"
        @click="confirmCreate"
      >
        Create link
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.share-dialog-hint {
  margin: 0;
  font-size: var(--text-base);
  opacity: 0.75;
}
.share-dialog-url-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-3);
}
.share-dialog-url {
  flex: 1;
  font-size: var(--text-2xs);
  word-break: break-all;
  opacity: 0.9;
  font-family: var(--font-mono);
}
.share-dialog-error {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-error));
}
</style>
