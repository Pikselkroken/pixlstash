<template>
  <div class="studio-pane studio-view" data-testid="studio-view">
    <StudioScene
      v-if="openKind === 'scene'"
      :key="`scene-${openId}`"
      :scene-id="openId"
      :scenes="store.overview.scenes"
      :back-label="backLabel"
      @close="closeLightbox"
      @go="openItem"
      @changed="store.refresh()"
      @item-sheets="emit('item-sheets', $event)"
    />
    <StudioItem
      v-else-if="openKind"
      :key="`${openKind}-${openId}`"
      :kind="openKind"
      :item-id="openId"
      :back-label="backLabel"
      @close="closeLightbox"
      @go="openItem"
      @changed="store.refresh()"
      @new-scene="newScene"
      @item-sheets="emit('item-sheets', $event)"
    />
    <template v-else>
      <div class="studio-toolbar">
        <span class="studio-title">{{ sectionTitle }}</span>
        <span class="studio-sub">{{ cards.length }}</span>
        <template v-if="section === 'scenes'">
          <AppSelect
            :model-value="optionValue(withPerson)"
            class="studio-filter"
            compact
            hide-label
            label="With"
            :options="personOptions"
            data-testid="studio-filter-person"
            @update:model-value="withPerson = fromOption($event)"
          />
          <AppSelect
            :model-value="optionValue(atLocation)"
            class="studio-filter"
            compact
            hide-label
            label="At"
            :options="locationOptions"
            data-testid="studio-filter-location"
            @update:model-value="atLocation = fromOption($event)"
          />
        </template>
        <AppSelect
          v-if="section === 'wardrobe'"
          :model-value="optionValue(wornBy)"
          class="studio-filter"
          compact
          hide-label
          label="Worn by"
          :options="wearerOptions"
          data-testid="studio-filter-wearer"
          @update:model-value="wornBy = $event === 'nobody' ? 'nobody' : fromOption($event)"
        />
        <Segmented
          v-if="section === 'favourites'"
          v-model="favouriteKind"
          :options="favouriteKindOptions"
          aria-label="Show favourites of one kind"
        />
        <span class="studio-spacer"></span>
        <AppButton
          v-if="SET_SECTIONS[section]"
          variant="ghost"
          icon-left="playlist-plus"
          data-testid="studio-add-set"
          @click="addSetOpen = true"
          >Add a set…</AppButton
        >
        <AppButton
          v-if="SET_SECTIONS[section]"
          variant="secondary"
          icon-left="draw"
          data-testid="studio-draw-sheets"
          @click="emit('item-sheets', { kind: SET_SECTIONS[section].kind })"
          >{{ SET_SECTIONS[section].draw }}</AppButton
        >
        <AppButton
          v-if="section === 'scenes'"
          variant="primary"
          icon-left="plus"
          @click="newScene()"
          >New scene</AppButton
        >
      </div>
      <p v-if="store.error" class="studio-error" role="alert">
        {{ store.error }}
      </p>
      <div
        ref="scrollEl"
        class="studio-scroll studio-scroll--tiles"
        @pointerdown="pressedBackground = isBackground($event.target)"
        @click="onBackgroundClick"
      >
        <div v-if="store.loaded && !cards.length" class="studio-empty">
          <v-icon size="32">{{ emptyIcon }}</v-icon>
          <span>{{ emptyText }}</span>
        </div>
        <div
          v-else
          ref="gridEl"
          class="studio-grid studio-grid--tiles"
          role="list"
          @keydown="onGridKey"
        >
          <div
            v-for="(card, index) in cards"
            :key="cardKey(card)"
            role="listitem"
            class="studio-cell"
            :class="{ 'studio-cell--selected': isSelected(card) }"
          >
            <!-- Click selects (Ctrl adds, Shift ranges), double-click or
                 Enter opens, right-click is the menu: the picture grid's
                 gestures. -->
            <button
              type="button"
              class="studio-card"
              :aria-pressed="isSelected(card) ? 'true' : 'false'"
              :data-key="cardKey(card)"
              :data-testid="`studio-card-${card.kind}-${card.id}`"
              @click="onCardClick(card, index, $event)"
              @dblclick="openItem(card.kind, card.id)"
              @keydown.enter.prevent="openItem(card.kind, card.id)"
              @contextmenu.prevent="openCardMenu(card, $event)"
            >
              <span
                class="studio-card__ph studio-tile-media"
                :class="`studio-tile-media--${card.tile.shape}`"
              >
                <span
                  v-for="panel in card.tile.panels"
                  :key="panel.key"
                  class="studio-tile-panel"
                  :class="{ 'studio-tile-panel--empty': !panel.src }"
                  :style="{ gridArea: panel.area }"
                  :data-testid="panel.src ? `studio-panel-${panel.key}` : undefined"
                >
                  <img v-if="panel.src" :src="panel.src" alt="" loading="lazy" />
                  <span v-if="panel.label" class="studio-tile-label">{{
                    panel.label
                  }}</span>
                </span>
                <span v-if="card.tile.chips.length" class="studio-tile-chips">
                  <img
                    v-for="chip in card.tile.chips"
                    :key="chip"
                    :src="chip"
                    alt=""
                    loading="lazy"
                    @error="$event.target.hidden = true"
                  />
                </span>
                <span
                  v-if="!card.tile.panels.length && !card.tile.prompt"
                  class="studio-card__empty"
                  ><v-icon size="28">{{ STUDIO_KINDS[card.kind].icon }}</v-icon></span
                >
                <span
                  v-if="section === 'favourites'"
                  class="studio-badge"
                  :style="kindStyle(card.kind)"
                >
                  <span class="studio-tile"
                    ><v-icon size="12">{{ STUDIO_KINDS[card.kind].icon }}</v-icon></span
                  >{{ STUDIO_KINDS[card.kind].label }}
                </span>
              </span>
              <span class="studio-card__name studio-card__line"
                ><span class="studio-card__title">{{ card.name }}</span
                ><span class="studio-card__meta">{{ card.meta }}</span></span
              >
            </button>
            <span
              v-if="card.tile.prompt"
              class="studio-tile-prompt"
              :class="`studio-tile-prompt--${card.tile.prompt.where}`"
            >
              <span>{{ card.tile.prompt.text }}</span>
              <AppButton
                variant="secondary"
                size="sm"
                icon-left="draw"
                :data-testid="`studio-draw-sheet-${card.id}`"
                @click="drawSheet(card, $event)"
                >Draw a character sheet…</AppButton
              >
            </span>
            <span class="studio-fav">
              <FavouriteHeart
                variant="cover"
                :on="!!card.favourite"
                :label="card.name"
                :tabindex="0"
                @toggle="toggleFavourite(card)"
              />
            </span>
          </div>
        </div>
      </div>
    </template>

    <Teleport to="body">
      <div
        v-if="cardMenu"
        ref="menuEl"
        class="ctx-menu studio-ctx-menu"
        role="menu"
        tabindex="-1"
        :style="menuStyle"
        data-testid="studio-card-menu"
        @contextmenu.prevent
        @mousedown.stop
        @keydown="onMenuKey"
      >
        <template v-for="(entry, i) in menuEntries" :key="i">
          <div v-if="entry === SEP" class="ctx-sep" role="separator"></div>
          <AddToEntityControl
            v-else-if="entry.key === 'project'"
            type="project"
            placement="right"
            :subject-ids="selectedKeys"
            :membership="projectMembership"
            data-verb="project"
            @selected="onProjectPicked"
          />
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

    <AppDialog
      :open="newOpen"
      title="New scene"
      subtitle="A cast in a location"
      size="sm"
      @close="newOpen = false"
      @accept="createNewScene"
    >
      <div class="studio-form">
        <AppInput
          v-model="newName"
          label="Name"
          placeholder="Rain at the taxi rank"
          autofocus
          data-testid="studio-new-name"
          @enter="createNewScene"
        />
        <AppSelect
          :model-value="optionValue(newLocation)"
          label="Location"
          :options="newLocationOptions"
          data-testid="studio-new-location"
          @update:model-value="newLocation = fromOption($event)"
        />
        <p class="studio-hint">
          {{
            store.scope === "any" || store.scope === "none"
              ? "In no project. Pick a project in the sidebar to make it there."
              : "Made in the project picked in the sidebar."
          }}
        </p>
        <p v-if="newError" class="studio-hint studio-hint--error" role="alert">
          {{ newError }}
        </p>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="newOpen = false">Cancel</AppButton>
        <AppButton
          variant="primary"
          :disabled="!newName.trim()"
          :loading="creating"
          data-testid="studio-new-create"
          @click="createNewScene"
          >Make scene</AppButton
        >
      </template>
    </AppDialog>

    <AppDialog
      :open="addSetOpen"
      :title="`Add ${{ wardrobe: 'an outfit', locations: 'a location' }[section] ?? 'a prop'}`"
      subtitle="From a picture set you already have"
      size="sm"
      @close="addSetOpen = false"
    >
      <div class="studio-form">
        <AppSelect
          :model-value="optionValue(addSetId)"
          label="Picture set"
          :options="setOptions"
          placeholder="Pick a set"
          data-testid="studio-add-set-pick"
          @update:model-value="addSetId = fromOption($event)"
        />
        <p class="studio-hint">
          Its pictures, oldest first, are the {{ section === "locations"
            ? "Wide, Reverse, Left and Right views"
            : "Front, ¾, Side and Back angles" }}, as a sheet cuts them.
        </p>
      </div>
      <template #footer>
        <AppButton variant="secondary" @click="addSetOpen = false">Cancel</AppButton>
        <AppButton
          variant="primary"
          :disabled="addSetId == null"
          @click="addSet"
          >Add</AppButton
        >
      </template>
    </AppDialog>
  </div>
