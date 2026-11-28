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
 *
 * Every row has a menu (right-click, the Menu key or Shift+F10) holding what
 * its grid's toolbar or its lightbox can do, as Media's sidebar rows do.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AppButton from "../widgets/AppButton.vue";
import SidebarFooterBand from "./SidebarFooterBand.vue";
import Tooltip from "../widgets/Tooltip.vue";
import LocationAnglesDialog from "../io/LocationAnglesDialog.vue";
import SidebarResizeHandle from "./SidebarResizeHandle.vue";
import SidebarScopeSlot from "./SidebarScopeSlot.vue";
import { listProjects } from "../../api/projects";
import { pictureThumbnailUrl } from "../../api/pictures";
import {
  deleteScene,
  setStudioFavourite,
  stopUsingSetInStudio,
} from "../../api/studio";
import { useConfirm } from "../../composables/useConfirm";
import { useSidebarWidth } from "../../composables/useSidebarWidth";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useStudioStore } from "../../stores/useStudioStore";
import { errorMessage } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import {
  KIND_SECTION,
  SECTION_KIND,
  STUDIO_KINDS,
  kindStyle,
  studioQuery,
} from "../../utils/studioKinds";

const emit = defineEmits(["item-sheets", "new-person"]);

const route = useRoute();
const router = useRouter();
const store = useStudioStore();
const noticeStore = useNoticeStore();
const { confirm } = useConfirm();
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

// The band's adds beside Scene. A cast member is a person, made with Media's
// editor (App hands it on) and opened here once saved; a location or a prop
// is a set listed in the Studio, so those open the grid's Add a set….
const footerVerbs = [
  {
    id: "cast",
    label: "New cast member…",
    icon: STUDIO_KINDS.cast.icon,
    mark: true,
    run: () =>
      emit("new-person", projectId.value, async (person) => {
        await store.refresh();
        openItem("cast", person.id);
      }),
  },
  {
    id: "location",
    label: "Add a location…",
    icon: STUDIO_KINDS.location.icon,
    mark: true,
    run: () => go({ section: "locations", open: null, via: null, add: "1" }),
  },
  {
    id: "prop",
    label: "Add a prop…",
    icon: STUDIO_KINDS.prop.icon,
    mark: true,
    run: () => go({ section: "props", open: null, via: null, add: "1" }),
  },
];

