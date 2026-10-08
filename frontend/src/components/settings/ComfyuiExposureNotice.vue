<script setup>
// Says that other computers can use PixlStash through a ComfyUI they can
// reach, and what to do about it. ComfyUI has no password and the pack's
// routes serve the library to whoever calls them; the link key itself only
// works from ComfyUI's own address, so this is the exposure that is left.
// `exposure` is the backend's: "other_computer" (ComfyUI is on another
// computer) or "listening" (on this one, started with a network --listen).
import { VIcon } from "vuetify/components";

defineProps({
  exposure: { type: String, required: true },
});
</script>

<template>
  <div class="cx-notice" data-testid="comfyui-exposure-notice">
    <v-icon class="cx-glyph" size="18" aria-hidden="true"
      >mdi-alert-outline</v-icon
    >
    <div class="cx-body">
      <p class="cx-title">Other computers can use PixlStash through ComfyUI</p>
      <template v-if="exposure === 'listening'">
        <p class="cx-text">
          ComfyUI was started with <code>--listen</code>, so it answers on your
          network, and it has no password. Anyone who can open its page can
          browse this library and save pictures into it through the PixlStash
          nodes.
        </p>
        <p class="cx-text">
          If only this computer uses ComfyUI, restart it without
          <code>--listen</code>, or with <code>--listen 127.0.0.1</code> (in
          ComfyUI Desktop, set the address it listens on to 127.0.0.1 in its
          server settings). If other computers need it, allow ComfyUI's port
          in this computer's firewall for those computers only.
        </p>
      </template>
      <template v-else>
        <p class="cx-text">
          ComfyUI is on another computer, so it answers on your network, and it
          has no password. Anyone who can open its page can browse this library
          and save pictures into it through the PixlStash nodes.
        </p>
        <p class="cx-text">
          Link it only on a network you trust, and on the computer ComfyUI runs
          on, allow its port in the firewall for your own computers only.
        </p>
      </template>
      <p class="cx-text">
        The key PixlStash gives ComfyUI is refused from every other address, so
        a copy taken off ComfyUI's page does not work from another computer,
        only through ComfyUI itself.
      </p>
    </div>
  </div>
</template>

<style scoped>
/* The app's notice shape (ExportRecipeDialog.vue): the glyph and the rail
   carry the warning hue, the sentences stay text. */
.cx-notice {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-border));
  border-left: var(--rail-w) solid rgb(var(--v-theme-surface-warning));
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.cx-glyph {
  color: rgb(var(--v-theme-surface-warning));
}

.cx-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.cx-title,
.cx-text {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

.cx-title {
  font-weight: var(--weight-semibold);
}

.cx-text {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cx-text code {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}
</style>