</template>

<script setup>
/**
 * The Studio's centre column (#2819): a grid of one kind (or of favourites)
 * with its filters in the toolbar, and an item's lightbox over it when the
 * route names one (`?open=<kind>:<id>`). The route is the Studio's place, so
 * a reload and Back land where they were; `project` in it is the scope Media
 * shares.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import Tooltip from "../widgets/Tooltip.vue";
import FavouriteHeart from "../widgets/FavouriteHeart.vue";
import Segmented from "../widgets/Segmented.vue";
import StudioItem from "./StudioItem.vue";
import StudioScene from "./StudioScene.vue";
import { characterThumbnailUrl } from "../../api/characters";
import { pictureThumbnailUrl } from "../../api/pictures";
import { listPictureSets } from "../../api/pictureSets";
import {
  createScene,
  listSetInStudio,
  setStudioFavourite,
} from "../../api/studio";
import { useConfirm } from "../../composables/useConfirm";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useStudioStore } from "../../stores/useStudioStore";
import { errorMessage } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import {
  cardKey,
  deleteCard,
  deleteWords,
  duplicateCard,
  duplicateReason,
  setCardProject,
} from "../../utils/studioCardActions";
import {
  ITEM_ANGLES,
  KIND_LIST,
  KIND_SECTION,
  LOCATION_VIEWS,
  SECTION_KIND,
  STUDIO_KINDS,
  fromOption,
  kindStyle,
  optionValue,
  studioQuery,
} from "../../utils/studioKinds";

const emit = defineEmits(["item-sheets"]);

const route = useRoute();
const router = useRouter();
const store = useStudioStore();
const noticeStore = useNoticeStore();
const { confirm } = useConfirm();

const section = computed(() => route.query.section || "scenes");
const openKind = computed(() => String(route.query.open || "").split(":")[0] || "");
const openId = computed(() => Number(String(route.query.open || "").split(":")[1]) || null);

function go(patch, { replace = false } = {}) {
  const to = { path: "/studio", query: studioQuery(route.query, patch) };
  return replace ? router.replace(to) : router.push(to);
}

// The route says the scope on the way in (a reload, a link, the title bar);
// the store holds it for Media too.
function scopeFromRoute() {
  const raw = route.query.project;
  if (raw == null) return "any";
  if (raw === "none") return "none";
  const id = Number(raw);
  return Number.isFinite(id) ? id : "any";
}

watch(
  () => route.query.project,
  () => {
    const want = scopeFromRoute();
    if (want !== store.scope) store.setScope(want);
  },
  { immediate: true },
);
watch(() => store.scope, () => store.refresh());
onMounted(() => store.refresh());

const sectionTitle = computed(() =>
  section.value === "favourites"
    ? "Favourites"
    : (STUDIO_KINDS[SECTION_KIND[section.value]]?.all ?? "All scenes"),
);

// The sections a picture set is listed in, as what it is: Add a set… and
// Draw … sheets… in their toolbars.
const SET_SECTIONS = {
  wardrobe: { kind: "outfit", draw: "Draw outfit sheets…" },
  locations: { kind: "location", draw: "Draw location sheets…" },
  props: { kind: "prop", draw: "Draw prop sheets…" },
};

const backLabel = computed(() => {
  const name = section.value === "favourites" ? "Favourites" : sectionTitle.value;
  return route.query.via === "sidebar" ? name : `Back to ${name.toLowerCase()}`;
});

function closeLightbox() {
  // From a sidebar row, Esc goes to that section's All grid; from a grid, back
  // to the grid it was opened from. Both are the section the route names.
  go({ open: null, via: null });
}

function openItem(kind, id, { fromSidebar = false } = {}) {
  go({
    open: `${kind}:${id}`,
    section:
      section.value === "favourites" && !fromSidebar
        ? "favourites"
        : KIND_SECTION[kind],
    via: fromSidebar ? "sidebar" : null,
  });
}

// ── the grids ─────────────────────────────────────────────────────────────
const withPerson = ref(null);
const atLocation = ref(null);
const favouriteKind = ref("all");
// The Wardrobe's filter: a person's id, "nobody" for clothes of their own,
// or null for every outfit.
const wornBy = ref(null);

const people = computed(() => store.overview.cast);
const personOptions = computed(() => [
  { label: "With anyone", value: "" },
  ...people.value.map((p) => ({ label: `With ${p.name}`, value: String(p.id) })),
]);
const wearerOptions = computed(() => [
  { label: "Worn by anyone", value: "" },
  { label: "Not anyone's", value: "nobody" },
  ...people.value.map((p) => ({ label: `Worn by ${p.name}`, value: String(p.id) })),
]);
const locationOptions = computed(() => [
  { label: "At any location", value: "" },
  ...store.overview.locations.map((l) => ({ label: `At ${l.name}`, value: String(l.id) })),
]);

function thumbOf(kind, item) {
  const id =
    kind === "scene"
      ? (item.cover_picture_id ??
        store.byKey.get(`location:${item.location_set_id}`)?.picture_ids?.[0])
      : kind === "cast"
        ? item.thumbnail_picture_id
        : item.picture_ids?.[0];
  return id ? pictureThumbnailUrl(id) : "";
}

function metaOf(kind, item) {
  if (kind === "scene") {
    const takes = `${item.take_count} ${item.take_count === 1 ? "take" : "takes"}`;
    return item.running ? `${takes} · drawing ${item.running}` : takes;
  }
  if (kind === "cast") {
    const n = item.outfit_ids.length;
    return `${n} ${n === 1 ? "outfit" : "outfits"} · in ${item.scene_count} ${item.scene_count === 1 ? "scene" : "scenes"}`;
  }
  if (kind === "outfit") {
    const who = store.byKey.get(`cast:${item.character_id}`)?.name;
    return who ? `${who}'s outfit` : "Not anyone's";
  }
  return `In ${item.scene_count} ${item.scene_count === 1 ? "scene" : "scenes"}`;
}

// Each card is a small copy of the sheet its kind is drawn on (5:3): a
// person's four headshots two by two and two body shots, an item's four views
// or angles two by two. A scene has no sheet: its latest take, 3:2, with the
// cast's faces on it. Fewer pictures than slots never leaves a hole.
const HEAD_AREAS = ["1 / 1", "1 / 2", "2 / 1", "2 / 2"];

function castTile(item) {
  const heads =
    item.headshot_ids ?? (item.thumbnail_picture_id ? [item.thumbnail_picture_id] : []);
  const bodies = item.body_ids ?? [];
  const panel = (id, area) => ({ key: id, area, src: pictureThumbnailUrl(id) });
  if (!heads.length && !bodies.length) {
    return {
      shape: "sheet",
      panels: [{ key: "empty", area: "1 / 1 / 3 / 5" }],
      chips: [],
      prompt: { where: "all", text: "No pictures yet" },
    };
  }
  const full = heads.length === HEAD_AREAS.length;
  const left = full
    ? heads.map((id, i) => panel(id, HEAD_AREAS[i]))
    : [panel(heads[0] ?? bodies[0], "1 / 1 / 3 / 3")];
  const shown = new Set(left.map((p) => p.key));
  const right = [...bodies, ...heads].filter((id) => !shown.has(id)).slice(0, 2);
  const panels = [
    ...left,
    ...right.map((id, i) =>
      panel(id, right.length === 1 ? "1 / 3 / 3 / 5" : `1 / ${3 + i} / 3 / ${4 + i}`),
    ),
  ];
  if (right.length) return { shape: "sheet", panels, chips: [], prompt: null };
  const one = (item.picture_count ?? 1) <= 1;
  return {
    shape: "sheet",
    panels: [...panels, { key: "empty", area: "1 / 3 / 3 / 5" }],
    chips: [],
    prompt: {
      where: "rest",
      text: one ? "One picture. A sheet fills the other five." : "No full-length pictures yet.",
    },
  };
}

function tileOf(kind, item) {
  if (kind === "cast") return castTile(item);
  if (kind === "scene") {
    const thumb = thumbOf(kind, item);
    return {
      shape: "scene",
      panels: thumb ? [{ key: "cover", area: "1 / 1", src: thumb }] : [],
      chips: item.cast_ids.map((id) => characterThumbnailUrl(id)),
      prompt: null,
    };
  }
  const ids = item.picture_ids ?? [];
  if (ids.length < 4) {
    return {
      shape: "one",
      panels: ids.length ? [{ key: ids[0], area: "1 / 1", src: pictureThumbnailUrl(ids[0]) }] : [],
      chips: [],
      prompt: null,
    };
  }
  const labels = kind === "location" ? LOCATION_VIEWS : ITEM_ANGLES;
  return {
    shape: "quad",
    panels: ids.slice(0, 4).map((id, i) => ({
      key: id,
      area: HEAD_AREAS[i],
      src: pictureThumbnailUrl(id),
      label: labels[i],
    })),
    chips: [],
    prompt: null,
  };
}

const asCard = (kind) => (item) => ({
  ...item,
  kind,
  tile: tileOf(kind, item),
  meta: metaOf(kind, item),
});

function drawSheet(card, event) {
  emit("item-sheets", {
    kind: "character",
    character: {
      id: card.id,
      name: card.name,
      image_count: card.picture_count ?? 0,
      returnFocusTo: event?.currentTarget ?? null,
    },
  });
}

const favourites = computed(() =>
  Object.entries(KIND_LIST).flatMap(([kind, list]) =>
    store.overview[list].filter((item) => item.favourite).map(asCard(kind)),
  ),
);

const favouriteKindOptions = computed(() => {
  const count = (kind) => favourites.value.filter((c) => c.kind === kind).length;
  return [
    { id: "all", label: `All ${favourites.value.length}` },
    ...Object.keys(KIND_LIST)
      .filter((kind) => count(kind) > 0)
      .map((kind) => ({
        id: kind,
        label: `${STUDIO_KINDS[kind].plural} ${count(kind)}`,
      })),
  ];
});

// Unstarring the last of a kind takes its segment away: fall back to All.
watch(favouriteKindOptions, (options) => {
  if (!options.some((o) => o.id === favouriteKind.value)) favouriteKind.value = "all";
});

const cards = computed(() => {
  switch (section.value) {
    case "favourites":
      return favouriteKind.value === "all"
        ? favourites.value
        : favourites.value.filter((c) => c.kind === favouriteKind.value);
    case "cast":
      return store.overview.cast.map(asCard("cast"));
    case "wardrobe":
      return store.overview.outfits
        .filter((o) =>
          wornBy.value == null
            ? true
            : wornBy.value === "nobody"
              ? o.character_id == null
              : o.character_id === wornBy.value,
        )
        .map(asCard("outfit"));
    case "locations":
      return store.overview.locations.map(asCard("location"));
    case "props":
      return store.overview.props.map(asCard("prop"));
    default:
      return store.overview.scenes
        .filter(
          (s) =>
            (withPerson.value == null || s.cast_ids.includes(withPerson.value)) &&
            (atLocation.value == null || s.location_set_id === atLocation.value),
        )
        .map(asCard("scene"));
  }
});

const emptyIcon = computed(
  () =>
    ({
      favourites: "mdi-heart-outline",
      cast: STUDIO_KINDS.cast.icon,
      wardrobe: STUDIO_KINDS.outfit.icon,
      locations: STUDIO_KINDS.location.icon,
      props: STUDIO_KINDS.prop.icon,
    })[section.value] ?? STUDIO_KINDS.scene.icon,
);

const emptyText = computed(
  () =>
    ({
      favourites: "Nothing starred yet. The heart on any card adds it here.",
      cast: "No people here yet. People are made in Media; their outfits are drawn from their lightbox here.",
      wardrobe:
        wornBy.value != null
          ? "No outfit matches this filter."
          : "No outfits yet. Draw outfit sheets, or add a set of pictures of one; a person's own are drawn from their lightbox.",
      locations: "No locations yet. Draw location sheets, or add a set of pictures of one place.",
      props: "No props yet. Draw prop sheets, or add a set of pictures of one.",
    })[section.value] ??
    (withPerson.value != null || atLocation.value != null
      ? "No scene matches these filters."
      : "No scenes yet. A scene is a cast in a location: make one to start."),
);

async function toggleFavourite(card) {
  try {
    await setStudioFavourite(card.kind, card.id, !card.favourite);
    await store.refresh();
  } catch (e) {
    noticeStore.error(`Couldn't change the favourite. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-favourite",
    });
  }
}

// ── Selection ─────────────────────────────────────────────────────────────
// The picture grid's gestures: click selects one, Ctrl/Cmd+click adds or
// takes away, Shift+click takes the run from the first selected; Esc or a
// click on the empty grid clears. Kinds share ids, so a key names both.
const selectedKeys = ref([]);
const scrollEl = ref(null);
const gridEl = ref(null);

const isSelected = (card) => selectedKeys.value.includes(cardKey(card));
const selectedCards = computed(() =>
  cards.value.filter((card) => selectedKeys.value.includes(cardKey(card))),
);

function onCardClick(card, index, event) {
  const key = cardKey(card);
  if (event?.shiftKey) {
    const anchor = cards.value.findIndex((c) => selectedKeys.value.includes(cardKey(c)));
    const from = anchor < 0 ? index : anchor;
    const [start, end] = from <= index ? [from, index] : [index, from];
    selectedKeys.value = cards.value.slice(start, end + 1).map(cardKey);
  } else if (event?.ctrlKey || event?.metaKey) {
    selectedKeys.value = selectedKeys.value.includes(key)
      ? selectedKeys.value.filter((k) => k !== key)
      : [...selectedKeys.value, key];
  } else {
    selectedKeys.value = [key];
  }
}

// What is not on show cannot stay selected: a new section, filter or refresh.
watch(cards, (shown) => {
  const keys = new Set(shown.map(cardKey));
  if (selectedKeys.value.some((k) => !keys.has(k))) {
    selectedKeys.value = selectedKeys.value.filter((k) => keys.has(k));
  }
});

// Decided by where the press started, as on Workflows: a press that slips off
// a card onto the gap is a fumbled pick, not a click on nothing.
let pressedBackground = false;
const isBackground = (target) => target === scrollEl.value || target === gridEl.value;

function onBackgroundClick(event) {
  if (pressedBackground && event.detail > 0) selectedKeys.value = [];
  pressedBackground = false;
}

function onGridKey(event) {
  if (event.key === "Escape" && selectedKeys.value.length && !cardMenu.value) {
    event.preventDefault();
    event.stopPropagation();
    selectedKeys.value = [];
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
    event.preventDefault();
    selectedKeys.value = cards.value.map(cardKey);
  } else if (event.key === "Delete" && selectedCards.value.length) {
    event.preventDefault();
    removeCards(selectedCards.value);
  } else if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
    const key = event.target?.closest?.("[data-key]")?.dataset.key;
    const card = cards.value.find((c) => cardKey(c) === key);
    if (!card) return;
    event.preventDefault();
    const at = event.target.getBoundingClientRect();
    openCardMenu(card, { clientX: at.left, clientY: at.bottom, currentTarget: event.target }, true);
  }
}

// ── The card menu ─────────────────────────────────────────────────────────
// The sidebar row menu's teleported `.ctx-menu`, for the selection: a
// right-click on a card outside it selects that card alone first.
const SEP = "sep";
const cardMenu = ref(null); // { x, y }
const menuEl = ref(null);
const menuSize = ref({ w: 0, h: 0 });
let menuTrigger = null;

async function openCardMenu(card, event, focusFirst = false) {
  if (!isSelected(card)) selectedKeys.value = [cardKey(card)];
  menuTrigger = event.currentTarget ?? null;
  cardMenu.value = { x: event.clientX, y: event.clientY };
  await nextTick();
  menuSize.value = { w: menuEl.value?.offsetWidth ?? 0, h: menuEl.value?.offsetHeight ?? 0 };
  if (focusFirst) menuEl.value?.querySelector(".ctx-item:not(:disabled)")?.focus();
}

function closeMenu({ refocus = false } = {}) {
  if (!cardMenu.value) return;
  cardMenu.value = null;
  const trigger = menuTrigger;
  menuTrigger = null;
  if (refocus) trigger?.focus?.();
}

const menuStyle = computed(() => {
  if (!cardMenu.value) return {};
  const { x, y } = cardMenu.value;
  const left = `${Math.max(8, Math.min(x, window.innerWidth - menuSize.value.w - 8))}px`;
  return y + menuSize.value.h > window.innerHeight - 8
    ? { left, bottom: `${window.innerHeight - y}px` }
    : { left, top: `${y}px` };
});

const menuEntries = computed(() => {
  const picked = selectedCards.value;
  if (!picked.length) return [];
  const one = picked.length === 1 ? picked[0] : null;
  const noCopy = picked.map(duplicateReason).find(Boolean) ?? "";
  const allFavourite = picked.every((c) => c.favourite);
  const entries = [];
  if (one) {
    entries.push({ key: "open", label: "Open", icon: "mdi-open-in-app", run: () => openItem(one.kind, one.id) });
  }
  entries.push(
    {
      key: "favourite",
      label: allFavourite ? "Remove from favourites" : "Add to favourites",
      icon: allFavourite ? "mdi-heart-off-outline" : "mdi-heart-outline",
      run: () => setFavourites(picked, !allFavourite),
    },
    {
      key: "duplicate",
      label: one ? "Duplicate" : `Duplicate ${picked.length}`,
      icon: "mdi-content-copy",
      reason: noCopy,
      run: () => duplicateCards(picked),
    },
    { key: "project" },
    SEP,
    {
      key: "delete",
      label: one ? "Delete…" : `Delete ${picked.length}…`,
      icon: "mdi-delete-outline",
      danger: true,
      run: () => removeCards(picked),
    },
  );
  return entries;
});

// Each selected card in each project it is in, for the Project flyout's ticks.
const projectMembership = computed(() => {
  const members = {};
  for (const card of selectedCards.value) {
    for (const pid of card.project_ids ?? []) {
      (members[`project-${pid}`] ??= new Set()).add(cardKey(card));
    }
  }
  return members;
});

function runEntry(entry) {
  if (entry.reason) return;
  closeMenu({ refocus: true });
  entry.run();
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
  if (!cardMenu.value || event.key !== "Escape") return;
  event.stopImmediatePropagation();
  event.preventDefault();
  closeMenu({ refocus: true });
}

watch(() => route.fullPath, () => closeMenu());
document.addEventListener("mousedown", onOutside);
document.addEventListener("keydown", onEscape, true);
onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onOutside);
  document.removeEventListener("keydown", onEscape, true);
});

// ── What the menu does ────────────────────────────────────────────────────
/** Run `act` on each card, then refresh; one notice names what failed. */
async function eachCard(picked, act, failure) {
  const failed = [];
  for (const card of picked) {
    try {
      await act(card);
    } catch (e) {
      console.warn(`Studio ${failure}`, cardKey(card), e);
      failed.push(`${card.name}: ${errorMessage(e, "Please try again.")}`);
    }
  }
  await store.refresh();
  if (failed.length) {
    noticeStore.error(`Couldn't ${failure} ${failed.join("; ")}`, { key: `studio-${failure}` });
  }
}

