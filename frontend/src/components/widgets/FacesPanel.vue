<template>
  <!-- The faces editor behind the Edit faces pencil, in the grid rail and the
       lightbox sidebar alike. It holds a person picker per face, so it is a
       panel rather than a menu, and wears the toolbar popovers' chrome
       (`.tbm`): a caret up at the pencil, a header with a count and a close
       button, a row per face, and a footer that says what Escape does.

       Drawn in place by its host rather than as a Vuetify menu: the pickers
       float their own lists over the page, and a Vuetify menu reads a click in
       one as a click outside and closes under it. -->
  <div
    :id="id"
    ref="rootEl"
    class="tbm faces-panel"
    :class="{ 'faces-panel--on-dark': onDark }"
    role="dialog"
    aria-label="Faces"
    @keydown.esc.stop.prevent="close(true)"
  >
    <span class="tbm-caret" :style="caretStyle"></span>
    <div class="tbm-header faces-panel__head">
      <span class="tbm-title">Faces</span>
      <span v-if="rows.length" class="faces-panel__count">{{ rows.length }}</span>
      <span class="tbm-spacer"></span>
      <AppBarButton
        icon="close"
        :icon-size="16"
        tooltip="Close"
        :on-dark="onDark"
        @click="close(true)"
      />
    </div>
    <div class="faces-panel__body">
      <p v-if="!rows.length" class="faces-panel__empty">No faces detected</p>
      <div
        v-for="row in rows"
        :key="row.key"
        class="faces-panel__row"
        :class="{
          'faces-panel__row--unknown': row.characterId == null,
          'faces-panel__row--on': row.key === focusKey,
        }"
        :style="{ '--face-hue': row.hue }"
        @mouseenter="emit('hover', row)"
        @mouseleave="emit('hover', null)"
      >
        <span class="faces-panel__crop">
          <span :style="row.cropStyle"></span>
        </span>
        <div class="faces-panel__meta">
          <span class="faces-panel__label">{{ row.label }}</span>
          <AddToEntityControl
            :ref="(el) => setPicker(row.key, el)"
            type="face"
            float-menu
            :allow-create="allowCreate"
            :subject-ids="[]"
            :force-dark="onDark"
            :face-id="row.id"
            :label="row.characterId == null ? 'Who is this?' : null"
            :assigned-character-id="row.characterId"
            :assigned-character-name="row.name"
            :readonly="readonly"
            :disabled="!row.id || readonly"
            @assign="emit('assign', row, $event)"
            @unassign="emit('unassign', row)"
            @create="emit('create', row, $event)"
          />
        </div>
      </div>
    </div>
    <div class="tbm-footer">
      <slot name="footer" />
      <span class="faces-panel__esc"><kbd class="fm-kbd">Esc</kbd> closes</span>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import AddToEntityControl from "./AddToEntityControl.vue";
import AppBarButton from "./AppBarButton.vue";

const props = defineProps({
  /** The panel's id: its triggers name it in `aria-controls`, which is also
   *  how a press on one is told from a press outside. */
  id: { type: String, required: true },
  /** One per face: `{ key, id, characterId, name, label, hue, cropStyle }`.
   *  `cropStyle` draws the face inside a 36px square. */
  rows: { type: Array, default: () => [] },
  /** The pencil that opened the panel: the caret points at its centre and
   *  focus returns to it when the panel is dismissed. */
  anchor: { type: Object, default: null },
  /** The row to start on (a face that was clicked); else the first unnamed. */
  focusKey: { type: [String, Number], default: null },
  onDark: { type: Boolean, default: false },
  allowCreate: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "hover", "assign", "unassign", "create"]);

const rootEl = ref(null);
const caretStyle = ref({ right: "calc(16px - 5.5px)" });
const pickers = new Map();

function setPicker(key, el) {
  if (el) pickers.set(key, el);
  else pickers.delete(key);
}

/** Put the keyboard on one face's picker, e.g. after a dialog it opened. */
function focusRow(key) {
  pickers.get(key)?.focusTrigger?.();
}

function close(returnFocus) {
  emit("hover", null);
  emit("close");
  if (returnFocus) props.anchor?.focus?.();
}

// A press anywhere else closes it, except on a trigger (which toggles it
// itself) or in a picker's floating list.
function onOutsidePointer(e) {
  const t = e.target;
  if (rootEl.value?.contains(t)) return;
  if (
    t instanceof Element &&
    (t.closest(`[aria-controls="${props.id}"]`) || t.closest(".ate-menu"))
  ) {
    return;
  }
  close(false);
}

