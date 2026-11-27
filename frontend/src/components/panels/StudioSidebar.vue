<script setup>
/**
 * The Studio's left rail (#2819). Its top slot is Media's project selector,
 * the same choice: picking a project here is picking it in Media.
 *
 * With Any project it lists the kinds only (All scenes, Favourites, Cast,
 * Locations, Props), each opening a grid, so nothing long is listed here.
 * In a project (or Not in a project) it lists the items themselves under an
 * All … row per section; a row opens that item's lightbox, and Esc from it
 * goes to its section's grid. The open scene unfolds to its location and cast.
 */
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppButton from "../widgets/AppButton.vue";
import SidebarResizeHandle from "./SidebarResizeHandle.vue";
import SidebarScopeSlot from "./SidebarScopeSlot.vue";
import { listProjects } from "../../api/projects";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useSidebarWidth } from "../../composables/useSidebarWidth";
import { useStudioStore } from "../../stores/useStudioStore";
import {
  KIND_SECTION,
  STUDIO_KINDS,
  kindStyle,
  studioQuery,
} from "../../utils/studioKinds";

const route = useRoute();
const router = useRouter();
const store = useStudioStore();
const { width, narrow } = useSidebarWidth();

const projects = ref([]);
onMounted(async () => {
  try {
    const list = await listProjects();
    projects.value = [...(Array.isArray(list) ? list : [])].sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    );
  } catch (e) {
    console.warn("Couldn't read the projects for the Studio's selector", e);
  }
});

const mode = computed(() => (store.scope === "any" ? "global" : "project"));
const projectId = computed(() =>
  typeof store.scope === "number" ? store.scope : null,
);
const inProject = computed(() => store.scope !== "any");

const section = computed(() => route.query.section || "scenes");
const open = computed(() => route.query.open || "");
const lists = computed(() => store.overview);

function go(patch) {
  router.push({ path: "/studio", query: studioQuery(route.query, patch) });
}

function chooseScope(next) {
  store.setScope(next);
  go({ project: next === "any" ? null : String(next), open: null, via: null });
}

function openSection(name) {
  go({ section: name, open: null, via: null });
}

function openItem(kind, id) {
  go({ section: KIND_SECTION[kind], open: `${kind}:${id}`, via: "sidebar" });
}

const TOP = [
  { id: "scenes", kind: "scene", label: "All scenes", list: "scenes" },
  { id: "favourites", kind: null, label: "Favourites", list: null },
  { id: "cast", kind: "cast", label: "Cast", list: "cast" },
  { id: "locations", kind: "location", label: "Locations", list: "locations" },
  { id: "props", kind: "prop", label: "Props", list: "props" },
];

const favouriteCount = computed(
  () =>
    ["scenes", "cast", "outfits", "locations", "props"]
      .map((key) => lists.value[key].filter((item) => item.favourite).length)
      .reduce((a, b) => a + b, 0),
);

const topRows = computed(() =>
  TOP.map((row) => ({
    ...row,
    icon: row.kind ? STUDIO_KINDS[row.kind].icon : "mdi-heart-outline",
    count: row.list ? lists.value[row.list].length : favouriteCount.value,
    lit: !open.value && section.value === row.id,
  })),
);

const openScene = computed(() => {
  const [kind, id] = open.value.split(":");
  if (kind !== "scene") return null;
  return lists.value.scenes.find((s) => s.id === Number(id)) || null;
});

const peopleById = computed(
  () => new Map(lists.value.cast.map((c) => [c.id, c])),
);
const outfitsById = computed(
  () => new Map(lists.value.outfits.map((o) => [o.id, o])),
);
const locationsById = computed(
  () => new Map(lists.value.locations.map((l) => [l.id, l])),
);

const isOpen = (kind, id) => open.value === `${kind}:${id}`;