function setFavourites(picked, on) {
  return eachCard(
    picked.filter((card) => !!card.favourite !== on),
    (card) => setStudioFavourite(card.kind, card.id, on),
    "change the favourite.",
  );
}

async function duplicateCards(picked) {
  const taken = new Set(cards.value.map((c) => c.name));
  const made = [];
  await eachCard(
    picked,
    async (card) => {
      const { id, name } = await duplicateCard(card, taken);
      taken.add(name);
      made.push(`${card.kind}:${id}`);
    },
    "duplicate.",
  );
  // The copies are the selection, so the next gesture is on them.
  if (made.length) selectedKeys.value = made;
}

async function removeCards(picked) {
  if (!picked.length || !(await confirm(deleteWords(picked)))) return;
  const gone = new Set(picked.map(cardKey));
  await eachCard(picked, deleteCard, "delete.");
  selectedKeys.value = selectedKeys.value.filter((k) => !gone.has(k));
}

function onProjectPicked({ projectId, action }) {
  if (projectId == null) return;
  const picked = selectedCards.value;
  return eachCard(
    picked,
    (card) => setCardProject(card, projectId, action === "added"),
    "change the project.",
  );
}

// ── New scene ─────────────────────────────────────────────────────────────
const newOpen = ref(false);
const newName = ref("");
const newLocation = ref(null);
const newError = ref("");
const creating = ref(false);
const newLocationOptions = computed(() => [
  { label: "No location yet", value: "" },
  ...store.overview.locations.map((l) => ({ label: l.name, value: String(l.id) })),
]);

