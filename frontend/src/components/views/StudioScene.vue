<template>
  <div class="studio-lightbox" data-testid="studio-scene">
    <div class="studio-lightbox__main">
      <div class="studio-toolbar">
        <AppButton variant="ghost" icon-left="chevron-left" key-hint="esc" @click="emit('close')">{{
          backLabel
        }}</AppButton>
        <template v-if="scenes.length > 1">
          <AppButton
            variant="ghost"
            icon-only
            icon-left="chevron-left"
            tooltip="Previous scene"
            :disabled="index <= 0"
            @click="step(-1)"
          />
          <span class="studio-sub">{{ index + 1 }} of {{ scenes.length }}</span>
          <AppButton
            variant="ghost"
            icon-only
            icon-left="chevron-right"
            tooltip="Next scene"
            :disabled="index >= scenes.length - 1"
            @click="step(1)"
          />
        </template>
        <input
          v-if="draft"
          v-model="draft.name"
          class="scene-name"
          aria-label="Scene name"
          data-testid="scene-name"
          @change="save(['name'])"
        />
        <span class="studio-spacer"></span>
        <template v-if="draft">
          <Segmented
            v-model="draft.camera.shot"
            :options="SHOTS"
            aria-label="Camera"
            @update:model-value="save(['camera'])"
          />
          <AppSelect
            :model-value="optionValue(draft.camera.view)"
            class="scene-view"
            compact
            hide-label
            label="Seen from"
            :options="viewOptions"
            :disabled="!location"
            data-testid="scene-view"
            @update:model-value="
              draft.camera.view = fromOption($event) ?? 0;
              save(['camera']);
            "
          />
        </template>
      </div>

      <p v-if="loadError" class="studio-error" role="alert">{{ loadError }}</p>

      <div v-if="draft" class="scene-body">
        <!-- The stage: the location's view the camera takes, with a box per
             person and prop. A box is dragged to where it stands and sized by
             how close it is; its words are placed by it ("on the left, in the
             foreground"). A take picked in the strip shows here instead. -->
        <div class="scene-stage-wrap">
          <div v-if="shownTake" class="scene-stage scene-stage--take">
            <video
              v-if="shownTake.kind === 'clip' && shownTake.format"
              :src="pictureMediaUrl(shownTake.picture_id, shownTake.format)"
              controls
              loop
              class="scene-take-media"
              data-testid="scene-take-video"
            ></video>
            <img
              v-else
              class="scene-take-media"
              :src="pictureThumbnailUrl(shownTake.picture_id)"
              alt="A take of this scene"
            />
            <AppButton class="scene-take-back" variant="secondary" size="sm" @click="selection = null"
              >Back to the stage</AppButton
            >
          </div>
          <div
            v-else
            ref="stageEl"
            class="scene-stage"
            :class="{ 'scene-stage--empty': !stageUrl }"
            data-testid="scene-stage"
            @pointerdown.self="selectLocation"
          >
            <img v-if="stageUrl" :src="stageUrl" alt="" class="scene-stage__bg" draggable="false" />
            <span v-else class="scene-stage__hint">
              Pick a location in the inspector: its view is the stage.
            </span>
            <div
              v-for="mark in marks"
              :key="mark.key"
              class="scene-mark"
              :class="[`scene-mark--${mark.type}`, { 'scene-mark--on': isSelected(mark) }]"
              :style="markStyle(mark)"
              :data-testid="`scene-mark-${mark.key}`"
              tabindex="0"
              role="button"
              :aria-label="`${mark.label}. Drag or use the arrow keys to place; drag the corner or Alt and the arrow keys to size.`"
              @pointerdown.stop="startDrag($event, mark, 'move')"
              @keydown="onMarkKey($event, mark)"
            >
              <span class="scene-mark__tag">
                <span class="studio-tile" :style="kindStyle(mark.type === 'person' ? 'cast' : 'prop')"
                  ><v-icon size="10">{{ mark.type === "person" ? STUDIO_KINDS.cast.icon : STUDIO_KINDS.prop.icon }}</v-icon></span
                >{{ mark.label }}
              </span>
              <span v-if="mark.words" class="scene-mark__words">{{ mark.words }}</span>
              <span
                v-if="isSelected(mark)"
                class="scene-mark__handle"
                aria-hidden="true"
                @pointerdown.stop="startDrag($event, mark, 'size')"
              ></span>
            </div>
            <button
              v-if="location"
              type="button"
              class="scene-envchip"
              :class="{ 'scene-envchip--on': selection?.type === 'location' }"
              @click="selectLocation"
            >
              <span class="studio-tile" :style="kindStyle('location')"
                ><v-icon size="10">{{ STUDIO_KINDS.location.icon }}</v-icon></span
              >{{ location.name }}{{ settingWords ? ` · ${settingWords}` : "" }}
            </button>
          </div>
        </div>

        <!-- Who and what is in the scene, and adding to it. -->
        <div class="scene-castbar">
          <button
            v-for="(member, i) in draft.cast"
            :key="`p-${member.character_id}`"
            type="button"
            class="scene-chip"
            :class="{ 'scene-chip--on': selection?.type === 'person' && selection.index === i }"
            @click="selection = { type: 'person', index: i }"
          >
            <span class="studio-tile" :style="kindStyle('cast')"
              ><v-icon size="10">{{ STUDIO_KINDS.cast.icon }}</v-icon></span
            >{{ personName(member.character_id) }}
            <span class="studio-card__meta">{{ outfitName(member.outfit_set_id) }}</span>
          </button>
          <button
            v-for="(prop, i) in draft.props"
            :key="`o-${prop.set_id}-${i}`"
            type="button"
            class="scene-chip"
            :class="{ 'scene-chip--on': selection?.type === 'prop' && selection.index === i }"
            @click="selection = { type: 'prop', index: i }"
          >
            <span class="studio-tile" :style="kindStyle('prop')"
              ><v-icon size="10">{{ STUDIO_KINDS.prop.icon }}</v-icon></span
            >{{ propName(prop.set_id) }}
          </button>
          <AppSelect
            :model-value="''"
            class="scene-add"
            compact
            hide-label
            label="Add a person"
            placeholder="Add a person…"
            :options="addPersonOptions"
            data-testid="scene-add-person"
            @update:model-value="addPerson(fromOption($event))"
          />
          <AppSelect
            :model-value="''"
            class="scene-add"
            compact
            hide-label
            label="Add a prop"
            placeholder="Add a prop…"
            :options="addPropOptions"
            data-testid="scene-add-prop"
            @update:model-value="addProp(fromOption($event))"
          />
        </div>

        <!-- The takes: stills and clips, newest first, and the shoots still
             drawing. -->
        <div class="scene-takes" role="list" aria-label="Takes">
          <span class="scene-takes__label">Takes</span>
          <div
            v-for="run in scene.runs"
            :key="run.key"
            class="scene-take scene-take--drawing"
            role="listitem"
          >
            <span class="studio-hint">{{ run.kind === "clip" ? "Filming…" : "Drawing…" }}</span>
          </div>
          <button
            v-for="take in scene.takes"
            :key="take.picture_id"
            type="button"
            role="listitem"
            class="scene-take"
            :class="{ 'scene-take--on': selection?.type === 'take' && selection.pictureId === take.picture_id }"
            :aria-label="take.kind === 'clip' ? 'A clip' : 'A still'"
            :data-testid="`scene-take-${take.picture_id}`"
            @click="selection = { type: 'take', pictureId: take.picture_id }"
          >
            <img :src="pictureThumbnailUrl(take.picture_id)" alt="" loading="lazy" />
            <span v-if="take.kind === 'clip'" class="scene-take__clip"
              ><v-icon size="12">mdi-play</v-icon>Clip</span
            >
          </button>
          <span v-if="!scene.runs.length && !scene.takes.length" class="studio-hint"
            >No takes yet. Shoot draws the scene as it stands.</span
          >
        </div>
      </div>
    </div>

    <aside v-if="draft" class="studio-inspector" aria-label="Inspector" :style="inspectorStyle">
      <div class="studio-inspector__head">
        <span class="studio-tile" :style="inspectorStyle"
          ><v-icon size="12">{{ inspectorIcon }}</v-icon></span
        >
        <span class="scene-inspector-title">{{ inspectorTitle }}</span>
        <span class="studio-spacer"></span>
        <FavouriteHeart
          v-if="!selection"
          :on="!!scene.favourite"
          :label="scene.name"
          :tabindex="0"
          @toggle="toggleFavourite"
        />
      </div>
      <div class="studio-inspector__body">
        <!-- A person -->
        <template v-if="selectedPerson">
          <details class="studio-group" open>
            <summary>Wearing</summary>
            <div class="studio-group__body">
              <AppSelect
                :model-value="optionValue(selectedPerson.outfit_set_id)"
                label="Outfit"
                hide-label
                :options="wardrobeOptions(selectedPerson.character_id)"
                data-testid="scene-wearing"
                @update:model-value="
                  selectedPerson.outfit_set_id = fromOption($event);
                  save(['cast']);
                "
              />
              <AppButton variant="ghost" size="sm" icon-left="hanger" @click="drawOutfit(selectedPerson.character_id)"
                >New outfit…</AppButton
              >
            </div>
          </details>
          <details class="studio-group" open>
            <summary>Doing</summary>
            <div class="studio-group__body">
              <AppTextarea
                v-model="selectedPerson.doing"
                :rows="2"
                placeholder="hails the taxi, laughing"
                @update:model-value="saveSoon(['cast'])"
              />
            </div>
          </details>
          <details class="studio-group" open>
            <summary>Pose</summary>
            <div class="studio-group__body">
              <AppTextarea
                v-model="selectedPerson.pose"
                :rows="2"
                placeholder="on her toes, right arm stretched high, turned ¾ to the left"
                @update:model-value="saveSoon(['cast'])"
              />
              <p class="studio-hint">
                How the body is placed, in words. Turned ¾ also picks the outfit's ¾ angle.
              </p>
            </div>
          </details>
          <details class="studio-group" open>
            <summary>Brings</summary>
            <div class="studio-group__body">
              <Segmented
                v-model="selectedPerson.brings"
                full
                :options="BRINGS"
                aria-label="What of this person goes to the engine"
                @update:model-value="save(['cast'])"
              />
              <p class="studio-hint">{{ bringsHint }}</p>
            </div>
          </details>
          <AppButton variant="ghost" size="sm" icon-left="account-remove-outline" @click="removeSelected"
            >Take out of the scene</AppButton
          >
        </template>

        <!-- A prop -->
        <template v-else-if="selectedProp">
          <details class="studio-group" open>
            <summary>State</summary>
            <div class="studio-group__body">
              <AppTextarea
                v-model="selectedProp.state"
                :rows="2"
                placeholder="open, tilted back, rain running off the edge"
                @update:model-value="saveSoon(['props'])"
              />
              <p class="studio-hint">What the prop is doing, in words, like a person's pose.</p>
            </div>
          </details>
          <details class="studio-group" open>
            <summary>Held by</summary>
            <div class="studio-group__body">
              <AppSelect
                :model-value="optionValue(selectedProp.held_by)"
                label="Held by"
                hide-label
                :options="heldByOptions"
                data-testid="scene-held-by"
                @update:model-value="
                  selectedProp.held_by = fromOption($event);
                  save(['props']);
                "
              />
            </div>
          </details>
          <AppButton variant="ghost" size="sm" icon-left="close" @click="removeSelected"
            >Take out of the scene</AppButton
          >
        </template>

        <!-- A take -->
        <template v-else-if="shownTake">
          <details class="studio-group" open>
            <summary>{{ shownTake.kind === "clip" ? "Clip" : "Still" }}</summary>
            <div class="studio-group__body">
              <p v-if="shownTake.from_picture_id" class="studio-hint">Started from a still take.</p>
              <AppButton
                v-if="shownTake.kind === 'still'"
                variant="secondary"
                icon-left="movie-open-play-outline"
                :disabled="!clipWorkflow || shooting"
                data-testid="scene-clip-from-take"
                @click="shoot('clip', shownTake.picture_id)"
                >Make a clip from this take</AppButton
              >
              <AppButton variant="ghost" size="sm" icon-left="close" @click="dropTake(shownTake.picture_id)"
                >Take out of the takes</AppButton
              >
              <p class="studio-hint">The picture stays in the library.</p>
            </div>
          </details>
        </template>

        <!-- The location: what it is doing -->
        <template v-else>
          <details class="studio-group" open>
            <summary>Location</summary>
            <div class="studio-group__body">
              <AppSelect
                :model-value="optionValue(draft.location_set_id)"
                label="Location"
                hide-label
                :options="locationOptions"
                data-testid="scene-location"
                @update:model-value="
                  draft.location_set_id = fromOption($event);
                  save(['location_set_id']);
                "
              />
            </div>
          </details>
          <details class="studio-group" open>
            <summary>What it is doing</summary>
            <div class="studio-group__body">
              <AppInput v-model="draft.setting.weather" label="Weather" placeholder="heavy rain" @blur="save(['setting'])" />
              <AppInput v-model="draft.setting.time" label="Time" placeholder="night" @blur="save(['setting'])" />
              <AppInput
                v-model="draft.setting.happening"
                label="Happening"
                placeholder="a taxi pulling up, steam from a grate"
                @blur="save(['setting'])"
              />
              <AppInput
                v-model="draft.setting.light"
                label="Light on the cast"
                placeholder="pink neon from the left"
                @blur="save(['setting'])"
              />
            </div>
          </details>
        </template>

        <details class="studio-group" open>
          <summary>Shoot</summary>
          <div class="studio-group__body">
            <AppSelect
              :model-value="stillWorkflow ?? ''"
              label="Stills with"
              :options="stillOptions"
              placeholder="Pick a workflow"
              data-testid="scene-still-workflow"
              @update:model-value="stillWorkflow = $event || null"
            />
            <Segmented v-model="count" full :options="COUNTS" aria-label="How many takes" />
            <AppButton
              variant="primary"
              icon-left="camera-iris"
              key-hint="ctrl+enter"
              block
              :disabled="!stillWorkflow || shooting"
              :loading="shooting"
              data-testid="scene-shoot"
              @click="shoot('still')"
              >Shoot {{ count }}</AppButton
            >
            <AppSelect
              :model-value="clipWorkflow ?? ''"
              label="Clips with"
              :options="clipOptions"
              placeholder="Pick a video workflow"
              data-testid="scene-clip-workflow"
              @update:model-value="clipWorkflow = $event || null"
            />
            <AppButton
              variant="secondary"
              icon-left="movie-open-outline"
              block
              :disabled="!clipWorkflow || shooting"
              data-testid="scene-clip"
              @click="shoot('clip')"
              >Make a clip of the scene</AppButton
            >
            <p v-if="shootError" class="studio-hint scene-error" role="alert">{{ shootError }}</p>
          </div>
        </details>
        <details class="studio-group">
          <summary>The whole prompt</summary>
          <div class="studio-group__body">
            <p class="studio-hint scene-prompt" data-testid="scene-prompt">{{ shot?.prompt || "…" }}</p>
            <p v-if="shot" class="studio-hint">
              {{ shot.references.length }}
              {{ shot.references.length === 1 ? "picture goes" : "pictures go" }} with it.
            </p>
          </div>
        </details>
        <AppButton variant="ghost" size="sm" icon-left="delete-outline" @click="removeScene"
          >Delete the scene</AppButton
        >
      </div>
    </aside>
  </div>
</template>

<script setup>
/**
 * A scene's lightbox (#2819): blocking on the picture. The location's view is
 * the stage, each person and prop a box on it; the inspector shows only the
 * selection, in groups that fold, so it still works with four or more people.
 * The strip is the scene's takes, stills and clips. Shoot writes the shot
 * (`POST /studio/scenes/{id}/shot`), runs the chosen workflow with it and
 * hands the runs to the scene, which files their pictures as takes.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { VIcon } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import AppInput from "../widgets/AppInput.vue";
import AppSelect from "../widgets/AppSelect.vue";
import AppTextarea from "../widgets/AppTextarea.vue";
import FavouriteHeart from "../widgets/FavouriteHeart.vue";
import Segmented from "../widgets/Segmented.vue";
import { pictureMediaUrl, pictureThumbnailUrl } from "../../api/pictures";
import {
  deleteScene,
  followSceneRuns,
  getScene,
  patchScene,
  removeTake,
  setStudioFavourite,
  writeShot,
} from "../../api/studio";
import {
  listWorkflowCards,
  preflightWorkflowRun,
  runWorkflowCard,
} from "../../api/workflows";
import { useConfirm } from "../../composables/useConfirm";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useStudioStore } from "../../stores/useStudioStore";
import { errorMessage } from "../../utils/apiError";
import { runKey } from "../../utils/gpuMemory";
import {
  LOCATION_VIEWS,
  STUDIO_KINDS,
  fromOption,
  kindStyle,
  optionValue,
} from "../../utils/studioKinds";

const props = defineProps({
  sceneId: { type: Number, required: true },
  scenes: { type: Array, default: () => [] },
  backLabel: { type: String, default: "Back" },
});
const emit = defineEmits(["close", "go", "changed", "item-sheets"]);

const store = useStudioStore();
const noticeStore = useNoticeStore();
const { confirm } = useConfirm();

const SHOTS = [
  { id: "wide", label: "Wide" },
  { id: "medium", label: "Medium" },
  { id: "close", label: "Close" },
];
const BRINGS = [
  { id: "text", label: "Words" },
  { id: "lora", label: "LoRA" },
  { id: "refs", label: "Pictures" },
];
const COUNTS = [1, 2, 4].map((n) => ({ id: n, label: String(n) }));
const POLL_MS = 3000;

const scene = ref(null);
const draft = ref(null);
const loadError = ref("");
const selection = ref(null);
const shot = ref(null);
const shooting = ref(false);
const shootError = ref("");
const count = ref(2);
const stageEl = ref(null);
let alive = true;
let pollTimer = null;
let saveTimer = null;
let pendingFields = new Set();
// The drag in progress lets go of the window when the lightbox closes under it.
let stopDrag = null;

const index = computed(() => props.scenes.findIndex((s) => s.id === props.sceneId));
function step(delta) {
  const next = props.scenes[index.value + delta];
  if (next) emit("go", "scene", next.id);
}

function toDraft(body) {
  return {
    name: body.name,
    location_set_id: body.location_set_id,
    camera: { shot: "medium", view: 0, ...(body.camera || {}) },
    setting: { weather: "", time: "", happening: "", light: "", ...(body.setting || {}) },
    cast: (body.cast || []).map((m) => ({ ...m })),
    props: (body.props || []).map((p) => ({ ...p })),
  };
}

async function load({ keepDraft = false } = {}) {
  try {
    const body = await getScene(props.sceneId);
    if (!alive) return;
    const hadRuns = scene.value?.runs?.length ?? 0;
    scene.value = body;
    if (!keepDraft || !draft.value) draft.value = toDraft(body);
    loadError.value = "";
    for (const run of body.ended || []) {
      if (run.status === "failed") {
        noticeStore.error(`A take of ${body.name} failed. ${run.message || ""}`.trim(), {
          key: `studio-run-${run.key}`,
        });
      } else if (run.status === "lost") {
        noticeStore.warning(
          `A take of ${body.name} was still drawing when PixlStash restarted; its pictures, if any, are in the library but not in the scene.`,
          { key: `studio-run-${run.key}` },
        );
      }
    }
    if ((body.ended || []).length || hadRuns !== body.runs.length) emit("changed");
    schedulePoll();
  } catch (e) {
    if (!alive) return;
    loadError.value = `Couldn't read the scene. ${errorMessage(e, "Please try again.")}`;
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (alive && scene.value?.runs?.length) {
    pollTimer = setTimeout(() => load({ keepDraft: true }), POLL_MS);
  }
}

/** Write the fields changed; the answer is the scene, the draft stays. */
async function save(fields) {
  for (const f of fields) pendingFields.add(f);
  clearTimeout(saveTimer);
  const sending = [...pendingFields];
  pendingFields = new Set();
  if (!draft.value || !sending.length) return;
  const body = {};
  for (const f of sending) {
    if (f === "name") {
      const name = draft.value.name.trim();
      if (!name) {
        draft.value.name = scene.value.name;
        continue;
      }
      body.name = name;
    } else {
      body[f] = draft.value[f];
    }
  }
  if (!Object.keys(body).length) return;
  try {
    const answer = await patchScene(props.sceneId, body);
    if (!alive) return;
    scene.value = { ...scene.value, ...answer };
    // A cast change can put a prop down: take the props back.
    if (body.cast && !body.props) draft.value.props = answer.props.map((p) => ({ ...p }));
    emit("changed");
    refreshShot();
  } catch (e) {
    noticeStore.error(`Couldn't save the scene. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-scene-save",
    });
  }
}

function saveSoon(fields) {
  for (const f of fields) pendingFields.add(f);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => save([]), 600);
}

// ── who and what ──────────────────────────────────────────────────────────
const location = computed(() =>
  draft.value?.location_set_id != null
    ? store.byKey.get(`location:${draft.value.location_set_id}`) || null
    : null,
);
const stageUrl = computed(() => {
  const ids = location.value?.picture_ids || [];
  const id = ids[draft.value?.camera.view ?? 0] ?? ids[0];
  return id ? pictureThumbnailUrl(id) : "";
});
const viewOptions = computed(() =>
  LOCATION_VIEWS.map((label, i) => ({
    label: `Seen from: ${label}`,
    value: String(i),
    disabled: i >= (location.value?.picture_ids.length ?? 0),
  })),
);
const locationOptions = computed(() => [
  { label: "No location", value: "" },
  ...store.overview.locations.map((l) => ({ label: l.name, value: String(l.id) })),
]);
const settingWords = computed(() =>
  draft.value
    ? [draft.value.setting.weather, draft.value.setting.time].filter((w) => w?.trim()).join(" · ")
    : "",
);

const personName = (id) => store.byKey.get(`cast:${id}`)?.name ?? "Someone not in this project";
const outfitName = (id) => (id != null ? store.byKey.get(`outfit:${id}`)?.name ?? "" : "");
const propName = (id) => store.byKey.get(`prop:${id}`)?.name ?? "A prop not in this project";

function wardrobeOptions(characterId) {
  return [
    { label: "Their own clothes", value: "" },
    ...store.overview.outfits
      .filter((o) => o.character_id === characterId)
      .map((o) => ({ label: o.name, value: String(o.id) })),
  ];
}

// The first row is what the closed field reads; it cannot be picked.
const addPersonOptions = computed(() => {
  const inScene = new Set(draft.value?.cast.map((m) => m.character_id));
  return [
    { label: "Add a person…", value: "", disabled: true },
    ...store.overview.cast
      .filter((c) => !inScene.has(c.id))
      .map((c) => ({ label: c.name, value: String(c.id) })),
  ];
});
const addPropOptions = computed(() => [
  { label: store.overview.props.length ? "Add a prop…" : "No props yet", value: "", disabled: true },
  ...store.overview.props.map((p) => ({ label: p.name, value: String(p.id) })),
]);
const heldByOptions = computed(() => [
  { label: "Nobody", value: "" },
  ...(draft.value?.cast || []).map((m) => ({
    label: personName(m.character_id),
    value: String(m.character_id),
  })),
]);

function addPerson(id) {
  if (id == null) return;
  const n = draft.value.cast.length;
  const x = Math.min(0.78, 0.08 + n * 0.2);
  draft.value.cast.push({
    character_id: id,
    outfit_set_id: fromOption(wardrobeOptions(id)[1]?.value ?? ""),
    doing: "",
    pose: "",
    brings: "refs",
    box: [x, 0.2, 0.18, 0.7],
  });
  selection.value = { type: "person", index: n };
  save(["cast"]);
}

function addProp(id) {
  if (id == null) return;
  draft.value.props.push({ set_id: id, state: "", held_by: null, box: [0.45, 0.6, 0.12, 0.2] });
  selection.value = { type: "prop", index: draft.value.props.length - 1 };
  save(["props"]);
}

function removeSelected() {
  const sel = selection.value;
  if (sel?.type === "person") {
    draft.value.cast.splice(sel.index, 1);
    save(["cast"]);
  } else if (sel?.type === "prop") {
    draft.value.props.splice(sel.index, 1);
    save(["props"]);
  }
  selection.value = null;
}

const selectedPerson = computed(() =>
  selection.value?.type === "person" ? draft.value?.cast[selection.value.index] || null : null,
);
const selectedProp = computed(() =>
  selection.value?.type === "prop" ? draft.value?.props[selection.value.index] || null : null,
);
const shownTake = computed(() =>
  selection.value?.type === "take"
    ? scene.value?.takes.find((t) => t.picture_id === selection.value.pictureId) || null
    : null,
);

const bringsHint = computed(
  () =>
    ({
      text: "Only their description: the engine draws someone who fits it.",
      lora: "Their LoRA, added to the run. Two people's LoRAs in one run can blend them.",
      refs: "Their reference picture goes in with the shot, if the workflow takes pictures.",
    })[selectedPerson.value?.brings || "refs"],
);

const inspectorTitle = computed(() => {
  if (selectedPerson.value) return `${personName(selectedPerson.value.character_id)} in ${scene.value?.name}`;
  if (selectedProp.value) return `${propName(selectedProp.value.set_id)} in ${scene.value?.name}`;
  if (shownTake.value) return shownTake.value.kind === "clip" ? "A clip" : "A still";
  return location.value ? `${location.value.name} · ${scene.value?.name}` : scene.value?.name;
});
const inspectorKind = computed(() =>
  selectedPerson.value ? "cast" : selectedProp.value ? "prop" : shownTake.value ? "scene" : location.value ? "location" : "scene",
);
const inspectorStyle = computed(() => kindStyle(inspectorKind.value));
const inspectorIcon = computed(() => STUDIO_KINDS[inspectorKind.value].icon);

function selectLocation() {
  selection.value = null;
}

// ── the marks on the stage ────────────────────────────────────────────────
const marks = computed(() => {
  if (!draft.value) return [];
  const people = draft.value.cast.map((m, i) => ({
    key: `person-${i}`,
    type: "person",
    index: i,
    box: m.box || [0.1 + i * 0.2, 0.2, 0.18, 0.7],
    label: personName(m.character_id),
    words: m.pose || m.doing,
  }));
  const things = draft.value.props.map((p, i) => {
    const holder = p.held_by != null ? draft.value.cast.find((m) => m.character_id === p.held_by) : null;
    const own = p.box || [0.45, 0.6, 0.12, 0.2];
    // A held prop goes where its holder goes: at their hands, its own size.
    const at = holder?.box;
    return {
      key: `prop-${i}`,
      type: "prop",
      index: i,
      held: !!at,
      box: at
        ? [
            clamp(at[0] + at[2] * 0.55, 0, 1 - own[2]),
            clamp(at[1] + at[3] * 0.25, 0, 1 - own[3]),
            own[2],
            own[3],
          ]
        : own,
      label: holder ? `${propName(p.set_id)} · held by ${personName(holder.character_id)}` : propName(p.set_id),
      words: p.state,
    };
  });
  return [...people, ...things];
});

const isSelected = (mark) =>
  selection.value?.type === mark.type && selection.value.index === mark.index;

function select(mark) {
  selection.value = { type: mark.type, index: mark.index };
}

function markStyle(mark) {
  const [x, y, w, h] = mark.box;
  return { left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%` };
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Arrows move a box (Shift for a bigger step); Alt and the arrows size it. */
function onMarkKey(event, mark) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    select(mark);
    return;
  }
  const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
  if (!delta) return;
  event.preventDefault();
  select(mark);
  const sizing = event.altKey;
  if (mark.held && !sizing) return;
  const list = mark.type === "person" ? draft.value.cast : draft.value.props;
  const entry = list[mark.index];
  const step = event.shiftKey ? 0.1 : 0.02;
  const [x, y, w, h] = mark.box;
  const [dx, dy] = [delta[0] * step, delta[1] * step];
  entry.box = sizing
    ? [x, y, clamp(w + dx, 0.04, 1 - x), clamp(h + dy, 0.04, 1 - y)]
    : [clamp(x + dx, 0, 1 - w), clamp(y + dy, 0, 1 - h), w, h];
  saveSoon([mark.type === "person" ? "cast" : "props"]);
}

function startDrag(event, mark, mode) {
  select(mark);
  // A held prop moves with its holder; only its size is its own.
  if (mark.held && mode === "move") return;
  const rect = stageEl.value?.getBoundingClientRect();
  if (!rect?.width || !rect?.height) return;
  const list = mark.type === "person" ? draft.value.cast : draft.value.props;
  const entry = list[mark.index];
  const start = [...mark.box];
  const x0 = event.clientX;
  const y0 = event.clientY;
  let moved = false;
  const onMove = (e) => {
    const dx = (e.clientX - x0) / rect.width;
    const dy = (e.clientY - y0) / rect.height;
    if (Math.abs(dx) + Math.abs(dy) > 0.002) moved = true;
    const [x, y, w, h] = start;
    entry.box =
      mode === "move"
        ? [clamp(x + dx, 0, 1 - w), clamp(y + dy, 0, 1 - h), w, h]
        : [x, y, clamp(w + dx, 0.04, 1 - x), clamp(h + dy, 0.04, 1 - y)];
  };
  const stop = () => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    stopDrag = null;
  };
  const onUp = () => {
    stop();
    if (moved) save([mark.type === "person" ? "cast" : "props"]);
  };
  stopDrag = stop;
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

// ── the prompt and the shoot ──────────────────────────────────────────────
const cards = ref([]);
const stillWorkflow = ref(readChoice("still"));
const clipWorkflow = ref(readChoice("clip"));

function readChoice(kind) {
  try {
    return localStorage.getItem(`pixlstash:studioWorkflow:${kind}`) || null;
  } catch (e) {
    console.warn("Couldn't read the Studio's workflow choice", kind, e);
    return null;
  }
}
function keepChoice(kind, id) {
  try {
    if (id) localStorage.setItem(`pixlstash:studioWorkflow:${kind}`, id);
  } catch (e) {
    console.warn("Couldn't keep the Studio's workflow choice; it resets on reload", kind, e);
  }
}
watch(stillWorkflow, (id) => keepChoice("still", id));
watch(clipWorkflow, (id) => keepChoice("clip", id));

const isVideo = (card) => card.type === "video";
const stillOptions = computed(() =>
  cards.value
    .filter((c) => !isVideo(c))
    .sort((a, b) => (b.reference_inputs || 0) - (a.reference_inputs || 0))
    .map((c) => ({
      label: c.reference_inputs ? `${c.name} · ${c.reference_inputs} pictures in` : c.name,
      value: c.id,
    })),
);
const clipOptions = computed(() => {
  const videos = cards.value.filter(isVideo).map((c) => ({ label: c.name, value: c.id }));
  return videos.length
    ? videos
    : [{ label: "No video workflow in Workflows", value: "", disabled: true }];
});

async function loadCards() {
  try {
    const { cards: list } = await listWorkflowCards();
    if (!alive) return;
    cards.value = list;
    const ids = new Set(list.map((c) => c.id));
    if (!ids.has(stillWorkflow.value)) stillWorkflow.value = stillOptions.value[0]?.value ?? null;
    if (!ids.has(clipWorkflow.value)) {
      const i2v = list.find((c) => isVideo(c) && /image to video/i.test(c.name));
      clipWorkflow.value = i2v?.id ?? list.find(isVideo)?.id ?? null;
    }
  } catch (e) {
    console.warn("Couldn't list the workflows for the Studio", e);
  }
}

let shotEpoch = 0;
async function refreshShot() {
  const asked = (shotEpoch += 1);
  try {
    const answer = await writeShot(props.sceneId, { kind: "still" });
    if (alive && asked === shotEpoch) shot.value = answer;
  } catch (e) {
    console.warn("Couldn't write the scene's prompt", props.sceneId, e);
  }
}

/** The workflow's picture inputs a run can fill, as its pre-flight names them. */
async function pictureInputs(workflowId) {
  const check = await preflightWorkflowRun(
    { workflow_id: workflowId, count: 1, prompt: "a scene" },
    { cached: true },
  );
  return (check?.groups || [])
    .flatMap((g) => g.picture_inputs || [])
    .filter((input) => input.mode !== "fixed");
}

async function shoot(kind, fromPictureId = null) {
  const workflowId = kind === "clip" ? clipWorkflow.value : stillWorkflow.value;
  if (!workflowId || shooting.value) return;
  shooting.value = true;
  shootError.value = "";
  try {
    // Whatever is being typed goes in first.
    await save([]);
    const inputs = await pictureInputs(workflowId);
    if (fromPictureId != null && !inputs.length) {
      throw new Error("That video workflow takes no picture, so it can't start from a still. Pick an image-to-video workflow.");
    }
    const written = await writeShot(props.sceneId, {
      kind,
      ...(fromPictureId != null ? { from_picture_id: fromPictureId } : {}),
      max_references: inputs.length,
    });
    const answer = await runWorkflowCard({
      workflow_id: workflowId,
      prompt: written.prompt,
      negative: written.negative,
      inputs: written.references.slice(0, inputs.length).map((pictureId, i) => ({
        slot_label: inputs[i].slot_label,
        input_name: inputs[i].input_name,
        picture_id: pictureId,
      })),
      add_loras: written.loras.map((sha256) => ({ sha256, strength_model: 1, strength_clip: 1 })),
      count: kind === "clip" ? 1 : count.value,
      label: `${kind === "clip" ? "Clip" : "Take"} · ${scene.value.name}`,
    });
    const keys = (answer?.prompts || []).map((p) => runKey(p)).filter(Boolean);
    if (!keys.length) {
      throw new Error(answer?.error || "The workflow can't run as it is. Open it from Workflows to see why.");
    }
    const followed = await followSceneRuns(
      props.sceneId,
      keys.map((key) => ({ key, kind, from_picture_id: fromPictureId })),
    );
    if (!alive) return;
    scene.value = { ...scene.value, ...followed };
    emit("changed");
    schedulePoll();
  } catch (e) {
    shootError.value = `Couldn't shoot. ${errorMessage(e, "Please try again.")}`;
  } finally {
    shooting.value = false;
  }
}

async function dropTake(pictureId) {
  try {
    const answer = await removeTake(props.sceneId, pictureId);
    scene.value = { ...scene.value, ...answer };
    selection.value = null;
    emit("changed");
  } catch (e) {
    noticeStore.error(`Couldn't take it out. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-take-remove",
    });
  }
}

async function toggleFavourite() {
  try {
    await setStudioFavourite("scene", props.sceneId, !scene.value.favourite);
    scene.value = { ...scene.value, favourite: !scene.value.favourite };
    emit("changed");
  } catch (e) {
    noticeStore.error(`Couldn't change the favourite. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-favourite",
    });
  }
}

