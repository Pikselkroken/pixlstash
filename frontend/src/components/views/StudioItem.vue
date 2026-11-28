<template>
  <div class="studio-lightbox" :style="kindStyle(kind)" data-testid="studio-item">
    <div class="studio-lightbox__main">
      <div class="studio-toolbar">
        <AppButton variant="ghost" icon-left="chevron-left" key-hint="esc" @click="emit('close')">{{
          backLabel
        }}</AppButton>
        <template v-if="neighbours.length > 1">
          <AppButton
            variant="ghost"
            icon-only
            icon-left="chevron-left"
            tooltip="Previous"
            :disabled="index <= 0"
            @click="step(-1)"
          />
          <span class="studio-sub">{{ index + 1 }} of {{ neighbours.length }}</span>
          <AppButton
            variant="ghost"
            icon-only
            icon-left="chevron-right"
            tooltip="Next"
            :disabled="index >= neighbours.length - 1"
            @click="step(1)"
          />
        </template>
        <span class="studio-title">{{ item?.name ?? "Not found" }}</span>
        <span class="studio-spacer"></span>
        <template v-if="item">
          <AppButton
            v-if="kind === 'location'"
            variant="secondary"
            icon-left="movie-open-plus-outline"
            @click="emit('new-scene', item.id)"
            >New scene here</AppButton
          >
          <AppButton
            v-if="kind === 'location'"
            variant="secondary"
            icon-left="rotate-orbit"
            :disabled="!item.picture_ids.length"
            data-testid="studio-more-angles"
            @click="openAngles"
            >More angles…</AppButton
          >
          <AppButton
            v-if="kind === 'cast'"
            variant="ghost"
            icon-left="wardrobe-outline"
            data-testid="studio-add-outfit"
            @click="outfitPickerOpen = true"
            >Add existing outfit…</AppButton
          >
          <AppButton
            v-if="kind === 'cast'"
            variant="secondary"
            icon-left="hanger"
            data-testid="studio-new-outfit"
            @click="drawOutfit"
            >New outfit…</AppButton
          >
          <AppButton
            v-if="kind !== 'cast'"
            variant="ghost"
            icon-left="draw"
            @click="drawSheets"
            >New sheets…</AppButton
          >
        </template>
      </div>
      <div class="studio-scroll">
        <div v-if="!item" class="studio-empty">
          <span>This item is not in this project, or it is gone.</span>
        </div>
        <template v-else-if="kind === 'cast'">
          <div class="studio-person">
            <img
              v-if="item.thumbnail_picture_id"
              class="studio-person__face"
              :src="pictureThumbnailUrl(item.thumbnail_picture_id)"
              data-testid="studio-person-face"
              @dblclick="openInLightbox(item.thumbnail_picture_id)"
              alt=""
            />
            <div class="studio-person__wardrobe">
              <!-- The pictures their likeness is drawn from, chosen in Media. -->
              <h3 class="studio-heading">Reference pictures</h3>
              <div v-if="references.length" class="studio-refs" data-testid="studio-refs">
                <button
                  v-for="(pid, i) in references"
                  :key="pid"
                  type="button"
                  class="studio-ref"
                  :aria-label="`${item.name}, reference picture ${i + 1}. Open it.`"
                  :data-testid="`studio-ref-${pid}`"
                  @dblclick="openInLightbox(pid)"
                  @keydown.enter.prevent="openInLightbox(pid)"
                >
                  <img :src="pictureThumbnailUrl(pid)" alt="" loading="lazy" />
                </button>
              </div>
              <p v-else-if="referencesRead" class="studio-hint" data-testid="studio-refs-none">
                No reference pictures yet. They are chosen from {{ item.name }}'s faces in Media.
              </p>
              <h3 class="studio-heading">Wardrobe</h3>
              <div v-if="wardrobe.length || outfitDraws.length" class="studio-grid">
                <!-- Outfit sheets drawing for them in the background. -->
                <DrawPlaceholder
                  v-for="draw in outfitDraws"
                  :key="draw.key"
                  :job="draw"
                  :label="draw.name"
                  icon="mdi-hanger"
                  :aspect="4 / 5"
                  testid="studio-outfit-placeholder"
                  @open="reopenSheets(draw)"
                />
                <button
                  v-for="outfit in wardrobe"
                  :key="outfit.id"
                  type="button"
                  class="studio-card"
                  @click="emit('go', 'outfit', outfit.id)"
                >
                  <span
                    class="studio-card__ph"
                    :style="outfit.aspect ? { '--tile-aspect': outfit.aspect } : undefined"
                  >
                    <img
                      v-if="outfit.picture_ids[0]"
                      :src="pictureThumbnailUrl(outfit.picture_ids[0])"
                      alt=""
                      loading="lazy"
                    />
                  </span>
                  <span class="studio-card__name"
                    >{{ outfit.name
                    }}<span class="studio-card__meta"
                      >{{ outfit.picture_ids.length }} angles</span
                    ></span
                  >
                </button>
              </div>
              <p v-else class="studio-hint">
                No outfits yet. New outfit… draws one as sheets of four angles.
              </p>
            </div>
          </div>
        </template>
        <template v-else>
          <!-- Click selects (Ctrl adds, Shift ranges), Esc clears, Delete or
               right-click acts on the selection: the picture grid's gestures. -->
          <div class="studio-angles" @click.self="selected = []">
            <figure
              v-for="(pid, i) in item.picture_ids"
              :key="pid"
              class="studio-angle"
              :class="{ 'studio-angle--on': selected.includes(pid) }"
            >
              <button
                type="button"
                class="studio-angle__pick"
                :aria-pressed="selected.includes(pid) ? 'true' : 'false'"
                :aria-label="`${item.name}, ${pictureName(i)}`"
                :data-testid="`studio-pic-${pid}`"
                @click="onPictureClick(pid, i, $event)"
                @dblclick="openInLightbox(pid)"
                @contextmenu.prevent="openPictureMenu(pid, $event)"
              >
                <img :src="pictureThumbnailUrl(pid)" alt="" />
              </button>
              <figcaption>{{ pictureName(i) }}</figcaption>
            </figure>
            <!-- The views More angles is drawing in the background, each
                 where it will land, with the draw's bar. -->
            <template v-if="angleDraw?.making === 'views'">
              <figure v-for="n in angleDraw.count" :key="`drawing-${n}`" class="studio-angle">
                <DrawPlaceholder
                  :job="angleDraw"
                  :label="`New view ${n}`"
                  icon="mdi-rotate-orbit"
                  :aspect="16 / 9"
                  :testid="`studio-view-placeholder-${n}`"
                  @open="locationDraw.reopen()"
                />
              </figure>
            </template>
            <p v-if="!item.picture_ids.length && !angleDraw" class="studio-hint">
              No pictures yet. New sheets… draws them.
            </p>
          </div>
        </template>
      </div>
      <!-- The selection pill over the pictures, as on every grid. -->
      <div v-if="selected.length" class="selbar-float">
        <StudioSelectionBar
          :count="selected.length"
          noun="picture"
          plural="pictures"
          :verbs="pictureVerbs"
          testid="studio-pics-bar"
          @verb="pictureVerb"
          @clear="selected = []"
          @select-all="selected = [...(item?.picture_ids ?? [])]"
        />
      </div>
    </div>
    <aside class="studio-inspector" aria-label="Inspector">
      <div class="studio-inspector__head">
        <span class="studio-tile"
          ><v-icon size="12">{{ STUDIO_KINDS[kind].icon }}</v-icon></span
        >
        {{ STUDIO_KINDS[kind].label }}
        <span class="studio-spacer"></span>
        <FavouriteHeart
          v-if="item"
          :on="!!item.favourite"
          :label="item.name"
          :tabindex="0"
          @toggle="toggleFavourite"
        />
      </div>
      <div v-if="item" class="studio-inspector__body">
        <details class="studio-group" open>
          <summary>Description</summary>
          <div class="studio-group__body">
            <p class="studio-hint studio-hint--ink">
              {{ item.description || "No description." }}
            </p>
            <p class="studio-hint">
              {{
                kind === "location"
                  ? "Goes into every scene's prompt for this place."
                  : kind === "cast"
                    ? "Goes into every scene's prompt for this person."
                    : kind === "outfit"
                      ? "Goes into every scene's prompt for whoever wears it."
                      : "Goes into every scene's prompt that has this prop."
              }}
            </p>
          </div>
        </details>
        <details v-if="kind === 'outfit' && wearer" class="studio-group" open>
          <summary>Worn by</summary>
          <div class="studio-group__body">
            <button type="button" class="studio-list-link" @click="emit('go', 'cast', wearer.id)">
              {{ wearer.name }}
            </button>
          </div>
        </details>
        <details class="studio-group" open>
          <summary>{{ kind === "location" ? "Scenes here" : "In scenes" }}</summary>
          <div class="studio-group__body">
            <button
              v-for="scene in usedIn"
              :key="scene.id"
              type="button"
              class="studio-list-link"
              @click="emit('go', 'scene', scene.id)"
            >
              {{ scene.name }}
              <span class="studio-card__meta">{{ scene.take_count }} takes</span>
            </button>
            <p v-if="!usedIn.length" class="studio-hint">In no scene yet.</p>
          </div>
        </details>
        <details v-if="kind !== 'cast'" class="studio-group">
          <summary>Source</summary>
          <div class="studio-group__body">
            <p class="studio-hint">
              A picture set of the same name. Its pictures, oldest first, are the
              {{ angleNames.join(", ") }}.
            </p>
            <AppButton variant="ghost" size="sm" @click="stopUsing"
              >Stop using in the Studio</AppButton
            >
          </div>
        </details>
      </div>
    </aside>

    <OutfitPicker
      v-if="kind === 'cast'"
      :open="outfitPickerOpen"
      :person="item"
      :outfits="store.overview.outfits"
      :people="store.overview.cast"
      @close="outfitPickerOpen = false"
      @add="addOutfits"
    />

    <Teleport to="body">
      <div
        v-if="menu"
        ref="menuEl"
        class="ctx-menu studio-ctx-menu"
        role="menu"
        tabindex="-1"
        :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
        data-testid="studio-pics-menu"
        @contextmenu.prevent
        @mousedown.stop
        @keydown="onMenuKey"
      >
        <button type="button" role="menuitem" class="ctx-item" data-verb="remove" @click="runMenu(removeSelected)">
          <v-icon class="ctx-icon" aria-hidden="true">mdi-playlist-remove</v-icon>
          <span class="ctx-label-text">Remove from this {{ noun }}</span>
        </button>
        <div class="ctx-sep" role="separator"></div>
        <button
          type="button"
          role="menuitem"
          class="ctx-item ctx-item--danger"
          data-verb="delete"
          @click="runMenu(deleteSelected)"
        >
          <v-icon class="ctx-icon" aria-hidden="true">mdi-delete-outline</v-icon>
          <span class="ctx-label-text">Delete</span>
          <span class="ctx-shortcut" aria-hidden="true">Del</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
