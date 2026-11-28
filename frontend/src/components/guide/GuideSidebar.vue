<template>
  <!-- The Guide's own sidebar (#2825): browse every concept, Start here and
       New in 2.x, then by goal and by workspace. Plain rows with counts, the
       way the Studio and Models sidebars draw theirs. The slot on the
       toolbar's line replays the welcome. -->
  <aside
    class="gsb sidebar-panel"
    :class="{ 'sidebar--narrow': narrow }"
    :style="{ width: `${width}px` }"
    aria-label="Guide navigation"
    data-testid="guide-sidebar"
  >
    <SidebarResizeHandle />
    <button
      type="button"
      class="gsb-slot"
      data-testid="guide-replay-welcome"
      @click="guide.replayWelcome()"
    >
      <span class="gsb-slot-mark" aria-hidden="true"
        ><v-icon size="12">mdi-play</v-icon></span
      >
      <span class="gsb-slot-name">Play the welcome again</span>
    </button>
    <nav class="gsb-list sidebar-nav-list sidebar-scroll">
      <template v-for="group in groups" :key="group.title">
        <div class="sidebar-section-header" role="presentation">
          <span class="sidebar-section-title">{{ group.title }}</span>
        </div>
        <button
          v-for="row in group.rows"
          :key="row.id"
          type="button"
          class="sidebar-list-item"
          :class="{ active: row.id === current }"
          :aria-current="row.id === current ? 'page' : undefined"
          :data-testid="`guide-row-${row.id}`"
          @click="browse(row.id)"
        >
          <span class="sidebar-list-icon" aria-hidden="true"
            ><v-icon size="16">{{ row.icon }}</v-icon></span
          >
          <span class="sidebar-list-label sidebar-list-label-text">{{
            row.label
          }}</span>
          <span class="sidebar-list-count">{{ row.count }}</span>
        </button>
      </template>
    </nav>
  </aside>
</template>

<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon } from "vuetify/components";
import SidebarResizeHandle from "../panels/SidebarResizeHandle.vue";
import { useSidebarWidth } from "../../composables/useSidebarWidth";
import { useGuideStore } from "../../stores/useGuideStore";
import { GOALS, HEADLINES, WORKSPACES } from "../../guide/catalogue";
import { browseOf, GUIDE_CONCEPTS } from "../../guide/browse";

const { width, narrow } = useSidebarWidth();
const route = useRoute();
const router = useRouter();
const guide = useGuideStore();

const current = computed(() => browseOf(route));

const groups = computed(() => [
  {
    title: "Browse",
    rows: [
      {
        id: "all",
        label: "All concepts",
        icon: "mdi-view-grid-outline",
        count: GUIDE_CONCEPTS.length,
      },
      {
        id: "start",
        label: "Start here",
        icon: "mdi-flag-outline",
        count: guide.startHere.length,
      },
      {
        id: "new",
        label: "New in 2.x",
        icon: "mdi-star-four-points-outline",
        count: HEADLINES.length,
      },
    ],
  },
  {
    title: "By goal",
    rows: GOALS.map((g) => ({
      id: `goal:${g.id}`,
      label: g.label,
      icon: "mdi-target",
      count: GUIDE_CONCEPTS.filter((c) => c.goal === g.id).length,
    })),
  },
  {
    title: "By workspace",
    rows: WORKSPACES.map((w) => ({
      id: `ws:${w.id}`,
      label: w.label,
      icon: w.icon ?? "mdi-application-outline",
      count: GUIDE_CONCEPTS.filter((c) => c.workspaces?.includes(w.id)).length,
    })),
  },
]);

/** A row always lands on the grid, even from a concept page. */
function browse(id) {
  router.push({ path: "/guide", query: id === "all" ? {} : { in: id } });
}
</script>

<style scoped src="../panels/SidebarNav.css"></style>
<style scoped>
.gsb {
  flex-shrink: 0;
}

/* The slot on the toolbar's line, as the Models sidebar draws its own. */
.gsb-slot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  height: var(--sidebar-slot-h);
  box-sizing: border-box;
  padding: 0 var(--space-4);
  border: 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.gsb-slot:hover {
  background: var(--hover-wash);
}

.gsb-slot:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

.gsb-slot-mark {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-sidebar-text), 0.12);
}

.gsb-slot-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  line-height: 1.2;
}

.gsb-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 0 var(--space-2);
}
</style>