async function removeScene() {
  const ok = await confirm({
    title: "Delete this scene?",
    message: `“${scene.value?.name}” and its cast, props and setting go. Its takes stay in the library.`,
    confirmLabel: "Delete",
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteScene(props.sceneId);
    emit("changed");
    emit("close");
  } catch (e) {
    noticeStore.error(`Couldn't delete the scene. ${errorMessage(e, "Please try again.")}`, {
      key: "studio-scene-delete",
    });
  }
}

function drawOutfit(characterId) {
  emit("item-sheets", { kind: "outfit", character: { id: characterId, name: personName(characterId) } });
}

function onKey(event) {
  if (event.defaultPrevented) return;
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    shoot("still");
    return;
  }
  if (event.target?.closest?.("input, textarea, [role=dialog], [role=listbox]")) return;
  if (event.key === "Escape") {
    if (selection.value) selection.value = null;
    else emit("close");
  }
}

onMounted(async () => {
  window.addEventListener("keydown", onKey);
  await load();
  refreshShot();
  loadCards();
});
onBeforeUnmount(() => {
  alive = false;
  stopDrag?.();
  clearTimeout(pollTimer);
  if (saveTimer) {
    clearTimeout(saveTimer);
    save([]);
  }
  window.removeEventListener("keydown", onKey);
});
</script>