const TOP = [
  { id: "scenes", kind: "scene", label: "All scenes", list: "scenes" },
  { id: "favourites", kind: null, label: "Favourites", list: null },
  { id: "cast", kind: "cast", label: "Cast", list: "cast" },
  { id: "wardrobe", kind: "outfit", label: "Wardrobe", list: "outfits" },
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
    id: "wardrobe",
    kind: "outfit",
    title: "Wardrobe",
    // Every outfit, a person's too: they are under that person as well.
    items: lists.value.outfits.map((o) => ({
      ...o,
      meta: peopleById.value.get(o.character_id)?.name ?? "",
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

// ── The row menu ──────────────────────────────────────────────────────────
// A teleported `.ctx-menu` at the pointer, as Media's sidebar draws its own.
// A target is `{ section }` for a kind's row or `{ kind, id }` for an item.
const SEP = "sep";
const menu = ref(null); // { target, x, y }
const menuEl = ref(null);
const menuH = ref(0);
const menuW = ref(0);
let menuTrigger = null;

function sectionEntries(id) {
  const kind = SECTION_KIND[id];
  const show = {
    key: "show",
    label: kind ? `Show ${STUDIO_KINDS[kind].all.toLowerCase()}` : "Show favourites",
    icon: "mdi-view-grid-outline",
    run: () => openSection(id),
  };
  if (id === "scenes") {
    return [show, SEP, { key: "new-scene", label: "New scene…", icon: "mdi-plus", run: () => newScene() }];
  }
  if (kind === "outfit" || kind === "location" || kind === "prop") {
    return [
      show,
      SEP,
      {
        key: "add-set",
        label: "Add a set…",
        icon: "mdi-playlist-plus",
        run: () => go({ section: id, open: null, via: null, add: "1" }),
      },
      {
        key: "draw-sheets",
        label: `Draw ${STUDIO_KINDS[kind].label.toLowerCase()} sheets…`,
        icon: "mdi-draw",
        run: () => emit("item-sheets", { kind }),
      },
    ];
  }
  return [show];
}

function itemEntries(kind, id) {
  const item = store.byKey.get(`${kind}:${id}`);
  if (!item) return [];
  const entries = [
    { key: "open", label: "Open", icon: "mdi-open-in-app", run: () => openItem(kind, id) },
    {
      key: "favourite",
      label: item.favourite ? "Remove from favourites" : "Add to favourites",
      icon: item.favourite ? "mdi-heart-off-outline" : "mdi-heart-outline",
      run: () => toggleFavourite(item),
    },
    SEP,
  ];
  if (kind === "scene") {
    entries.push({
      key: "delete",
      label: "Delete the scene…",
      icon: "mdi-delete-outline",
      danger: true,
      run: () => removeScene(item),
    });
    return entries;
  }
  if (kind === "cast") {
    entries.push({
      key: "new-outfit",
      label: "New outfit…",
      icon: "mdi-hanger",
      run: () => emit("item-sheets", { kind: "outfit", character: { id: item.id, name: item.name } }),
    });
    return entries;
  }
  if (kind === "location") {
    entries.push(
      {
        key: "new-scene-here",
        label: "New scene here…",
        icon: "mdi-movie-open-plus-outline",
        run: () => newScene(item.id),
      },
      {
        key: "more-angles",
        label: "More angles…",
        icon: "mdi-rotate-orbit",
        reason: item.picture_ids.length ? "" : "No pictures yet to start from.",
        run: (trigger) => {
          anglesTrigger = trigger;
          anglesOf.value = item;
        },
      },
    );
  }
  const wearer = kind === "outfit" ? store.byKey.get(`cast:${item.character_id}`) : null;
  entries.push(
    {
      key: "new-sheets",
      label: "New sheets…",
      icon: "mdi-draw",
      run: () =>
        emit("item-sheets", {
          kind,
          character: wearer ? { id: wearer.id, name: wearer.name } : null,
        }),
    },
    SEP,
    {
      key: "stop-using",
      label: "Stop using in the Studio",
      icon: "mdi-playlist-remove",
      run: () => stopUsing(kind, item),
    },
  );
  return entries;
}

const entriesFor = (target) =>
  target.section ? sectionEntries(target.section) : itemEntries(target.kind, target.id);

const menuEntries = computed(() => (menu.value ? entriesFor(menu.value.target) : []));

async function showMenu(target, x, y, trigger) {
  if (!entriesFor(target).length) return false;
  menuTrigger = trigger;
  menu.value = { target, x, y };
  // Its size depends on the target, so the clamp and flip read it once drawn.
  await nextTick();
  menuH.value = menuEl.value?.offsetHeight ?? 0;
  menuW.value = menuEl.value?.offsetWidth ?? 0;
  return true;
}

function onRowContext(target, event) {
  if (!entriesFor(target).length) return;
  event.preventDefault();
  showMenu(target, event.clientX, event.clientY, event.currentTarget);
}

// The Menu key and Shift+F10 on a focused row: its menu under the row, with
// the first entry focused (as Media's review rows do).
async function onRowKey(target, event) {
  if (event.key !== "ContextMenu" && !(event.key === "F10" && event.shiftKey)) return;
  event.preventDefault();
  const row = event.currentTarget;
  const at = row.getBoundingClientRect();
  if (await showMenu(target, at.left, at.bottom, row)) {
    menuEl.value?.querySelector(".ctx-item:not(:disabled)")?.focus();
  }
}

function closeMenu({ refocus = false } = {}) {
  if (!menu.value) return;
  menu.value = null;
  const trigger = menuTrigger;
  menuTrigger = null;
  if (refocus) trigger?.focus?.();
}

const menuStyle = computed(() => {
  if (!menu.value) return {};
  const { x, y } = menu.value;
  const left = `${Math.max(8, Math.min(x, window.innerWidth - menuW.value - 8))}px`;
  return y + menuH.value > window.innerHeight - 8
    ? { left, bottom: `${window.innerHeight - y}px` }
    : { left, top: `${y}px` };
});

function runEntry(entry) {
  if (entry.reason) return;
  // A verb that opens a dialog hands focus back to the row it came from.
  const trigger = menuTrigger;
  closeMenu();
  trigger?.focus?.();
  entry.run(trigger);
}

function onMenuKey(event) {
  if (event.key === "Tab") {
    event.preventDefault();
    closeMenu({ refocus: true });
    return;
  }
  onMenuKeydown(event);
}

function onOutside() {
  closeMenu();
}

function onEscape(event) {
  if (!menu.value || event.key !== "Escape") return;
  // Before the lightbox's own Esc, which would close it.
  event.stopImmediatePropagation();
  event.preventDefault();
  closeMenu({ refocus: true });
}

// Back, Forward or a lightbox's own links move on: the menu was for there.
watch(() => route.fullPath, () => closeMenu());

document.addEventListener("mousedown", onOutside);
document.addEventListener("keydown", onEscape, true);
onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onOutside);
  document.removeEventListener("keydown", onEscape, true);
});

// ── What the menu does ────────────────────────────────────────────────────
// New scene and Add a set open the grid's own dialogs, by a one-shot in the
// route as the footer's New scene does.
function newScene(locationId = null) {
  go({
    section: "scenes",
    open: null,
    via: null,
    new: locationId == null ? "1" : `location:${locationId}`,
  });
}

async function toggleFavourite(item) {
  try {
    await setStudioFavourite(item.kind, item.id, !item.favourite);
    await store.refresh();
  } catch (e) {
    noticeStore.error(`Couldn't change the favourite. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-favourite",
    });
  }
}

async function removeScene(scene) {
  const ok = await confirm({
    title: "Delete this scene?",
    message: `“${scene.name}” and its cast, props and setting go. Its takes stay in the library.`,
    confirmLabel: "Delete",
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteScene(scene.id);
    if (isOpen("scene", scene.id)) go({ open: null, via: null });
    await store.refresh();
  } catch (e) {
    noticeStore.error(`Couldn't delete the scene. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-scene-delete",
    });
  }
}