/**
 * The lightbox of a person, an outfit, a location or a prop (#2819): its
 * pictures, the first four named as the sheet cuts them (a person's
 * wardrobe), and in the inspector its words, who wears it and the scenes it
 * is in. ‹ › steps through its section's items. The pictures select as on the
 * picture grid, to take out of the item or delete.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { VIcon } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import FavouriteHeart from "../widgets/FavouriteHeart.vue";
import OutfitPicker from "../widgets/OutfitPicker.vue";
import DrawPlaceholder from "../widgets/DrawPlaceholder.vue";
import StudioSelectionBar from "../panels/StudioSelectionBar.vue";
import { deletePictures, pictureThumbnailUrl } from "../../api/pictures";
import { removePictureFromSet } from "../../api/pictureSets";
import { getReferencePictures } from "../../api/characters";
import { setStudioFavourite, stopUsingSetInStudio } from "../../api/studio";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useOpenInLightbox } from "../../composables/useOpenInLightbox";
import { useLocationDrawStore } from "../../stores/useLocationDrawStore";
import { useSheetDrawsStore } from "../../stores/useSheetDrawsStore";
import { useStudioStore } from "../../stores/useStudioStore";
import { errorMessage } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard";
import { addOutfitToWardrobe } from "../../utils/studioCardActions";
import {
  ITEM_ANGLES,
  KIND_LIST,
  LOCATION_VIEWS,
  STUDIO_KINDS,
  kindStyle,
} from "../../utils/studioKinds";

const props = defineProps({
  kind: { type: String, required: true },
  itemId: { type: Number, default: null },
  backLabel: { type: String, default: "Back" },
});
const emit = defineEmits(["close", "go", "changed", "new-scene", "item-sheets"]);

const store = useStudioStore();
const noticeStore = useNoticeStore();
// More angles… opens the one location draw (App.vue), which goes on when it
// closes; focus goes back to what opened it there.
const locationDraw = useLocationDrawStore();
// A double-click opens a picture in the Lightbox; Esc or Back comes here.
const openInLightbox = useOpenInLightbox();

function openAngles() {
  locationDraw.openFor(item.value);
}

// What is being drawn for this item in the background: More angles' views
// of a location, a person's outfit sheets. Each gets a placeholder.
const sheetDraws = useSheetDrawsStore();
const angleDraw = computed(() =>
  props.kind === "location" ? locationDraw.jobFor(props.itemId) : null,
);
const outfitDraws = computed(() =>
  props.kind === "cast" ? sheetDraws.of("outfit", props.itemId) : [],
);

/** Open the sheet dialog on a draw kept while it runs. */
function reopenSheets(draw) {
  emit("item-sheets", { kind: draw.kind, character: draw.character, sheet: true });
}