<style scoped src="./Studio.css"></style>
<style scoped>
.scene-name {
  min-width: 0;
  flex: 0 1 280px;
  height: var(--control-h-sm);
  padding: 0 var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}

.scene-name:hover,
.scene-name:focus {
  border-color: rgb(var(--v-theme-divider));
}

.scene-name:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
}

.scene-view {
  width: 170px;
}

.scene-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  overflow-y: auto;
}

.scene-stage-wrap {
  flex: 1;
  min-height: 240px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.scene-stage {
  position: relative;
  width: 100%;
  max-height: 100%;
  aspect-ratio: 5 / 3;
  border-radius: var(--radius-md);
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
  touch-action: none;
  user-select: none;
}

.scene-stage--empty {
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
}

.scene-stage__bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  pointer-events: none;
}

.scene-stage__hint {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: var(--text-sm);
}

.scene-stage--take {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(var(--v-theme-background));
}

.scene-take-media {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.scene-take-back {
  position: absolute;
  top: var(--space-3);
  left: var(--space-3);
}

/* A box is hue-free until it is selected: selection is olive, whatever the
   category; the category shows on its tag. */
.scene-mark {
  position: absolute;
  border-radius: var(--radius-sm);
  box-shadow: 0 0 0 2px rgba(var(--v-theme-on-surface), 0.6);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-1);
  padding: var(--space-2);
  cursor: grab;
}

