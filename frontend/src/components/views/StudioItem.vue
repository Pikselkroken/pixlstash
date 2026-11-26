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
            @click="anglesOpen = true"
            >More angles…</AppButton
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
              alt=""
            />
            <div class="studio-person__wardrobe">
              <h3 class="studio-heading">Wardrobe</h3>
              <div v-if="wardrobe.length" class="studio-grid">
                <button
                  v-for="outfit in wardrobe"
                  :key="outfit.id"
                  type="button"
                  class="studio-card"
                  @click="emit('go', 'outfit', outfit.id)"
                >
                  <span class="studio-card__ph">
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
        <div v-else class="studio-angles">
          <figure
            v-for="(pid, i) in item.picture_ids.slice(0, 4)"
            :key="pid"
            class="studio-angle"
          >
            <img :src="pictureThumbnailUrl(pid)" :alt="`${item.name}, ${angleNames[i]}`" />
            <figcaption>{{ angleNames[i] }}</figcaption>
          </figure>
          <p v-if="!item.picture_ids.length" class="studio-hint">
            No pictures yet. New sheets… draws them.
          </p>
        </div>
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
    <LocationAnglesDialog
      v-if="kind === 'location'"
      :open="anglesOpen"
      :location="item"
      @close="anglesOpen = false"
      @done="emit('changed')"
    />
  </div>
</template>

<script setup>
/**
 * The lightbox of a person, an outfit, a location or a prop (#2819): its four
 * angles (a person's wardrobe), and in the inspector its words, who wears it
 * and the scenes it is in. ‹ › steps through its section's items.
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { VIcon } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import FavouriteHeart from "../widgets/FavouriteHeart.vue";
import LocationAnglesDialog from "../io/LocationAnglesDialog.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { setStudioFavourite, stopUsingSetInStudio } from "../../api/studio";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useStudioStore } from "../../stores/useStudioStore";
import { errorMessage } from "../../utils/apiError";
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
const anglesOpen = ref(false);

const item = computed(() => store.byKey.get(`${props.kind}:${props.itemId}`) || null);
const angleNames = computed(() => (props.kind === "location" ? LOCATION_VIEWS : ITEM_ANGLES));

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
  if (anglesOpen.value) return;
  if (event.defaultPrevented || event.target?.closest?.("input, textarea, [role=dialog]")) return;
  if (event.key === "Escape") emit("close");
  else if (event.key === "ArrowLeft") step(-1);
  else if (event.key === "ArrowRight") step(1);
}
onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
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