/** A project's sections: an All … row, then every item. */
const sections = computed(() => [
  {
    id: "scenes",
    kind: "scene",
    title: "Scenes",
    items: lists.value.scenes.map((s) => ({
      ...s,
      meta: s.cast_ids.length ? `${s.cast_ids.length} cast` : "",
    })),
  },
  {
    id: "cast",
    kind: "cast",
    title: "Cast",
    items: lists.value.cast.map((c) => ({
      ...c,
      meta: c.outfit_ids.length
        ? `${c.outfit_ids.length} ${c.outfit_ids.length === 1 ? "outfit" : "outfits"}`
        : "",
    })),
  },
  {
    id: "locations",
    kind: "location",
    title: "Locations",
    items: lists.value.locations.map((l) => ({
      ...l,
      meta: `${l.picture_ids.length} views`,
    })),
  },
  {
    id: "props",
    kind: "prop",
    title: "Props",
    items: lists.value.props.map((p) => ({
      ...p,
      meta: `${p.picture_ids.length} angles`,
    })),
  },
]);

function thumbOf(kind, item) {
  const id =
    kind === "scene"
      ? item.cover_picture_id
      : kind === "cast"
        ? item.thumbnail_picture_id
        : item.picture_ids?.[0];
  return id ? pictureThumbnailUrl(id) : "";
}
</script>