.scene-mark:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
}

.scene-mark--on {
  box-shadow: 0 0 0 2px var(--active-bar);
  background: var(--active-wash);
}

.scene-mark__tag,
.scene-envchip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--control-h-sm);
  max-width: 100%;
  padding: 0 var(--space-3) 0 var(--space-1);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo-strong);
  color: rgb(var(--v-theme-on-secondary));
  font-size: var(--text-2xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.scene-mark__words {
  max-width: 100%;
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-secondary));
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
  text-align: center;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.scene-mark__handle {
  position: absolute;
  right: calc(-1 * var(--space-2));
  bottom: calc(-1 * var(--space-2));
  width: var(--space-3);
  height: var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--active-bar);
  cursor: nwse-resize;
}

.scene-envchip {
  position: absolute;
  left: var(--space-3);
  top: var(--space-3);
  border: 1px dashed rgba(var(--v-theme-on-secondary), 0.5);
  cursor: pointer;
  font: inherit;
  font-size: var(--text-2xs);
}

.scene-envchip--on {
  border: 1px solid var(--active-bar);
}

.scene-castbar {
  flex: none;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.scene-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--control-h);
  padding: 0 var(--space-3) 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: inherit;
  font: inherit;
  font-size: var(--text-sm);
  cursor: pointer;
}

.scene-chip--on {
  background: var(--active-wash);
  border-color: var(--active-bar);
}

.scene-add {
  width: 160px;
}

.scene-takes {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  overflow-x: auto;
  padding-bottom: var(--space-1);
}

.scene-takes__label {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-right: var(--space-2);
}

.scene-take {
  position: relative;
  flex: none;
  width: 72px;
  height: 72px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
  cursor: pointer;
}

.scene-take img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.scene-take--on {
  box-shadow: 0 0 0 2px var(--active-bar);
}

.scene-take--drawing {
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed rgba(var(--v-theme-on-surface), 0.35);
  cursor: default;
}

.scene-take__clip {
  position: absolute;
  left: var(--space-1);
  bottom: var(--space-1);
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-2) 0 var(--space-1);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo-strong);
  color: rgb(var(--v-theme-on-secondary));
  font-size: var(--text-2xs);
}

.scene-inspector-title {
  overflow: hidden;
  text-overflow: ellipsis;
}

.scene-prompt {
  color: rgb(var(--v-theme-on-surface));
  white-space: pre-wrap;
}

.scene-error {
  color: rgb(var(--v-theme-error));
}
</style>