function newScene(locationId = null) {
  newName.value = "";
  newLocation.value = locationId;
  newError.value = "";
  newOpen.value = true;
}

// New scene from the sidebar arrives as `?new=1`, or `?new=location:<id>`
// from a location's row menu, a one-shot.
watch(
  () => route.query.new,
  (value) => {
    if (!value) return;
    go({ new: null }, { replace: true });
    const [kind, id] = String(value).split(":");
    newScene(kind === "location" ? Number(id) || null : null);
  },
  { immediate: true },
);

async function createNewScene() {
  const name = newName.value.trim();
  if (!name || creating.value) return;
  creating.value = true;
  newError.value = "";
  try {
    const scene = await createScene({
      name,
      location_set_id: newLocation.value,
      project_id: typeof store.scope === "number" ? store.scope : null,
    });
    newOpen.value = false;
    await store.refresh();
    go({ section: "scenes", open: `scene:${scene.id}`, via: null });
  } catch (e) {
    newError.value = `Couldn't make the scene. ${errorMessage(e, "Please try again.")}`;
  } finally {
    creating.value = false;
  }
}

// ── Add a set as a location or a prop ─────────────────────────────────────
const addSetOpen = ref(false);
const addSetId = ref(null);
const allSets = ref([]);

watch(addSetOpen, async (open) => {
  if (!open) return;
  addSetId.value = null;
  try {
    const sets = await listPictureSets();
    allSets.value = Array.isArray(sets) ? sets : (sets?.picture_sets ?? []);
  } catch (e) {
    console.warn("Couldn't read the picture sets for Add a set", e);
    allSets.value = [];
  }
});

