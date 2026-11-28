<template>
  <AppDialog
    :open="open"
    title="Add existing outfits"
    :subtitle="person ? `To ${person.name}'s wardrobe` : ''"
    size="xl"
    :pad-body="false"
    @close="emit('close')"
    @accept="add"
  >
    <!-- The picture picker's frame (PicturePicker.css): the rail narrows by
         whose an outfit is, the grid shows each by its first picture. -->
    <div class="pp" data-testid="outfit-picker">
      <nav class="pp-rail" aria-label="Narrow the outfits">
        <div class="pp-sec">Whose</div>
        <button
          v-for="facet in facets"
          :key="facet.id"
          type="button"
          class="pp-facet"
          :class="{ 'pp-facet--on': whose === facet.id }"
          :aria-pressed="whose === facet.id"
          :data-testid="`outfit-facet-${facet.id}`"
          @click="whose = facet.id"
        >
          <span class="pp-facet__label">{{ facet.label }}</span>
          <span class="pp-facet__count">{{ facet.count }}</span>
        </button>
      </nav>

      <div class="pp-main">
        <div class="pp-head">
          <AppInput
            v-model="search"
            class="pp-search"
            icon="magnify"
            placeholder="Search the outfits"
            data-testid="outfit-search"
          />
        </div>
        <div class="pp-scroll">
          <p v-if="!shown.length" class="pp-note" role="status">
            {{
              offered.length
                ? "No outfit matches."
                : "No other outfits yet. New outfit… draws one; the Wardrobe holds every outfit."
            }}
          </p>
          <div v-else class="pp-grid">
            <div v-for="outfit in shown" :key="outfit.id" class="op-tile">
              <button
                type="button"
                class="pp-cell"
                :class="{ 'pp-cell--on': chosen.includes(outfit.id) }"
                :aria-pressed="chosen.includes(outfit.id)"
                :aria-label="`${outfit.name}, ${ownerWords(outfit)}`"
                :data-testid="`outfit-pick-${outfit.id}`"
                @click="toggle(outfit.id)"
                @dblclick="addOnly(outfit.id)"
              >
                <img
                  v-if="outfit.picture_ids?.[0]"
                  :src="pictureThumbnailUrl(outfit.picture_ids[0])"
                  alt=""
                  loading="lazy"
                />
                <span v-else class="pp-gone"><v-icon size="18">mdi-hanger</v-icon></span>
                <span v-if="chosen.includes(outfit.id)" class="pp-tick" aria-hidden="true"
                  >✓</span
                >
              </button>
              <span class="op-name">{{ outfit.name }}</span>
              <span class="op-meta">{{ ownerWords(outfit) }}</span>
            </div>
          </div>
        </div>
        <div class="pp-foot">
          <span class="pp-chosen">{{
            chosen.length ? `${chosen.length} chosen` : "None chosen"
          }}</span>
          <span class="pp-spacer"></span>
          <AppButton variant="secondary" key-hint="esc" @click="emit('close')"
            >Cancel</AppButton
          >
          <AppButton
            variant="primary"
            :disabled="!chosen.length"
            data-testid="outfit-add"
            @click="add"
            >{{ chosen.length > 1 ? `Add ${chosen.length} outfits` : "Add outfit" }}</AppButton
          >
        </div>
      </div>
    </div>
  </AppDialog>
</template>

<script setup>
/**
 * Add outfits the Studio already has to a person's wardrobe (#2819), from
 * their lightbox's *Add existing outfit…*. Every outfit not theirs is
 * offered: one that is no one's becomes theirs, and another person's is
 * added as a copy of the same pictures, so it is taken from nobody.
 */
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";
import AppInput from "./AppInput.vue";
import { pictureThumbnailUrl } from "../../api/pictures";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `{ id, name }` of the person whose wardrobe it is. */
  person: { type: Object, default: null },
  /** Every outfit in the Studio, as the overview lists them. */
  outfits: { type: Array, default: () => [] },
  /** The Studio's people, to name whose an outfit is. */
  people: { type: Array, default: () => [] },
});
const emit = defineEmits(["close", "add"]);

const whose = ref("all");
const search = ref("");
const chosen = ref([]);

watch(
  () => props.open,
  (open) => {
    if (!open) return;
    whose.value = "all";
    search.value = "";
    chosen.value = [];
  },
);

const nameOf = computed(() => new Map(props.people.map((p) => [p.id, p.name])));
const offered = computed(() =>
  props.outfits.filter((o) => o.character_id !== props.person?.id),
);

function ownerWords(outfit) {
  if (outfit.character_id == null) return "Not anyone's";
  return `${nameOf.value.get(outfit.character_id) ?? "Someone"}'s · adds a copy`;
}

const facets = computed(() => {
  const count = (test) => offered.value.filter(test).length;
  const owners = [...new Set(offered.value.map((o) => o.character_id).filter((id) => id != null))];
  return [
    { id: "all", label: "Everyone's", count: offered.value.length },
    { id: "nobody", label: "Not anyone's", count: count((o) => o.character_id == null) },
    ...owners
      .map((id) => ({
        id: `person-${id}`,
        label: nameOf.value.get(id) ?? "Someone",
        count: count((o) => o.character_id === id),
      }))
      .sort((a, b) => a.label.localeCompare(b.label)),
  ];
});

const shown = computed(() => {
  const words = search.value.trim().toLowerCase();
  return offered.value.filter((o) => {
    if (whose.value === "nobody" && o.character_id != null) return false;
    if (whose.value.startsWith("person-") && `person-${o.character_id}` !== whose.value) {
      return false;
    }
    return !words || o.name.toLowerCase().includes(words);
  });
});

function toggle(id) {
  chosen.value = chosen.value.includes(id)
    ? chosen.value.filter((c) => c !== id)
    : [...chosen.value, id];
}

function add() {
  if (!chosen.value.length) return;
  emit(
    "add",
    props.outfits.filter((o) => chosen.value.includes(o.id)),
  );
}

/** A double-click adds that one outfit alone, as the picture picker uses one. */
function addOnly(id) {
  chosen.value = [id];
  add();
}
</script>

<style scoped src="./PicturePicker.css"></style>
<style scoped>
.op-tile {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

/* An outfit is drawn on a mannequin, taller than wide: the tile follows. */
.op-tile .pp-cell {
  aspect-ratio: 4 / 5;
}

.op-name {
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.op-meta {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