const item = computed(() => store.byKey.get(`${props.kind}:${props.itemId}`) || null);
const angleNames = computed(() => (props.kind === "location" ? LOCATION_VIEWS : ITEM_ANGLES));
const pictureName = (i) => angleNames.value[i] ?? `Picture ${i + 1}`;
const noun = computed(() => STUDIO_KINDS[props.kind]?.label.toLowerCase() ?? "item");

// ── The pictures' selection ───────────────────────────────────────────────
const selected = ref([]);

function onPictureClick(pid, index, event) {
  const ids = item.value?.picture_ids ?? [];
  if (event?.shiftKey) {
    const anchor = ids.findIndex((id) => selected.value.includes(id));
    const from = anchor < 0 ? index : anchor;
    const [start, end] = from <= index ? [from, index] : [index, from];
    selected.value = ids.slice(start, end + 1);
  } else if (event?.ctrlKey || event?.metaKey) {
    selected.value = selected.value.includes(pid)
      ? selected.value.filter((id) => id !== pid)
      : [...selected.value, pid];
  } else {
    selected.value = [pid];
  }
}

// A picture no longer in the item cannot stay selected; another item, none.
watch(
  () => item.value?.picture_ids,
  (ids) => {
    const now = new Set(ids ?? []);
    if (selected.value.some((id) => !now.has(id))) {
      selected.value = selected.value.filter((id) => now.has(id));
    }
  },
);
watch(() => props.itemId, () => {
  selected.value = [];
});

