<template>
  <!-- The version the workflow is on, and what can be done with an earlier
       one: go back to it (#1850, rule 3 of #1846), duplicate it or open it in
       ComfyUI (#1792). A quiet chip on its own line under the picture
       count: the count shares its line with the star chart, and the three do
       not fit the 288px rail together. -->
  <span ref="root" class="wfver">
    <v-menu
      v-model="open"
      location="bottom start"
      :offset="4"
      @after-enter="enterRows"
    >
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          class="wfver-btn"
          type="button"
          aria-haspopup="menu"
          :aria-expanded="open ? 'true' : 'false'"
          data-testid="wftab-version"
          @keydown="noteArrow"
        >
          <!-- What the menu is for is said before it is opened. -->
          <Tooltip
            text="Go back to, duplicate or open an earlier version"
            activator="parent"
          />
          <v-icon size="16" aria-hidden="true">mdi-history</v-icon>
          <span>{{ label }}</span>
          <v-icon size="16" aria-hidden="true">mdi-chevron-down</v-icon>
        </button>
      </template>
      <div
        ref="surface"
        class="ctx-menu wfver-menu"
        role="menu"
        aria-label="Versions"
        tabindex="-1"
        data-testid="wftab-version-menu"
        @keydown="onSurfaceKeydown"
      >
        <div class="section-label ctx-label" aria-hidden="true">Versions</div>
        <!-- Newest first, as the server sends them. The current one is a
             checked radio row, so it is said and not only drawn; choosing it
             just closes the menu. An earlier one opens a submenu of what can
             be done with it (#1792): going back is one of three, and none
             acts on a single click. The leading slot is kept on every row so
             the numbers share a left edge. Named outright: the two spans
             would otherwise be read run together, "Version 32 Oct". -->
        <template v-for="row in versions ?? []" :key="row.version">
          <button
            v-if="row.current"
            class="ctx-item"
            type="button"
            role="menuitemradio"
            aria-checked="true"
            :aria-label="partsOf(row).join(', ')"
            :data-version="row.version"
            @click="open = false"
          >
            <v-icon class="ctx-icon" aria-hidden="true">mdi-check</v-icon>
            <span class="ctx-label-text">Version {{ row.version }}</span>
            <span class="ctx-meta">{{ partsOf(row).slice(1).join(" · ") }}</span>
          </button>
          <div
            v-else
            class="ctx-submenu-wrap"
            :class="{ 'ctx-flip-sub': flip }"
            @mouseenter="openSub(row, $event.currentTarget)"
            @mouseleave="leaveSub"
            @focusout="focusOut"
          >
            <!-- Opens, never toggles, and stops its click: a `v-menu` closes
                 on any click in its content. -->
            <button
              class="ctx-item"
              type="button"
              role="menuitem"
              aria-haspopup="menu"
              :aria-expanded="sub === row.version ? 'true' : 'false'"
              :aria-label="partsOf(row).join(', ')"
              :data-version="row.version"
              @click.stop="openSub(row, $event.currentTarget.parentElement)"
            >
              <span class="ctx-icon" aria-hidden="true"></span>
              <span class="ctx-label-text">Version {{ row.version }}</span>
              <span class="ctx-meta">{{
                partsOf(row).slice(1).join(" · ")
              }}</span>
              <v-icon class="ctx-arrow" aria-hidden="true">mdi-chevron-right</v-icon>
            </button>
            <div
              v-if="sub === row.version"
              class="ctx-submenu"
              role="menu"
              :aria-label="`Version ${row.version}`"
              data-testid="wftab-version-actions"
              @keydown="subKeydown"
            >
              <button
                class="ctx-item"
                type="button"
                role="menuitem"
                data-action="go-back"
                @click="act('go-back', row)"
              >
                <v-icon class="ctx-icon" aria-hidden="true">mdi-history</v-icon>
                <span class="ctx-label-text">Go back to this version</span>
              </button>
              <button
                class="ctx-item"
                type="button"
                role="menuitem"
                data-action="duplicate"
                @click="act('duplicate', row)"
              >
                <v-icon class="ctx-icon" aria-hidden="true"
                  >mdi-content-copy</v-icon
                >
                <span class="ctx-label-text">Duplicate</span>
              </button>
              <button
                v-if="canOpenComfyui"
                class="ctx-item"
                type="button"
                role="menuitem"
                data-action="open-comfyui"
                @click="act('open-comfyui', row)"
              >
                <ComfyuiIcon class="ctx-icon" />
                <span class="ctx-label-text">Open in ComfyUI</span>
              </button>
            </div>
          </div>
        </template>
        <!-- The read is out, or failed: a row that says so, never an empty
             surface. Opening the menu again asks again. -->
        <button
          v-if="!versions"
          class="ctx-item ctx-item--disabled"
          type="button"
          role="menuitem"
          aria-disabled="true"
          data-testid="wftab-version-unread"
        >
          <span class="ctx-label-text">{{
            failed
              ? "Could not read the versions just now."
              : "Reading the versions…"
          }}</span>
        </button>
      </div>
    </v-menu>
  </span>
</template>

<script>
/**
 * A version's day as the head and the menu say it: `today`, `2 Oct`, and the
 * year too once it is not this one. "" for a stamp that is missing or will not
 * parse.
 *
 * @param {string|null} iso - when the version was stored. A stamp with no zone
 *   is UTC, as the backend's naive stamps are everywhere else in the app.
 * @param {Date} [now]
 * @returns {string}
 */
export function versionDay(iso, now = new Date()) {
  if (!iso) return "";
  const text = String(iso);
  const naive = text.includes("T") && !/(Z|[+-]\d{2}:\d{2})$/.test(text);
  const at = new Date(naive ? `${text}Z` : text);
  if (Number.isNaN(at.getTime())) return "";
  if (at.toDateString() === now.toDateString()) return "today";
  return at.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(at.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}
</script>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import { onMenuKeydown } from "../../utils/menuKeyboard";
import ComfyuiIcon from "../widgets/ComfyuiIcon.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** The current version's number. */
  number: { type: Number, required: true },
  /** When the current version was stored (ISO 8601), or null. */
  at: { type: String, default: null },
  /**
   * `GET /workflows/{id}/versions`'s rows, newest first:
   * `{version, created_at, reason, current}`. Null while they are unread.
   */
  versions: { type: Array, default: null },
  /** The read of `versions` failed. */
  failed: { type: Boolean, default: false },
  /** Whether an earlier version can be opened in ComfyUI from here. */
  canOpenComfyui: { type: Boolean, default: false },
});

/** `go-back`, `duplicate` and `open-comfyui` each carry the version's row. */
const emit = defineEmits(["open", "go-back", "duplicate", "open-comfyui"]);

const open = ref(false);
const root = ref(null);

const label = computed(() =>
  [`Version ${props.number}`, versionDay(props.at)].filter(Boolean).join(" · "),
);

/**
 * A row's number, its day and the reason the save stored. The reason is the
 * server's own words, printed as sent: null (the version a workflow arrived
 * as) leaves the number and the day.
 */
function partsOf(row) {
  return [
    `Version ${row.version}`,
    versionDay(row.created_at),
    row.reason,
  ].filter(Boolean);
}

/**
 * The arrow that is opening the menu, until it has opened.
 *
 * `v-menu` answers ArrowDown on a closed menu by opening it and then focusing
 * the first row two timeouts later, which in a browser is before the content
 * is laid out: the focus call lands on nothing, and the keyboard is left on
 * the chip of a menu that says it is expanded. So the arrow is remembered
 * here and answered once the menu has entered.
 */
let opening = "";
const surface = ref(null);

// Every arrow on the chip, not only one on a closed menu: `v-menu`'s own
// handler runs first on the same keydown and has already set `open` by the
// time this one is called. One pressed in an open menu is forgotten when the
// menu closes, and nothing reads it before then.
function noteArrow(event) {
  if (["ArrowDown", "ArrowUp"].includes(event.key)) opening = event.key;
}

/** Entered by an arrow: the first row for ↓, the last for ↑. */
function enterRows() {
  const key = opening;
  opening = "";
  if (!key) return;
  const rows = [
    ...(surface.value?.querySelectorAll(".ctx-item:not(.ctx-item--disabled)") ??
      []),
  ];
  // Unread: nothing to land on but the surface, which says why.
  (key === "ArrowUp" ? rows.at(-1) : rows[0])?.focus();
  if (!rows.length) surface.value?.focus();
}

/** Which earlier version's submenu is open, by number, or null. */
const sub = ref(null);
// Opened leftward when the menu sits too near the window's right edge for
// it, as the rail's menu does: the rail is the window's right edge.
const flip = ref(false);

/** Open *row*'s submenu beside `wrap`, its first row focused when `focus`. */
async function openSub(row, wrap, focus = false) {
  flip.value = wrap.getBoundingClientRect().right + 200 > window.innerWidth;
  sub.value = row.version;
  if (!focus) return;
  await nextTick();
  wrap.querySelector(".ctx-submenu .ctx-item")?.focus();
}

// Not while a row in it holds focus: a pointer crossing the row after the
// keyboard opened it would take the focused row away with it.
function leaveSub(event) {
  if (!event.currentTarget.contains(document.activeElement)) sub.value = null;
}

function focusOut(event) {
  if (!event.currentTarget.contains(event.relatedTarget)) sub.value = null;
}

/**
 * The surface's keys: the arrows between rows, and → / Enter / Space on an
 * earlier version's row open its submenu on the first row.
 *
 * One handler on the surface rather than one on each row as well: with a
 * keydown handler on the row, the surface's own never ran for a key pressed
 * there in the built app (the arrows stopped at the first earlier row), while
 * jsdom ran both.
 */
function onSurfaceKeydown(event) {
  const target = event.target;
  const version =
    target?.matches?.("[aria-haspopup][data-version]") &&
    ["ArrowRight", "Enter", " "].includes(event.key)
      ? props.versions?.find((row) => String(row.version) === target.dataset.version)
      : null;
  if (!version) {
    onMenuKeydown(event);
    return;
  }
  event.preventDefault();
  openSub(version, target.parentElement, true);
}

/** ← backs out to the version's row; the parent's cursor never moves too. */
function subKeydown(event) {
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    const wrap = event.currentTarget.parentElement;
    sub.value = null;
    wrap.querySelector(".ctx-item")?.focus();
  } else {
    onMenuKeydown(event);
  }
  if (["ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.stopPropagation();
  }
}

function act(verb, row) {
  open.value = false;
  emit(verb, row);
}

/** Put the keyboard on the chip. */
function focus() {
  root.value?.querySelector(".wfver-btn")?.focus();
}

// A closed menu takes the row that had focus with it, and focus would fall to
// the page: it goes back to the chip, unless the click that closed the menu
// landed on another control.
watch(open, async (now) => {
  if (now) {
    emit("open");
    return;
  }
  opening = "";
  sub.value = null;
  await nextTick();
  const active = document.activeElement;
  if (!active || active === document.body || active.closest(".wfver-menu")) {
    focus();
  }
});

defineExpose({ focus });
</script>

<style scoped>
.wfver {
  display: flex;
}

/* The mock-up's `.ver`: metadata until pointed at. Pulled left by its own
   padding so the glyph sits on the head's left edge. */
.wfver-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--control-h-sm);
  margin-left: calc(-1 * var(--space-3));
  padding: 0 var(--space-2) 0 var(--space-3);
  border-radius: var(--radius-sm);
  font: inherit;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  white-space: nowrap;
  transition:
    background var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard);
}

.wfver-btn:hover,
.wfver-btn[aria-expanded="true"] {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.wfver-menu {
  min-width: 280px;
}
</style>
