<script setup>
import { computed } from "vue";
import { VIcon } from "vuetify/components";
import { defaultInstallType } from "../../utils/telemetryPayload";

/**
 * "PixlStash needs you!": the way from the app to the report page on
 * pixlstash.dev, pinned to the foot of the Settings rail.
 *
 * One link and nothing else. The page it opens is a static wizard that sends
 * nothing by itself; the version and install type ride along in the query
 * string only so the reporter does not have to look them up, and only when
 * they click.
 */
const props = defineProps({
  version: { type: String, default: "" },
});

/** The report page; `website/report.html` reads `version` and `install`. */
const REPORT_PAGE_URL = "https://pixlstash.dev/report.html";

/**
 * Served from `frontend/public`. Bound rather than written as a literal `src`
 * so the template compiler leaves it a URL instead of turning it into an
 * import.
 */
const POSTER_SRC = "/PixlStashNeedsYou.jpg";

const href = computed(() => {
  const params = new URLSearchParams();
  if (props.version) params.set("version", props.version);
  const installType = defaultInstallType();
  if (installType) params.set("install", installType);
  const query = params.toString();
  return query ? `${REPORT_PAGE_URL}?${query}` : REPORT_PAGE_URL;
});
</script>

<template>
  <a
    class="needs-you"
    data-testid="needs-you-box"
    :href="href"
    target="_blank"
    rel="noopener noreferrer"
  >
    <img
      class="needs-you__poster"
      :src="POSTER_SRC"
      alt="PixlStash needs you!"
    />
    <span class="needs-you__text">
      Report a bug or an idea
      <v-icon size="12" class="needs-you__ext">mdi-open-in-new</v-icon>
    </span>
  </a>
</template>

<style scoped>
/* A card at the foot of the rail: the surface tier radius, the info-card
   ground, and the rail's own hover wash. */
.needs-you {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
  transition: background var(--dur-1) var(--ease-standard);
}

.needs-you:hover {
  background-color: var(--hover-wash);
}

.needs-you__poster {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
  object-fit: cover;
}

.needs-you__text {
  min-width: 0;
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.needs-you:hover .needs-you__text {
  color: rgb(var(--v-theme-on-surface));
}

.needs-you__ext {
  vertical-align: -1px;
}
</style>