// The pill's verbs, the same two the right-click menu has.
const pictureVerbs = computed(() => [
  { key: "remove", icon: "playlist-remove", label: `Remove from this ${noun.value}` },
  {
    key: "delete",
    icon: "delete-outline",
    label: "Delete: to the Scrapheap, where it can be restored (Del)",
    danger: true,
  },
]);

function pictureVerb(key) {
  if (key === "remove") removeSelected();
  else if (key === "delete") deleteSelected();
}

/** Take the selected pictures out of this item; they stay in the library. */
async function removeSelected() {
  const ids = [...selected.value];
  if (!ids.length) return;
  const failed = [];
  for (const id of ids) {
    try {
      await removePictureFromSet(props.itemId, id);
    } catch (e) {
      console.warn("Couldn't take a picture out of the Studio item", props.itemId, id, e);
      failed.push(id);
    }
  }
  selected.value = failed;
  emit("changed");
  if (failed.length) {
    noticeStore.error(`Couldn't take ${failed.length} of the pictures out of ${item.value?.name}.`, {
      key: "studio-pics-remove",
    });
  }
}

/** Delete the selected pictures: to the Scrapheap, where they can be restored. */
async function deleteSelected() {
  const ids = [...selected.value];
  if (!ids.length) return;
  try {
    await deletePictures(ids);
    selected.value = [];
    emit("changed");
  } catch (e) {
    noticeStore.error(`Couldn't delete the pictures. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-pics-delete",
    });
  }
}

// ── Their menu ────────────────────────────────────────────────────────────
const menu = ref(null); // { x, y }
const menuEl = ref(null);
let menuTrigger = null;

async function openPictureMenu(pid, event) {
  if (!selected.value.includes(pid)) selected.value = [pid];
  menuTrigger = event.currentTarget ?? null;
  menu.value = { x: event.clientX, y: event.clientY };
  // Kept inside the window by its own drawn size, as the card menu is.
  await nextTick();
  const w = menuEl.value?.offsetWidth ?? 0;
  const h = menuEl.value?.offsetHeight ?? 0;
  menu.value = {
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - w - 8)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - h - 8)),
  };
  menuEl.value?.querySelector(".ctx-item")?.focus();
}

function closeMenu({ refocus = false } = {}) {
  if (!menu.value) return;
  menu.value = null;
  if (refocus) menuTrigger?.focus?.();
  menuTrigger = null;
}

function runMenu(verb) {
  closeMenu({ refocus: true });
  verb();
}

// Tab leaves the menu for the picture it was opened on, as the card menu's.
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

const neighbours = computed(() => {
  const list = store.overview[KIND_LIST[props.kind]] || [];
  if (props.kind === "outfit" && item.value?.character_id != null) {
    return list.filter((o) => o.character_id === item.value.character_id);
  }
  return list;
});
const index = computed(() => neighbours.value.findIndex((i) => i.id === props.itemId));

function step(delta) {
  const next = neighbours.value[index.value + delta];
  if (next) emit("go", props.kind, next.id);
}

const wardrobe = computed(() =>
  props.kind === "cast" ? store.overview.outfits.filter((o) => o.character_id === props.itemId) : [],
);
const wearer = computed(() =>
  props.kind === "outfit" ? store.byKey.get(`cast:${item.value?.character_id}`) : null,
);

const usedIn = computed(() =>
  store.overview.scenes.filter((s) => {
    if (props.kind === "cast") return s.cast_ids.includes(props.itemId);
    if (props.kind === "location") return s.location_set_id === props.itemId;
    if (props.kind === "outfit") return s.outfit_ids.includes(props.itemId);
    return s.prop_ids.includes(props.itemId);
  }),
);

function drawSheets() {
  emit("item-sheets", {
    kind: props.kind,
    character: props.kind === "outfit" && wearer.value ? { id: wearer.value.id, name: wearer.value.name } : null,
  });
}

// ── A person's reference pictures ─────────────────────────────────────────
const references = ref([]);
const referencesRead = ref(false);
watch(
  () => (props.kind === "cast" ? props.itemId : null),
  async (id) => {
    references.value = [];
    referencesRead.value = false;
    if (id == null) return;
    try {
      const body = await getReferencePictures(id);
      if (props.itemId !== id) return;
      references.value = body?.reference_picture_ids ?? [];
    } catch (e) {
      console.warn("Couldn't read the person's reference pictures", id, e);
    } finally {
      if (props.itemId === id) referencesRead.value = true;
    }
  },
  { immediate: true },
);

// ── Add existing outfit… ──────────────────────────────────────────────────
const outfitPickerOpen = ref(false);

/** Put the outfits picked in this person's wardrobe, then show them there. */
async function addOutfits(outfits) {
  outfitPickerOpen.value = false;
  const failed = [];
  for (const outfit of outfits) {
    try {
      await addOutfitToWardrobe(outfit, item.value);
    } catch (e) {
      console.warn("Couldn't add an outfit to the wardrobe", outfit.id, props.itemId, e);
      failed.push(`${outfit.name}: ${errorMessage(e, "Please try again.")}`);
    }
  }
  emit("changed");
  if (failed.length) {
    noticeStore.error(`Couldn't add ${failed.join("; ")}`, { key: "studio-add-outfit" });
  }
}

function drawOutfit() {
  emit("item-sheets", { kind: "outfit", character: { id: item.value.id, name: item.value.name } });
}

async function toggleFavourite() {
  try {
    await setStudioFavourite(props.kind, props.itemId, !item.value.favourite);
    emit("changed");
  } catch (e) {
    noticeStore.error(`Couldn't change the favourite. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-favourite",
    });
  }
}

async function stopUsing() {
  try {
    await stopUsingSetInStudio(props.itemId);
    emit("changed");
    emit("close");
  } catch (e) {
    noticeStore.error(`Couldn't take it out of the Studio. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-stop-using",
    });
  }
}