onMounted(async () => {
  document.addEventListener("pointerdown", onOutsidePointer, true);
  await nextTick();
  // ponytail: measured once on open; the panel is short-lived, so a rail
  // resized while it is open leaves the caret where it was.
  if (props.anchor && rootEl.value) {
    const a = props.anchor.getBoundingClientRect();
    const p = rootEl.value.getBoundingClientRect();
    if (a.width) {
      caretStyle.value = { left: `${a.left + a.width / 2 - p.left - 5.5}px` };
    }
  }
  const start =
    props.rows.find((r) => r.key === props.focusKey) ??
    props.rows.find((r) => r.characterId == null && r.id) ??
    props.rows[0];
  if (start) focusRow(start.key);
  else rootEl.value?.querySelector("button")?.focus();
});

onBeforeUnmount(() =>
  document.removeEventListener("pointerdown", onOutsidePointer, true),
);

defineExpose({ focusRow });
</script>

<style scoped>
.faces-panel {
  display: flex;
  flex-direction: column;
}

/* The lightbox's always-dark sidebar: the panel takes the dark surface in both
   themes, as the person menus there do (`.ctx-menu--on-dark`). */
.faces-panel--on-dark,
.faces-panel--on-dark .tbm-caret {
  --v-theme-panel: var(--v-theme-dark-surface);
  --v-theme-on-panel: var(--v-theme-on-dark-surface);
  border-color: rgba(var(--v-theme-on-dark-surface), 0.14);
}

.faces-panel--on-dark .tbm-header,
.faces-panel--on-dark .tbm-footer {
  border-color: rgba(var(--v-theme-on-dark-surface), 0.1);
}

.faces-panel__head {
  padding-left: var(--space-5);
  padding-right: var(--space-2);
}

.faces-panel__count {
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-panel), 0.12);
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
}

.faces-panel__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-height: min(60vh, 420px);
  overflow-y: auto;
  padding: var(--space-3);
  /* The bounded-list bar (visual-language.md): thin, always shown. */
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-panel), 0.4) transparent;
}

.faces-panel__row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-2);
  border-radius: var(--radius-sm);
}

.faces-panel__row--on {
  background: rgba(var(--v-theme-on-panel), 0.16);
}

/* The face in a 36px square, ringed in the hue its box has on the picture;
   an unnamed face is dashed and muted, as its crop in the sidebar is. */
.faces-panel__crop {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background-color: rgba(var(--v-theme-on-panel), 0.06);
  box-shadow: 0 0 0 1.5px var(--face-hue);
}

.faces-panel__crop > span {
  flex: none;
  background-repeat: no-repeat;
}

.faces-panel__row--unknown .faces-panel__crop {
  box-shadow: none;
  outline: 1px dashed
    rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.faces-panel__meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.faces-panel__label {
  font-size: var(--text-2xs);
  color: var(--face-hue);
}

/* The hues are picked to read on dark (the boxes sit on a picture); on the
   light theme's panel a yellow or green label would not, so the crop's ring
   carries the hue there and the label takes the quiet ink. */
.v-theme--pixlStashLight
  .faces-panel:not(.faces-panel--on-dark)
  .faces-panel__label {
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

/* The picker's trigger is the row's select: full width, a field's height and
   border, the person (or the placeholder) ellipsed before the chevron. */
.faces-panel__meta :deep(.ate) {
  width: 100%;
}

.faces-panel__meta :deep(.ate-btn) {
  width: 100%;
  height: var(--control-h);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-input-background));
  color: rgb(var(--v-theme-on-panel));
  font-size: var(--text-sm);
}

.faces-panel--on-dark .faces-panel__meta :deep(.ate-btn) {
  border-color: rgba(var(--v-theme-on-dark-surface), 0.15);
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
}

.faces-panel--on-dark .fm-kbd {
  border-color: rgba(var(--v-theme-on-dark-surface), 0.3);
  background: transparent;
}

.faces-panel__meta :deep(.ate-label) {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: left;
}

.faces-panel__row--unknown :deep(.ate-label) {
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.faces-panel__empty {
  margin: 0;
  padding: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}

.faces-panel__esc {
  margin-left: auto;
}
</style>