// Add a set… from a Locations or Props row menu: `?add=1`, a one-shot.
watch(
  () => route.query.add,
  (value) => {
    if (!value) return;
    go({ add: null }, { replace: true });
    addSetOpen.value = true;
  },
  { immediate: true },
);

const setOptions = computed(() => {
  const used = new Set(
    ["outfits", "locations", "props"].flatMap((list) =>
      store.overview[list].map((item) => item.id),
    ),
  );
  return [
    { label: "Pick a set", value: "", disabled: true },
    ...allSets.value
      .filter((s) => !used.has(s.id))
      .map((s) => ({ label: s.name, value: String(s.id) })),
  ];
});

async function addSet() {
  const kind = SET_SECTIONS[section.value]?.kind ?? "prop";
  try {
    await listSetInStudio(addSetId.value, kind);
    addSetOpen.value = false;
    await store.refresh();
  } catch (e) {
    noticeStore.error(`Couldn't add that set. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-add-set",
    });
  }
}
</script>

<style scoped src="./Studio.css"></style>
<style scoped>
.studio-view {
  position: relative;
}

.studio-filter {
  width: 180px;
}

.studio-cell {
  position: relative;
  min-width: 0;
}

/* Selected: the workflow card's wash and ring, on the picture. */
.studio-cell--selected .studio-card__ph::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: var(--z-raised);
  border-radius: inherit;
  background: var(--active-wash);
  box-shadow: var(--selection-ring);
  pointer-events: none;
}

/* The sidebar row menu's frame (StudioSidebar.vue). */
.studio-ctx-menu {
  position: fixed;
  z-index: var(--z-overlay);
  min-width: 160px;
  max-width: 280px;
  user-select: none;
}

.studio-scroll--tiles {
  padding: var(--space-4);
}

.studio-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.studio-hint--error {
  color: rgb(var(--v-theme-error));
}

/* ── the sheet tiles ──────────────────────────────────────────────────────
   Two across at 1280px, four at 1920px; 8px between cards, 2px between the
   panels of one card (the seam of a sheet, so wider reads as two items). */
.studio-grid--tiles {
  grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
  gap: var(--space-4) var(--space-3);
}

.studio-tile-media {
  display: grid;
  gap: var(--space-1);
  aspect-ratio: 5 / 3;
  background: rgb(var(--v-theme-background));
}

.studio-tile-media--sheet {
  grid-template-columns: repeat(4, 1fr);
  grid-template-rows: 1fr 1fr;
}

.studio-tile-media--quad {
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
}

.studio-tile-media--one {
  grid-template-columns: 1fr;
  background: rgb(var(--v-theme-surface));
}

.studio-tile-media--scene {
  grid-template-columns: 1fr;
  aspect-ratio: 3 / 2;
  background: rgb(var(--v-theme-surface));
}

.studio-tile-panel {
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.studio-tile-panel--empty {
  background: rgb(var(--v-theme-surface));
}

.studio-tile-label {
  position: absolute;
  left: var(--space-2);
  bottom: var(--space-2);
  display: flex;
  align-items: center;
  height: var(--badge-size);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--scrim-photo-strong);
  color: rgb(var(--v-theme-on-secondary));
  font-size: var(--text-2xs);
}

/* The cast's faces on a scene's take. */
.studio-tile-chips {
  position: absolute;
  left: var(--space-3);
  bottom: var(--space-3);
  z-index: var(--z-raised);
  display: flex;
  gap: var(--space-2);
}

.studio-card__ph .studio-tile-chips img {
  width: 40px;
  height: 48px;
  border-radius: var(--radius-sm);
  box-shadow: 0 0 0 1px rgba(var(--v-theme-on-secondary), 0.55);
}

/* Name and counts on one line under the card. */
.studio-card__line {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
}

.studio-card__title {
  flex: none;
  max-width: 70%;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: var(--weight-semibold);
}

.studio-card__line .studio-card__meta {
  display: inline;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: var(--text-xs);
}

/* A person short of pictures: what is missing and the way to draw it, over
   the empty half (or the whole card). A sibling of the card's button, not
   inside it; clicks off the button fall through to the card. */
.studio-tile-prompt {
  position: absolute;
  top: 0;
  right: 0;
  z-index: var(--z-raised);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-4);
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: var(--text-xs);
  text-align: center;
  pointer-events: none;
}

.studio-tile-prompt--rest {
  width: calc(50% - var(--space-1) / 2);
  aspect-ratio: 5 / 6;
}

.studio-tile-prompt--all {
  width: 100%;
  aspect-ratio: 5 / 3;
}

.studio-tile-prompt :deep(.app-btn) {
  pointer-events: auto;
}
</style>