function onKey(event) {
  if (locationDraw.dialogOpen || outfitPickerOpen.value) return;
  if (event.defaultPrevented || event.target?.closest?.("input, textarea, [role=dialog]")) return;
  // Esc closes the menu, then clears the selection, and only then the lightbox.
  // With the menu open its keys are its own: no stepping or deleting under it.
  if (menu.value && event.key !== "Escape") return;
  if (event.key === "Escape" && menu.value) {
    // The menu's alone: no other Esc handler closes what is under it.
    event.preventDefault();
    event.stopImmediatePropagation();
    closeMenu({ refocus: true });
  } else if (event.key === "Escape" && selected.value.length) selected.value = [];
  else if (event.key === "Delete" && selected.value.length) deleteSelected();
  else if (event.key === "Escape") emit("close");
  else if (event.key === "ArrowLeft") step(-1);
  else if (event.key === "ArrowRight") step(1);
}
onMounted(() => {
  window.addEventListener("keydown", onKey);
  document.addEventListener("mousedown", onOutside);
});
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  document.removeEventListener("mousedown", onOutside);
});
</script>

<style scoped src="./Studio.css"></style>
<style scoped>
.studio-person {
  display: flex;
  gap: var(--space-6);
  align-items: flex-start;
}

.studio-person__face {
  width: 240px;
  border-radius: var(--radius-md);
  object-fit: cover;
}

/* A person's reference pictures: small, whole, side by side. */
.studio-refs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));
  gap: var(--space-2);
  margin-bottom: var(--space-5);
}

.studio-ref {
  aspect-ratio: 1 / 1;
  padding: 0;
  border: 0;
  border-radius: var(--radius-md);
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
  cursor: zoom-in;
}

.studio-ref img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.studio-ref:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--space-1);
}

.studio-person__wardrobe {
  flex: 1;
  min-width: 0;
}

.studio-heading {
  margin: 0 0 var(--space-3);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.studio-hint--ink {
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
}
</style>