<template>
  <div
    class="studio-sidebar sidebar-panel"
    :class="{ 'sidebar--narrow': narrow }"
    :style="{ width: `${width}px` }"
  >
    <SidebarResizeHandle />
    <SidebarScopeSlot
      :projects="projects"
      :mode="mode"
      :project-id="projectId"
      read-only
      @choose="chooseScope"
    />
    <nav
      class="studio-sidebar__list sidebar-nav-list sidebar-scroll"
      aria-label="Studio navigation"
    >
      <template v-if="!inProject">
        <button
          v-for="row in topRows"
          :key="row.id"
          type="button"
          class="sidebar-list-item"
          :class="{ active: row.lit }"
          :aria-current="row.lit ? 'page' : undefined"
          :data-testid="`studio-row-${row.id}`"
          @click="openSection(row.id)"
        >
          <span class="sidebar-list-icon" aria-hidden="true">
            <span
              class="studio-kind-tile"
              :style="kindStyle(row.kind)"
              ><v-icon size="16">{{ row.icon }}</v-icon></span
            >
          </span>
          <span class="sidebar-list-label sidebar-list-label-text">{{
            row.label
          }}</span>
          <span v-if="store.loaded" class="sidebar-list-count">{{
            row.count
          }}</span>
        </button>
        <p class="studio-sidebar__hint">
          Pick a project above to work inside it. With Any project, each row
          opens a grid of everything of that kind.
        </p>
      </template>

      <template v-else>
        <button
          type="button"
          class="sidebar-list-item"
          :class="{ active: !open && section === 'favourites' }"
          :aria-current="!open && section === 'favourites' ? 'page' : undefined"
          data-testid="studio-row-favourites"
          @click="openSection('favourites')"
        >
          <span class="sidebar-list-icon" aria-hidden="true"
            ><v-icon size="16">mdi-heart-outline</v-icon></span
          >
          <span class="sidebar-list-label sidebar-list-label-text"
            >Favourites</span
          >
          <span v-if="store.loaded" class="sidebar-list-count">{{
            favouriteCount
          }}</span>
        </button>
        <template v-for="sec in sections" :key="sec.id">
          <div class="sidebar-section-header" role="presentation">
            <span
              class="sidebar-section-title studio-section-title"
              :style="kindStyle(sec.kind)"
              >{{ sec.title }}</span
            >
          </div>
          <button
            type="button"
            class="sidebar-list-item"
            :class="{ active: !open && section === sec.id }"
            :aria-current="!open && section === sec.id ? 'page' : undefined"
            :data-testid="`studio-all-${sec.id}`"
            @click="openSection(sec.id)"
          >
            <span class="sidebar-list-icon" aria-hidden="true">
              <span
                class="studio-kind-tile"
                :style="kindStyle(sec.kind)"
                ><v-icon size="16">{{ STUDIO_KINDS[sec.kind].icon }}</v-icon></span
              >
            </span>
            <span class="sidebar-list-label sidebar-list-label-text">{{
              STUDIO_KINDS[sec.kind].all
            }}</span>
            <span class="sidebar-list-count">{{ sec.items.length }}</span>
          </button>
          <template v-for="item in sec.items" :key="`${sec.kind}:${item.id}`">
            <button
              type="button"
              class="sidebar-list-item studio-item-row"
              :class="{ active: isOpen(sec.kind, item.id) }"
              :aria-current="isOpen(sec.kind, item.id) ? 'page' : undefined"
              :data-testid="`studio-item-${sec.kind}-${item.id}`"
              @click="openItem(sec.kind, item.id)"
            >
              <span class="sidebar-list-icon" aria-hidden="true">
                <img
                  v-if="thumbOf(sec.kind, item)"
                  class="studio-thumb"
                  :src="thumbOf(sec.kind, item)"
                  alt=""
                  loading="lazy"
                />
                <v-icon v-else size="16">{{ STUDIO_KINDS[sec.kind].icon }}</v-icon>
              </span>
              <span class="sidebar-list-label sidebar-list-label-text">{{
                item.name
              }}</span>
              <span class="studio-meta">{{ item.meta }}</span>
            </button>
            <!-- The open scene unfolds to its location and cast. -->
            <template
              v-if="sec.kind === 'scene' && openScene && openScene.id === item.id"
            >
              <button
                v-if="locationsById.get(item.location_set_id)"
                type="button"
                class="sidebar-list-item studio-child"
                @click="openItem('location', item.location_set_id)"
              >
                <span class="sidebar-list-icon" aria-hidden="true">
                  <span class="studio-kind-tile" :style="kindStyle('location')"
                    ><v-icon size="16">{{ STUDIO_KINDS.location.icon }}</v-icon></span
                  >
                </span>
                <span class="sidebar-list-label sidebar-list-label-text">{{
                  locationsById.get(item.location_set_id).name
                }}</span>
                <span class="studio-meta">location</span>
              </button>
              <button
                v-for="cid in item.cast_ids"
                :key="`cast-${cid}`"
                type="button"
                class="sidebar-list-item studio-child"
                @click="openItem('cast', cid)"
              >
                <span class="sidebar-list-icon" aria-hidden="true">
                  <span class="studio-kind-tile" :style="kindStyle('cast')"
                    ><v-icon size="16">{{ STUDIO_KINDS.cast.icon }}</v-icon></span
                  >
                </span>
                <span class="sidebar-list-label sidebar-list-label-text">{{
                  peopleById.get(cid)?.name ?? "Someone not in this project"
                }}</span>
              </button>
            </template>
            <!-- A person's wardrobe, under them. -->
            <template v-if="sec.kind === 'cast' && isOpen('cast', item.id)">
              <button
                v-for="oid in item.outfit_ids"
                :key="`outfit-${oid}`"
                type="button"
                class="sidebar-list-item studio-child"
                :class="{ active: isOpen('outfit', oid) }"
                @click="openItem('outfit', oid)"
              >
                <span class="sidebar-list-icon" aria-hidden="true">
                  <span class="studio-kind-tile" :style="kindStyle('outfit')"
                    ><v-icon size="16">{{ STUDIO_KINDS.outfit.icon }}</v-icon></span
                  >
                </span>
                <span class="sidebar-list-label sidebar-list-label-text">{{
                  outfitsById.get(oid)?.name
                }}</span>
              </button>
            </template>
          </template>
        </template>
      </template>
    </nav>

    <div class="sidebar-footer-band">
      <AppButton
        class="studio-sidebar__new"
        variant="primary"
        icon-left="plus"
        block
        data-testid="studio-new-scene"
        @click="go({ section: 'scenes', open: null, via: null, new: '1' })"
        >New scene</AppButton
      >
    </div>
  </div>
</template>

<style scoped src="./SidebarNav.css"></style>
<style scoped>
.studio-sidebar__list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 0 var(--space-2);
}

.studio-sidebar__hint {
  margin: var(--space-4) var(--space-4) 0;
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-sidebar-text), 0.7);
}

.studio-child {
  --depth: 1;
}

/* A category is its icon in the kind's colour, no tile behind it; a project
   is a round dot. Scenes and Favourites have no kind colour, so they are ink. */
.studio-kind-tile {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
}

.studio-kind-tile .v-icon {
  color: var(--kind-tile, rgb(var(--v-theme-sidebar-text))) !important;
}

.studio-section-title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.studio-section-title::before {
  content: "";
  width: var(--space-3);
  height: var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--kind-tile, transparent);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-sidebar-text), 0.4);
}

.studio-thumb {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  border-radius: 25%;
  object-fit: cover;
}

.studio-meta {
  flex: none;
  margin-left: var(--space-2);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-sidebar-text), 0.7);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.studio-sidebar__new {
  height: var(--control-h-bar);
}
</style>