async function stopUsing(kind, item) {
  try {
    await stopUsingSetInStudio(item.id);
    if (isOpen(kind, item.id)) go({ open: null, via: null });
    await store.refresh();
  } catch (e) {
    noticeStore.error(`Couldn't take it out of the Studio. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-stop-using",
    });
  }
}

// More angles…, the location lightbox's dialog, opened from its row. Opened
// from code, so focus goes back to the row by hand on close.
const anglesOf = ref(null);
let anglesTrigger = null;

function closeAngles() {
  anglesOf.value = null;
  const trigger = anglesTrigger;
  anglesTrigger = null;
  nextTick(() => trigger?.focus?.());
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
          @contextmenu="onRowContext({ section: row.id }, $event)"
          @keydown="onRowKey({ section: row.id }, $event)"
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
          @contextmenu="onRowContext({ section: 'favourites' }, $event)"
          @keydown="onRowKey({ section: 'favourites' }, $event)"
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
            @contextmenu="onRowContext({ section: sec.id }, $event)"
            @keydown="onRowKey({ section: sec.id }, $event)"
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
              @contextmenu="onRowContext({ kind: sec.kind, id: item.id }, $event)"
              @keydown="onRowKey({ kind: sec.kind, id: item.id }, $event)"
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
                @contextmenu="
                  onRowContext({ kind: 'location', id: item.location_set_id }, $event)
                "
                @keydown="onRowKey({ kind: 'location', id: item.location_set_id }, $event)"
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
                @contextmenu="onRowContext({ kind: 'cast', id: cid }, $event)"
                @keydown="onRowKey({ kind: 'cast', id: cid }, $event)"
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
                @contextmenu="onRowContext({ kind: 'outfit', id: oid }, $event)"
                @keydown="onRowKey({ kind: 'outfit', id: oid }, $event)"
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

    <SidebarFooterBand :verbs="footerVerbs">
      <AppButton
        class="studio-sidebar__new"
        variant="primary"
        icon-left="plus"
        data-testid="studio-new-scene"
        @click="go({ section: 'scenes', open: null, via: null, new: '1' })"
        >Scene</AppButton
      >
    </SidebarFooterBand>

    <LocationAnglesDialog
      :open="!!anglesOf"
      :location="anglesOf"
      @close="closeAngles"
      @done="store.refresh()"
    />

    <Teleport to="body">
      <div
        v-if="menu && menuEntries.length"
        ref="menuEl"
        class="ctx-menu studio-ctx-menu"
        role="menu"
        tabindex="-1"
        :style="menuStyle"
        data-testid="studio-menu"
        @contextmenu.prevent
        @mousedown.stop
        @keydown="onMenuKey"
      >
        <template v-for="(entry, i) in menuEntries" :key="i">
          <div v-if="entry === SEP" class="ctx-sep" role="separator"></div>
          <button
            v-else
            type="button"
            role="menuitem"
            class="ctx-item"
            :class="{ 'ctx-item--danger': entry.danger }"
            :disabled="!!entry.reason"
            :data-verb="entry.key"
            @click="runEntry(entry)"
          >
            <v-icon class="ctx-icon" aria-hidden="true">{{ entry.icon }}</v-icon>
            <span class="ctx-label-text">{{ entry.label }}</span>
            <Tooltip v-if="entry.reason" :text="entry.reason" activator="parent" />
          </button>
        </template>
      </div>
    </Teleport>
  </div>
</template>

<style scoped src="./SidebarNav.css"></style>
<style scoped>
/* The scope dropdown is this workspace's header slot, so it takes the slot's
   height and top edge, not the 32px framed band it wears inside Media's list;
   otherwise a switch to Studio moves the sidebar's top rule. */
.studio-sidebar > .scope-slot {
  height: var(--sidebar-slot-h);
  border-top: 0;
}

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

.studio-ctx-menu {
  position: fixed;
  z-index: var(--z-overlay);
  min-width: 140px;
  max-width: 260px;
  user-select: none;
}
</style>
