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
        <Segmented
          v-if="section === 'favourites'"
          v-model="favouriteKind"
          :options="favouriteKindOptions"
          aria-label="Show favourites of one kind"
        />
        <span class="studio-spacer"></span>
        <AppButton
          v-if="section === 'locations' || section === 'props'"
          variant="ghost"
          icon-left="playlist-plus"
          data-testid="studio-add-set"
          @click="addSetOpen = true"
          >Add a set…</AppButton
        >
        <AppButton
          v-if="section === 'locations' || section === 'props'"
          variant="secondary"
          icon-left="draw"
          data-testid="studio-draw-sheets"
          @click="
            emit('item-sheets', {
              kind: section === 'locations' ? 'location' : 'prop',
            })
          "
          >{{
            section === "locations" ? "Draw location sheets…" : "Draw prop sheets…"
          }}</AppButton
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
      <div class="studio-scroll">
        <div v-if="store.loaded && !cards.length" class="studio-empty">
          <v-icon size="32">{{ emptyIcon }}</v-icon>
          <span>{{ emptyText }}</span>
        </div>
        <div v-else class="studio-grid" role="list">
          <div
            v-for="card in cards"
            :key="`${card.kind}:${card.id}`"
            role="listitem"
            class="studio-cell"
          >
            <button
              type="button"
              class="studio-card"
              :data-testid="`studio-card-${card.kind}-${card.id}`"
              @click="openItem(card.kind, card.id)"
            >
              <span
                class="studio-card__ph"
                :class="{
                  'studio-card__ph--land':
                    card.kind === 'location' || card.kind === 'scene',
                }"
              >
                <img v-if="card.thumb" :src="card.thumb" alt="" loading="lazy" />
                <span v-else class="studio-card__empty"
                  ><v-icon size="28">{{ STUDIO_KINDS[card.kind].icon }}</v-icon></span
                >
                <span class="studio-badge" :style="kindStyle(card.kind)">
                  <span class="studio-tile"
                    ><v-icon size="12">{{ STUDIO_KINDS[card.kind].icon }}</v-icon></span
                  >{{ STUDIO_KINDS[card.kind].label }}
                </span>
              </span>
              <span class="studio-card__name"
                >{{ card.name
                }}<span class="studio-card__meta">{{ card.meta }}</span></span
              >
            </button>
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
      :title="section === 'locations' ? 'Add a location' : 'Add a prop'"
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
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import FavouriteHeart from "../widgets/FavouriteHeart.vue";
import Segmented from "../widgets/Segmented.vue";
import StudioItem from "./StudioItem.vue";
import StudioScene from "./StudioScene.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { listPictureSets } from "../../api/pictureSets";
import {
  createScene,
  listSetInStudio,
  setStudioFavourite,
} from "../../api/studio";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useStudioStore } from "../../stores/useStudioStore";
import { errorMessage } from "../../utils/apiError";
import {
  KIND_LIST,
  KIND_SECTION,
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

// New scene from the sidebar's footer arrives as `?new=1`, a one-shot.
watch(
  () => route.query.new,
  (value) => {
    if (!value) return;
    go({ new: null }, { replace: true });
    newScene();
  },
  { immediate: true },
);

const sectionTitle = computed(() =>
  section.value === "favourites"
    ? "Favourites"
    : (STUDIO_KINDS[{ scenes: "scene", cast: "cast", locations: "location", props: "prop" }[section.value]]?.all ??
      "All scenes"),
);

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

const people = computed(() => store.overview.cast);
const personOptions = computed(() => [
  { label: "With anyone", value: "" },
  ...people.value.map((p) => ({ label: `With ${p.name}`, value: String(p.id) })),
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
    return who ? `${who}'s outfit` : "Outfit";
  }
  return `In ${item.scene_count} ${item.scene_count === 1 ? "scene" : "scenes"}`;
}

const asCard = (kind) => (item) => ({
  ...item,
  kind,
  thumb: thumbOf(kind, item),
  meta: metaOf(kind, item),
});

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

const cards = computed(() => {
  switch (section.value) {
    case "favourites":
      return favouriteKind.value === "all"
        ? favourites.value
        : favourites.value.filter((c) => c.kind === favouriteKind.value);
    case "cast":
      return store.overview.cast.map(asCard("cast"));
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
      locations: STUDIO_KINDS.location.icon,
      props: STUDIO_KINDS.prop.icon,
    })[section.value] ?? STUDIO_KINDS.scene.icon,
);

const emptyText = computed(
  () =>
    ({
      favourites: "Nothing starred yet. The heart on any card adds it here.",
      cast: "No people here yet. People are made in Media; their outfits are drawn from their lightbox here.",
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
  const kind = section.value === "locations" ? "location" : "prop";
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

.studio-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.studio-hint--error {
  color: rgb(var(--v-theme-error));
}
</style>
