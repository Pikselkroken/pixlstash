<template>
  <div
    ref="rootRef"
    class="ate"
    :class="{
      open: menuOpen,
      disabled,
      'ate--readonly': readonly,
      'ate--flyout': placement === 'right',
      'ate--flip': flyoutFlipped,
      'ate--force-dark': forceDark,
    }"
    @mouseenter="onFlyoutMouseenter"
    @mouseleave="onFlyoutMouseleave"
  >
    <button
      class="ate-btn"
      type="button"
      :disabled="disabled"
      :aria-expanded="menuOpen"
      aria-haspopup="listbox"
      :aria-controls="listboxId"
      :aria-label="config.ariaLabel"
      @click.stop="toggleMenu"
    >
      <v-icon size="18">{{ config.icon }}</v-icon>
      <span class="ate-label">{{ effectiveLabel }}</span>
      <v-icon size="16" class="ate-chevron">{{
        placement === "right" ? "mdi-chevron-right" : "mdi-chevron-down"
      }}</v-icon>
    </button>

    <Teleport :disabled="!floating" to="body">
      <div
        ref="menuRef"
        class="ate-menu"
        :style="menuStyle"
        :class="{
          open: menuOpen,
          flyout: placement === 'right',
          'force-dark': forceDark,
          'ate-menu--floating': floating,
        }"
        @keydown="onMenuKeydown"
      >
        <div class="ate-search">
          <v-icon size="14">mdi-magnify</v-icon>
          <input
            ref="searchInputRef"
            v-model="searchQuery"
            type="text"
            :aria-label="config.searchLabel"
            :placeholder="config.searchPlaceholder"
            @keydown.enter.prevent="onSearchEnter"
          />
        </div>

        <!-- Only the item list scrolls; the search box above and status below stay
             pinned. The list's max height is sized to the viewport in sizeMenu(), so
             a long list (or a flyout opened low on screen) scrolls instead of
             running off the bottom. -->
        <div class="ate-list">
          <div v-if="isLoading" class="ate-empty">{{ config.loadingText }}</div>
          <!-- No-match empty state (character only): the empty state itself
               becomes the create action, quoting the typed query. Enter in the
               search box activates it too (onSearchEnter). -->
          <button
            v-else-if="showEmptyCreateRow"
            :class="[
              'ate-item',
              'ate-item--create',
              { 'ate-item--disabled': createDisabled },
            ]"
            type="button"
            :disabled="createDisabled"
            @click.stop="requestCreate"
          >
            <v-icon size="16" class="ate-item-check">mdi-account-plus</v-icon>
            <span class="ate-item-name">Create "{{ trimmedQuery }}"…</span>
          </button>
          <div v-else-if="filteredItems.length === 0" class="ate-empty">
            {{ config.emptyText }}
          </div>
          <!-- Only the entity rows are the listbox. The loading / empty / create
               states above are not options and must stay outside it, or a screen
               reader counts them as choosable entries. -->
          <div
            :id="listboxId"
            role="listbox"
            :aria-label="config.listLabel"
            :aria-multiselectable="isMultiSelect ? 'true' : undefined"
          >
            <button
              v-for="item in filteredItems"
              :key="item.key"
              :class="[
                'ate-item',
                {
                  'ate-item--disabled': isItemDisabled(item),
                  'ate-item--checked':
                    !isFace && getItemState(item) === 'checked',
                },
              ]"
              type="button"
              role="option"
              :aria-selected="getAriaSelected(item)"
              :disabled="isItemDisabled(item)"
              :title="isItemLocked(item) ? 'This set is locked' : undefined"
              @click.stop="toggleItem(item)"
            >
              <v-icon size="16" class="ate-item-check">
                {{ getItemGlyph(item) }}
              </v-icon>
              <span class="ate-item-name">{{ item.name }}</span>
              <!-- Partial membership is carried as text folded into the
                   accessible name ("Vacation 2024, partially applied"), which
                   is announced far more reliably than aria-checked="mixed" on
                   an option. -->
              <span
                v-if="!isFace && getItemState(item) === 'partial'"
                class="visually-hidden"
                >, partially applied</span
              >
              <span v-if="isSet" class="ate-item-meta">
                <v-icon
                  v-if="isItemLocked(item)"
                  size="14"
                  class="ate-item-lock"
                  >mdi-lock-outline</v-icon
                >
                <span
                  v-else-if="isLastUsedItem(item)"
                  class="ate-item-shortcut"
                  title="Press A to add to this set"
                  >A</span
                >
              </span>
            </button>
          </div>
        </div>

        <!-- Pinned create affordance (character only, allowCreate hosts
             only): sits below the scrolling list so it stays visible however
             long the list is. Creation itself is the host's job; this only
             emits "create". -->
        <div v-if="canCreate" class="ate-create-pinned">
          <button
            :class="[
              'ate-item',
              'ate-item--create',
              { 'ate-item--disabled': createDisabled },
            ]"
            type="button"
            :disabled="createDisabled"
            @click.stop="requestCreate"
          >
            <v-icon size="16" class="ate-item-check">mdi-account-plus</v-icon>
            <span class="ate-item-name">New person…</span>
          </button>
        </div>

        <div
          v-if="statusMessage"
          class="ate-status"
          role="status"
          aria-live="polite"
        >
          {{ statusMessage }}
        </div>
      </div>
    </Teleport>

    <div
      v-if="isSet && statusMessage && !menuOpen"
      class="ate-shortcut-status"
      role="status"
      aria-live="polite"
    >
      {{ statusMessage }}
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import {
  getPictureSetMembership,
  addPictureToSet,
  removePictureFromSet,
} from "../../api/pictureSets";
import { getProjectMembership } from "../../api/projects";
import {
  getCharacterMembership,
  addCharacterFaces,
  removeCharacterFaces,
} from "../../api/characters";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { errorDetail } from "../../utils/apiError";
import { API_BASE_URL } from "../../utils/apiClient";
const props = defineProps({
  // 'set' | 'project' | 'character' | 'face'.
  //
  // `face` is a SEPARATE single-select mode, deliberately not bolted onto the
  // character path: a face has exactly one person or none, so the character
  // mode's tri-state checkboxes, toggle semantics and picture-id writes are all
  // wrong for it. It lists people with radio glyphs plus an Unassigned row and
  // performs NO writes of its own, emitting `assign` / `unassign` so the host
  // keeps its face-level API calls. It lives here rather than in a local
  // overlay menu because the `.ate-*` skin is scoped to this file, and one
  // create rule has to serve both call sites.
  type: { type: String, required: true },
  backendUrl: { type: String, default: () => API_BASE_URL },
  // The subjects the tri-state is computed across. Named for what it is rather
  // than for pictures, because the model shelf attaches ADAPTERS through this
  // same picker (shelf plan F3) and an adapter is not a picture.
  //
  // `required` on purpose, and it is the only protection that works here: every
  // call site stubs this component in its tests, so a binding that goes missing
  // would land in `$attrs` with no warning, leave this list empty, and make the
  // menu open with every row unchecked and every click a no-op. Vue warns for a
  // missing REQUIRED prop; it says nothing about an unknown attribute. Face
  // mode has no subject list and passes `[]`.
  subjectIds: { type: Array, required: true },
  // Membership supplied by the host: `item.key -> Set<string>` of subject ids.
  //
  // Supplying it is the single switch into host-driven mode. The internal
  // readers below answer "which of these PICTURES are in each entity", which
  // only the picture hosts can ask; the model shelf already has every adapter's
  // attachments on the rows it drew, so it hands them straight in and no read
  // happens at all. Null keeps the picture path exactly as it was.
  membership: { type: Object, default: null },
  // Emit the intent and write nothing. Not a new behaviour: `face` has always
  // worked this way and so has `project`, which emits `selected` and updates
  // optimistically. This names it so a fourth host can ask for it, and so the
  // two existing cases stop reading as special.
  //
  // Supplying `membership` implies it. A host that owns the data owns the
  // writes; "your membership, my API calls" is a combination with no meaning
  // and no caller, and leaving it representable is how it eventually gets
  // written by accident.
  hostOwnsWrites: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false },
  label: { type: String, default: null },
  includeDeletedMembers: { type: Boolean, default: false },
  expandStacks: { type: Boolean, default: true },
  placement: { type: String, default: "bottom" },
  forceDark: { type: Boolean, default: false },
  // Ids of locked sets. A locked set can't take or drop members, so its row is
  // greyed and unselectable here (membership in *unlocked* sets is unaffected).
  lockedSetIds: { type: Object, default: () => new Set() },
  // Opt-in create affordance (character type only): the pinned "New person…"
  // row, the no-match Create "query"… row, and Enter-on-no-match. Off by
  // default because only hosts that handle the "create" event may show it; a
  // visible row that does nothing would be worse than no affordance. Enabled
  // only by ImageGridContextMenu (#645).
  allowCreate: { type: Boolean, default: false },
  // Opt-in: teleport the menu to <body> and position it against the viewport
  // instead of rendering it in place.
  //
  // The default (in place, `position: absolute`) is only safe when no ancestor
  // clips or scrolls, which is true of every original call site: the grid
  // context menu is itself fixed and teleported, and SelectionMenu and the
  // overlay's top chrome have no clipping ancestor. It is NOT true inside the
  // overlay's Faces panel, where `.overlay-sidebar` is `overflow: hidden` and
  // `.face-assign-grid` is `overflow-y: auto`: an absolutely positioned menu
  // there is both clipped AND inflates the scroller's extent, which is the
  // spurious scrollbar this prop exists to remove.
  //
  // Host layout, not entity type, decides this, which is why it is a prop and
  // not tied to `type === "face"`. Incompatible with `placement="right"`: the
  // `.ate--flyout` rules position the menu at `left: 100%` of the root, which
  // has no meaning once the node has left its parent. That is now ENFORCED and
  // not merely stated - see `floating`, which drops the request rather than
  // honouring a pair with no coherent behaviour.
  floatMenu: { type: Boolean, default: false },
  // Face mode only: which face the menu acts on, and who it currently shows.
  faceId: { type: [String, Number], default: null },
  assignedCharacterId: { type: [String, Number], default: null },
  assignedCharacterName: { type: String, default: "" },
});

const emit = defineEmits([
  "added",
  "removed",
  "selected",
  "create",
  "assign",
  "unassign",
  "attach",
  "detach",
]);

// --- Type-derived helpers ---
const isSet = computed(() => props.type === "set");
const isProject = computed(() => props.type === "project");
const isCharacter = computed(() => props.type === "character");
const isFace = computed(() => props.type === "face");
// Both people modes list characters and can offer the create row.
const listsPeople = computed(() => isCharacter.value || isFace.value);
// A picture can be in many sets and carry many characters, but it has exactly
// one project and a face has exactly one person - so only the first two are a
// multi-selectable listbox.
const isMultiSelect = computed(() => isSet.value || isCharacter.value);

// `searchLabel` and `listLabel` are the accessible names for the search box and
// the option list. They are separate from the placeholder because a placeholder
// is not a label: it disappears as soon as the user types.
const config = computed(() => {
  if (isSet.value) {
    return {
      icon: "mdi-folder-plus",
      ariaLabel: "Set",
      searchPlaceholder: "Search sets...",
      searchLabel: "Search sets",
      listLabel: "Sets",
      loadingText: "Loading sets...",
      emptyText: "No sets found",
    };
  }
  if (isProject.value) {
    return {
      icon: "mdi-briefcase-edit-outline",
      ariaLabel: "Set project",
      searchPlaceholder: "Search projects...",
      searchLabel: "Search projects",
      listLabel: "Projects",
      loadingText: "Loading projects...",
      emptyText: "No projects found",
    };
  }
  if (isFace.value) {
    return {
      icon: "mdi-account-outline",
      ariaLabel: "Assign this face to a person",
      searchPlaceholder: "Search people...",
      searchLabel: "Search people",
      listLabel: "People",
      loadingText: "Loading people...",
      emptyText: "No people found",
    };
  }
  return {
    icon: "mdi-account-plus",
    ariaLabel: "Set character",
    searchPlaceholder: "Search characters...",
    searchLabel: "Search characters",
    listLabel: "Characters",
    loadingText: "Loading characters...",
    emptyText: "No characters found",
  };
});

const effectiveLabel = computed(() => {
  if (props.label !== null) return props.label;
  if (isSet.value) return "Set";
  if (isProject.value) return "Project";
  // The face trigger reads as the current state, like the select it replaced.
  if (isFace.value) return props.assignedCharacterName || "Unassigned";
  return "Person";
});

// --- Core state ---
const rootRef = ref(null);
const menuRef = ref(null);
const searchInputRef = ref(null);
const menuOpen = ref(false);
const searchQuery = ref("");
const statusMessage = ref("");
const membersById = ref({}); // key: item.key → Set<string> of subject IDs

/**
 * The membership in play: the host's when it supplied one, else what was read.
 *
 * Resolved once so no reader has to remember which mode it is in, and so the
 * host's map cannot be half-applied - every consumer of membership goes through
 * this.
 */
const effectiveMembers = computed(() => props.membership ?? membersById.value);

/**
 * Whether this control writes, or only announces.
 *
 * `face` and `project` were already announce-only before the flag existed, so
 * they are listed here rather than converted: the behaviour is unchanged and
 * the flag simply has three users on its first day instead of one.
 */
const writesAreHosted = computed(
  () =>
    props.hostOwnsWrites ||
    Boolean(props.membership) ||
    isFace.value ||
    isProject.value,
);
// The trigger's aria-controls target. Several of these controls sit in one menu,
// so the id has to be per-instance.
const listboxId = useId();
// Membership is a live per-selection read, so until it lands we know which
// entities exist but not which ones this selection is already in. Toggling is
// held back until then; the list itself renders immediately.
const membershipLoaded = ref(false);
let statusTimer = null;

// The entity lists are shared, cached and revalidated centrally, so this menu
// renders from whatever the store already has and never waits on the network.
const entityLists = useEntityListsStore();
const entityKind = computed(() =>
  isSet.value ? "sets" : isProject.value ? "projects" : "characters",
);
const isLoading = computed(() => entityLists.isLoading(entityKind.value));

// Set-only state
const lastUsedItem = ref(null); // { id, name }

// Character-only state
const picturesWithFaces = ref(new Set());

const flyoutFlipped = ref(false);
const flyoutClickedOpen = ref(false);

/**
 * Whether the menu actually floats, which is `floatMenu` MINUS the combination
 * it cannot serve.
 *
 * The prop doc has said "incompatible with `placement="right"`" since it was
 * written, and saying it was not enough: the model shelf's row context menu
 * passed both, and the flyout came out below the row it hangs off and outside
 * the `.ate` root it hovers off, so reaching for it fired `mouseleave` and shut
 * it. Refused here rather than at each call site - the guard belongs where all
 * of them route through, and a combination that is documented as meaningless
 * should not be representable.
 */
const floating = computed(() => props.floatMenu && props.placement !== "right");

// Dynamic max-height so the menu never runs off the bottom of the screen: measure
// the menu's top in the viewport and cap its height to what's left below it. The
// inner .ate-list scrolls; the search box and status stay pinned. Recomputed on
// open and on resize/scroll (scroll uses capture, to catch a scrolling ancestor
// such as the sidebar when this is a flyout).
const menuStyle = ref({});

// Breathing room to the trigger, keep-off distance from the viewport edges, and
// the height below which "below the trigger" counts as not fitting.
const MENU_GAP_PX = 8;
const MENU_MARGIN_PX = 8;
const MENU_MIN_HEIGHT_PX = 140;

function sizeMenu() {
  nextTick(() => {
    // A flyout's SIDE is as viewport-dependent as its height, and this is the
    // function the `resize` and capture-phase `scroll` listeners call - so a
    // menu that is already open follows the window rather than keeping the side
    // it was opened on. Hover cannot stand in for this: a keyboard user never
    // fires one, and that is the case the flip used to depend on entirely.
    measureFlyoutSide();
    const el = menuRef.value;
    if (!el) return;
    if (!floating.value) {
      const top = el.getBoundingClientRect().top;
      const avail = window.innerHeight - top - 12;
      menuStyle.value = {
        maxHeight: `${Math.max(MENU_MIN_HEIGHT_PX, Math.round(avail))}px`,
      };
      return;
    }
    // Floating: the menu no longer moves with its parent, so POSITION is
    // recomputed here too, not just height. This runs on open and on the
    // resize / capture-phase scroll listeners openMenu() already registers;
    // capture phase is what catches the overlay sidebar scrolling, since the
    // scrolling ancestor is not the window.
    const anchor = rootRef.value?.getBoundingClientRect();
    if (!anchor) return;
    const width = el.getBoundingClientRect().width || 220;
    const left = Math.max(
      MENU_MARGIN_PX,
      Math.min(anchor.left, window.innerWidth - width - MENU_MARGIN_PX),
    );
    const below =
      window.innerHeight - anchor.bottom - MENU_GAP_PX - MENU_MARGIN_PX;
    const above = anchor.top - MENU_GAP_PX - MENU_MARGIN_PX;
    // Flip up for a trigger low in the sidebar, but only when that is actually
    // roomier: near the bottom of a short viewport both are cramped and the
    // menu should stay put and scroll internally.
    const flipUp = below < MENU_MIN_HEIGHT_PX && above > below;
    const maxHeight = Math.max(
      MENU_MIN_HEIGHT_PX,
      Math.round(flipUp ? above : below),
    );
    // Anchoring the flipped menu by its BOTTOM avoids needing its height,
    // which is not known until after max-height has been applied.
    menuStyle.value = flipUp
      ? {
          left: `${Math.round(left)}px`,
          top: "auto",
          bottom: `${Math.round(window.innerHeight - anchor.top + MENU_GAP_PX)}px`,
          maxHeight: `${maxHeight}px`,
        }
      : {
          left: `${Math.round(left)}px`,
          top: `${Math.round(anchor.bottom + MENU_GAP_PX)}px`,
          bottom: "auto",
          maxHeight: `${maxHeight}px`,
        };
  });
}

// --- URL helpers ---
const baseUrl = computed(() =>
  props.backendUrl ? String(props.backendUrl).replace(/\/$/, "") : "",
);

// The resource modules take the backend base as an option; an empty string
// means "address the API relatively", which is what a missing prop implies.
const apiOpts = computed(() => ({ baseUrl: baseUrl.value || "" }));

// --- Normalised subject IDs ---
const normalisedPictureIds = computed(() =>
  (Array.isArray(props.subjectIds) ? props.subjectIds : [])
    .map((id) => String(id))
    .filter(Boolean),
);

const normalisedIdsKey = computed(() => normalisedPictureIds.value.join("|"));

// --- Items, read straight through the shared list store ---
const items = computed(() => {
  if (isSet.value) {
    return entityLists.pictureSets
      .filter((s) => !s?.reference_character)
      .map((s) => ({
        id: s.id,
        key: String(s.id),
        name: s.name,
        count: s.picture_count ?? null,
      }));
  }
  if (isProject.value) {
    return entityLists.projects
      .map((row) => ({
        id: Number(row?.id),
        name: String(row?.name || "").trim() || `Project ${row?.id}`,
      }))
      .filter((row) => Number.isFinite(row.id) && row.id > 0)
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((p) => ({
        id: p.id,
        key: `project-${p.id}`,
        name: p.name,
        count: null,
      }));
  }
  return [...entityLists.characters]
    .sort((a, b) => String(a?.name || "").localeCompare(String(b?.name || "")))
    .map((c) => ({ id: c.id, key: String(c.id), name: c.name, count: null }));
});

// --- Filtered items list ---
const filteredItems = computed(() => {
  // Face mode leads with the clear-assignment row, the single-select
  // counterpart of unchecking a box.
  const base = isFace.value
    ? [{ id: null, key: "face-unassigned", name: "Unassigned" }, ...items.value]
    : items.value;
  const needle = searchQuery.value.trim().toLowerCase();
  if (!needle) return base;
  return base.filter((item) =>
    String(item.name || "")
      .toLowerCase()
      .includes(needle),
  );
});

// --- Item state helpers ---
// A set frozen by the lock cannot gain or lose members.
function isItemLocked(item) {
  return isSet.value && !!props.lockedSetIds?.has?.(item.id);
}

function isItemDisabled(item) {
  if (props.readonly) return true;
  // Face mode is scoped by a face, not by a picture selection.
  if (isFace.value) return !props.faceId;
  if (!normalisedPictureIds.value.length) return true;
  if (isItemLocked(item)) return true;
  if (isCharacter.value) return false;
  // A set/project toggle is a diff against current membership, so it stays
  // inert for the moment between the list rendering and the membership landing.
  return !membershipLoaded.value;
}

// Face mode: exactly one row is selected, so the glyph is a radio, and it stays
// on-dark-surface in BOTH states. The shape carries selected/unselected, which
// is what lets the olive be spent on the create row alone.
function isFaceItemSelected(item) {
  if (!isFace.value) return false;
  const current =
    props.assignedCharacterId != null ? String(props.assignedCharacterId) : "";
  const value = item?.id != null ? String(item.id) : "";
  return current === value;
}

function getItemGlyph(item) {
  if (isFace.value) {
    return isFaceItemSelected(item)
      ? "mdi-radiobox-marked"
      : "mdi-radiobox-blank";
  }
  const state = getItemState(item);
  if (state === "checked") return "mdi-checkbox-marked";
  if (state === "partial") return "mdi-minus-box-outline";
  return "mdi-checkbox-blank-outline";
}

// The option's ARIA state. `aria-selected` is what a listbox option is expected
// to expose; `aria-checked` (and "mixed" least of all) is not reliably announced
// on one, so partial membership is carried as text in the row instead. "false"
// for a partial row is not a lie: clicking it adds the rest of the selection,
// exactly like an unchecked row, because only "checked" removes.
function getAriaSelected(item) {
  if (isFace.value) return isFaceItemSelected(item) ? "true" : "false";
  return getItemState(item) === "checked" ? "true" : "false";
}

function getItemState(item) {
  const ids = normalisedPictureIds.value;
  if (!ids.length) return "unchecked";
  const members = effectiveMembers.value?.[item.key];
  if (!members || members.size === 0) return "unchecked";

  // The face narrowing is a PICTURE rule - a picture with no face cannot be a
  // character member - so it must not survive into host-driven mode. Left in,
  // it would filter the shelf's adapter ids against a set that never contains
  // them, and every row would read `unchecked` however many were attached.
  const relevantIds =
    isCharacter.value && !props.membership
      ? ids.filter((id) => picturesWithFaces.value.has(String(id)))
      : ids;
  if (!relevantIds.length) return "unchecked";

  const matched = relevantIds.filter((id) => members.has(String(id))).length;
  if (matched === 0) return "unchecked";
  if (matched === relevantIds.length) return "checked";
  return "partial";
}

function isLastUsedItem(item) {
  return Boolean(
    isSet.value && lastUsedItem.value && item?.id === lastUsedItem.value.id,
  );
}

// --- Create-person affordance (character type only, opt-in) ---
// Rendered only when the host declares it handles the "create" event via
// `allowCreate`. Disabled under the same conditions as sibling rows:
// read-only sessions and an empty picture selection. Creation logic lives in
// the host; this component only announces the intent, carrying the typed
// query.
const canCreate = computed(() => listsPeople.value && props.allowCreate);

const trimmedQuery = computed(() => searchQuery.value.trim());

const createDisabled = computed(() => {
  if (props.readonly) return true;
  // Each people mode is gated by whatever it acts on.
  return isFace.value ? !props.faceId : !normalisedPictureIds.value.length;
});

const showEmptyCreateRow = computed(
  () =>
    canCreate.value &&
    !isLoading.value &&
    filteredItems.value.length === 0 &&
    trimmedQuery.value.length > 0,
);

function requestCreate() {
  if (!canCreate.value || createDisabled.value) return;
  const query = trimmedQuery.value;
  // Capture the query BEFORE closeMenu(), which clears the search box.
  closeMenu();
  emit("create", query);
}

// Enter in the search box activates the empty-state create row when the query
// matches nothing; with matches on screen it stays inert (clicking a row is
// the assignment gesture, and Enter must not create a duplicate person).
function onSearchEnter() {
  if (!showEmptyCreateRow.value) return;
  requestCreate();
}

// --- Menu open/close ---
function toggleMenu() {
  if (props.disabled) return;
  if (props.placement === "right") {
    // For flyout placement, click/keyboard can open (or close a click-opened menu).
    // Hover-opened menus are not toggled by click to avoid accidental dismissal.
    if (menuOpen.value && flyoutClickedOpen.value) {
      flyoutClickedOpen.value = false;
      closeMenu();
    } else if (!menuOpen.value) {
      flyoutClickedOpen.value = true;
      openMenu();
      // Deliberate activation (click, Enter, Space) puts the caret in the search
      // box, same as the default placement. Hover-open goes through
      // onFlyoutMouseenter instead and never moves focus.
      nextTick(() => searchInputRef.value?.focus());
      document.addEventListener("pointerdown", handleOutsideClick, true);
    }
    return;
  }
  menuOpen.value = !menuOpen.value;
  if (menuOpen.value) {
    openMenu();
  } else {
    closeMenu();
  }
}

/**
 * Which side a flyout opens on, measured off the trigger.
 *
 * 185px is the flyout's fixed width (`.ate--flyout .ate-menu`), and 8px is the
 * same keep-off distance `sizeMenu` uses. It used to live in
 * `onFlyoutMouseenter` alone, so a flyout opened from the KEYBOARD near the
 * right edge kept the last measurement - `false` on the first open - and
 * painted off-screen with nothing to clamp it.
 *
 * Three callers, and each covers a case the others cannot: `openMenu`
 * synchronously, so the first paint is already on the correct side; `sizeMenu`,
 * which is what the `resize` / `scroll` listeners call, so an open menu follows
 * the window; and `onFlyoutMouseenter`, which is the only one that sees a hover
 * of an already-open menu.
 */
function measureFlyoutSide() {
  if (props.placement !== "right") return;
  const rect = rootRef.value?.getBoundingClientRect();
  if (!rect) return;
  flyoutFlipped.value = rect.right + 185 > window.innerWidth - 8;
}

function openMenu() {
  menuOpen.value = true;
  measureFlyoutSide();
  // Stale-while-revalidate, both halves fired without awaiting: the list is
  // already on screen from the store's cache, and revalidating on open is the
  // only invalidation a share/scoped session gets (the ws stream is owner-only).
  entityLists.refresh(entityKind.value, { baseUrl: baseUrl.value });
  fetchMembers();
  sizeMenu();
  window.addEventListener("resize", sizeMenu);
  window.addEventListener("scroll", sizeMenu, true);
  if (props.placement !== "right") {
    nextTick(() => searchInputRef.value?.focus());
    document.addEventListener("pointerdown", handleOutsideClick, true);
  }
}

function closeMenu() {
  // Whoever was driving the menu from the keyboard is about to lose their focus
  // holder, so hand it back to the trigger instead of dropping it on <body>.
  // Guarded on focus actually being inside: a hover-out or a click elsewhere
  // must not yank focus away from wherever the user just went.
  const returnFocus = menuRef.value?.contains(document.activeElement) ?? false;
  menuOpen.value = false;
  searchQuery.value = "";
  window.removeEventListener("resize", sizeMenu);
  window.removeEventListener("scroll", sizeMenu, true);
  if (flyoutClickedOpen.value) {
    document.removeEventListener("pointerdown", handleOutsideClick, true);
    flyoutClickedOpen.value = false;
  }
  if (props.placement !== "right") {
    document.removeEventListener("pointerdown", handleOutsideClick, true);
  }
  if (returnFocus) focusTrigger();
}

// --- Keyboard ownership ---
// Navigation lives here rather than in each host's roving-focus query: with
// `floatMenu` the menu is teleported to <body>, so a host keydown listener never
// sees these keys at all. Order is [search box, ...enabled rows]; no wrapping, so
// ArrowUp off the first row lands back in the search box.
function navigableItems() {
  const menu = menuRef.value;
  if (!menu) return [];
  // Options only. The create rows share `.ate-item` but sit outside the listbox
  // on purpose, and they are actions rather than choices, so they keep their
  // normal Tab order instead of joining this roving focus.
  const rows = Array.from(
    menu.querySelectorAll('[role="option"]:not([disabled])'),
  );
  const input = searchInputRef.value;
  return input ? [input, ...rows] : rows;
}

function onMenuKeydown(event) {
  if (!menuOpen.value) return;
  const key = event.key;
  const inSearch = event.target === searchInputRef.value;

  if (key === "Escape") {
    // The first Escape dismisses this list only. Hosts that also watch Escape
    // (the grid context menu) exempt events originating inside `.ate-menu`, so
    // the menu behind us survives and takes the second press.
    event.preventDefault();
    event.stopPropagation();
    closeMenu();
    return;
  }

  // "Back" in the flyout idiom. Inside the search box only at caret start, so
  // it stays a text-editing key while there is text to move through.
  if (key === "ArrowLeft" && props.placement === "right") {
    if (
      inSearch &&
      !(event.target.selectionStart === 0 && event.target.selectionEnd === 0)
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    closeMenu();
    return;
  }

  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(key)) return;
  // Home/End keep their text-editing meaning while the caret is in the search box.
  if (inSearch && (key === "Home" || key === "End")) return;
  const items = navigableItems();
  if (!items.length) return;
  event.preventDefault();
  event.stopPropagation();
  const rows = items.filter((el) => el !== searchInputRef.value);
  const current = items.indexOf(document.activeElement);
  let target;
  if (key === "Home") target = rows[0];
  else if (key === "End") target = rows[rows.length - 1];
  else if (key === "ArrowDown")
    target = items[Math.min(current + 1, items.length - 1)];
  else target = items[Math.max(current - 1, 0)];
  target?.focus();
}

function onFlyoutMouseenter() {
  if (props.placement !== "right" || props.disabled) return;
  // Re-measured on a hover of an ALREADY-open menu too, which is the one case
  // `openMenu` cannot cover: the window may have been resized under it.
  measureFlyoutSide();
  if (menuOpen.value) return;
  openMenu();
}

function onFlyoutMouseleave() {
  if (props.placement !== "right") return;
  if (flyoutClickedOpen.value) return; // user clicked to open - keep it open
  closeMenu();
}

function handleOutsideClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) return;
  if (rootRef.value?.contains(target)) return;
  if (menuRef.value?.contains(target)) return;
  closeMenu();
}

// --- Membership (a live per-selection read) ---
// Membership is NOT cached: it answers "is *this* selection in each entity",
// which changes with every click. It is fetched alongside the list rather than
// before it, so the flyout paints its rows immediately and the checkmarks fill
// in a moment later. Every response is stamped with the selection it was asked
// for and dropped if the selection has moved on, so one selection's membership
// can never bleed into the next.
async function fetchMembers() {
  const ids = normalisedPictureIds.value;
  const requestKey = normalisedIdsKey.value;
  // Host-driven: the membership arrived as a prop, so there is nothing to read
  // and the picture readers would not understand these ids anyway.
  if (props.membership) {
    membershipLoaded.value = true;
    return;
  }
  if (!props.backendUrl || !ids.length) {
    membersById.value = {};
    picturesWithFaces.value = new Set();
    membershipLoaded.value = false;
    return;
  }
  try {
    const next = isSet.value
      ? await readSetMembers(ids)
      : isProject.value
        ? await readProjectMembers(ids)
        : await readCharacterMembers(ids);
    if (requestKey !== normalisedIdsKey.value) return; // selection moved on
    // Nothing above this guard may write component state: the readers are pure
    // reads that hand everything back, so a superseded response is discarded
    // whole rather than leaking one of its halves.
    membersById.value = next.members;
    if (next.withFaces) picturesWithFaces.value = next.withFaces;
    membershipLoaded.value = true;
  } catch (e) {
    if (requestKey !== normalisedIdsKey.value) return;
    console.warn(
      `[AddToEntityControl] failed to read ${props.type} membership for ${ids.length} picture(s):`,
      e,
    );
    membersById.value = {};
    picturesWithFaces.value = new Set();
    membershipLoaded.value = false;
  }
}

async function readSetMembers(ids) {
  const data =
    (await getPictureSetMembership(ids, {
      ...apiOpts.value,
      includeDeleted: props.includeDeletedMembers ?? false,
    })) ?? {};
  const members = {};
  Object.entries(data).forEach(([setId, memberIds]) => {
    members[String(setId)] = new Set(
      (Array.isArray(memberIds) ? memberIds : []).map(String),
    );
  });
  return { members, withFaces: null };
}

async function readProjectMembers(ids) {
  const data = (await getProjectMembership(ids, apiOpts.value)) ?? {};
  const assignments = data.project_assignments ?? {};
  const unassignedIds = data.unassigned_picture_ids ?? [];
  const members = { unassigned: new Set(unassignedIds.map(String)) };
  Object.entries(assignments).forEach(([projectId, picIds]) => {
    members[`project-${projectId}`] = new Set((picIds ?? []).map(String));
  });
  return { members, withFaces: null };
}

async function readCharacterMembers(ids) {
  const data = (await getCharacterMembership(ids, apiOpts.value)) ?? {};
  const members = {};
  Object.entries(data.character_assignments ?? {}).forEach(
    ([charId, picIds]) => {
      members[String(charId)] = new Set(picIds.map(String));
    },
  );
  // Handed back rather than assigned here: this runs before fetchMembers'
  // selection guard, so writing it in place would leak a superseded
  // selection's face ids past the discard.
  return {
    members,
    withFaces: new Set((data.pictures_with_faces ?? []).map(String)),
  };
}

/**
 * Handle a failed assignment.
 *
 * A 404 means the cached list named an entity the server no longer has, so the
 * cache is refetched rather than left to offer the same dead row again.
 */
function reportToggleFailure(e, fallback) {
  const status = e?.response?.status;
  const detail = errorDetail(e) || e?.message || String(e);
  console.warn(
    `[AddToEntityControl] ${props.type} assignment failed (status=${status ?? "n/a"}):`,
    e,
  );
  if (status === 404) {
    entityLists.invalidate([entityKind.value], { baseUrl: baseUrl.value });
    return `${effectiveLabel.value} no longer exists`;
  }
  return detail ? String(detail) : fallback;
}

// --- Toggle dispatch ---
async function toggleItem(item) {
  if (isItemDisabled(item)) return;
  if (isFace.value) {
    selectFacePerson(item);
    return;
  }
  if (isProject.value) {
    toggleProject(item);
    return;
  }
  // Announce-only: the host holds the API. The payload carries the resolved
  // intent rather than the raw click, because only this component knows whether
  // a partially-applied entity resolves up (it does) and therefore which
  // subjects still need writing.
  if (writesAreHosted.value) {
    announceToggle(item);
    return;
  }
  if (isSet.value) await toggleSet(item);
  else await toggleCharacter(item);
}

/**
 * Emit what a click means, for a host that owns the writes.
 *
 * `attach` / `detach` rather than `added` / `removed`: those two are already
 * spoken for by the picture modes, carry `pictureIds`, and are forwarded
 * verbatim into grid stores. A new name keeps this addition from reaching any
 * existing listener.
 */
function announceToggle(item) {
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const members = effectiveMembers.value?.[item.key];
  const state = getItemState(item);
  if (state === "checked") {
    emit("detach", {
      entityType: props.type,
      entityId: item.id,
      subjectIds: ids,
    });
  } else {
    // Partial resolves UP, the same rule the writing paths apply: only the
    // subjects that are not already in get written, and nothing is detached.
    const missing = members
      ? ids.filter((id) => !members.has(String(id)))
      : ids;
    if (!missing.length) return;
    emit("attach", {
      entityType: props.type,
      entityId: item.id,
      entityName: item.name,
      subjectIds: missing,
    });
  }
  closeMenu();
}

// Face mode performs no writes: the host owns the face-level API calls and its
// optimistic bookkeeping, so this only announces the choice. Re-picking the
// current person is a no-op rather than a redundant request.
function selectFacePerson(item) {
  if (isFaceItemSelected(item)) {
    closeMenu();
    return;
  }
  if (item?.id == null) {
    emit("unassign", { faceId: props.faceId });
  } else {
    emit("assign", {
      faceId: props.faceId,
      characterId: item.id,
      characterName: item.name,
    });
  }
  closeMenu();
}

async function toggleSet(item) {
  if (!item?.id) return;
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const members = membersById.value?.[item.key];
  const state = getItemState(item);
  const shouldRemove = state === "checked";
  const idsToAdd = members ? ids.filter((id) => !members.has(String(id))) : ids;
  const idsToRemove = members
    ? ids.filter((id) => members.has(String(id)))
    : [];
  if (!shouldRemove && !idsToAdd.length) {
    statusMessage.value = "Already in set";
    return;
  }
  if (shouldRemove && !idsToRemove.length) {
    statusMessage.value = "Not in set";
    return;
  }
  statusMessage.value = shouldRemove ? "Removing..." : "Adding...";
  try {
    if (shouldRemove) {
      await Promise.all(
        idsToRemove.map((id) =>
          removePictureFromSet(item.id, id, apiOpts.value),
        ),
      );
      statusMessage.value = `Removed from ${item.name}`;
      emit("added", {
        setId: item.id,
        pictureIds: idsToRemove,
        action: "removed",
      });
      if (members) idsToRemove.forEach((id) => members.delete(String(id)));
    } else {
      await Promise.all(
        idsToAdd.map((id) => addPictureToSet(item.id, id, apiOpts.value)),
      );
      statusMessage.value = `Added to ${item.name}`;
      emit("added", { setId: item.id, pictureIds: idsToAdd, action: "added" });
      lastUsedItem.value = { id: item.id, name: item.name };
      if (members) idsToAdd.forEach((id) => members.add(String(id)));
    }
    // The membership just moved, so the list's picture counts did too: ask the
    // server again rather than patching the shared cache from here.
    entityLists.invalidate(["sets"], { baseUrl: baseUrl.value });
  } catch (e) {
    const detail = errorDetail(e) || e?.message || String(e);
    statusMessage.value = String(detail).includes("already in set")
      ? "Already in set"
      : reportToggleFailure(
          e,
          shouldRemove ? "Failed to remove" : "Failed to add",
        );
  }
  scheduleStatusClear();
}

function toggleProject(item) {
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const state = getItemState(item);
  if (item.id == null && state === "checked") {
    statusMessage.value = "Already unassigned";
    return;
  }
  const shouldRemove = item.id != null && state === "checked";
  const action = shouldRemove ? "removed" : "added";
  statusMessage.value = shouldRemove ? "Removing..." : "Adding...";
  emit("selected", {
    projectId: item.id ?? null,
    projectName: item.name,
    action,
    pictureIds: ids,
    expandStacks: props.expandStacks,
  });
  applyOptimisticProjectUpdate(item, action, ids);
  statusMessage.value =
    action === "removed"
      ? `Removed from ${item.name}`
      : item.id == null
        ? "Set to unassigned"
        : `Added to ${item.name}`;
  scheduleStatusClear(1600);
}

function applyOptimisticProjectUpdate(item, action, ids) {
  const next = { ...membersById.value };
  const unassignedKey = "unassigned";
  if (!(next[unassignedKey] instanceof Set)) next[unassignedKey] = new Set();
  for (const proj of items.value) {
    if (!(next[proj.key] instanceof Set)) next[proj.key] = new Set();
  }

  if (item.id == null && action === "added") {
    for (const proj of items.value) {
      ids.forEach((id) => next[proj.key].delete(String(id)));
    }
    ids.forEach((id) => next[unassignedKey].add(String(id)));
    membersById.value = next;
    return;
  }

  if (!(next[item.key] instanceof Set)) next[item.key] = new Set();

  if (action === "removed") {
    ids.forEach((id) => next[item.key].delete(String(id)));
    for (const id of ids) {
      const idStr = String(id);
      const stillAssigned = items.value.some(
        (proj) => next[proj.key] instanceof Set && next[proj.key].has(idStr),
      );
      if (!stillAssigned) next[unassignedKey].add(idStr);
    }
  } else {
    ids.forEach((id) => {
      next[item.key].add(String(id));
      next[unassignedKey].delete(String(id));
    });
  }
  membersById.value = next;
}

async function toggleCharacter(item) {
  if (!item?.id) return;
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const state = getItemState(item);

  if (state === "checked") {
    statusMessage.value = "Removing...";
    try {
      await removeCharacterFaces(item.id, ids, apiOpts.value);
      statusMessage.value = `Removed from ${item.name}`;
      emit("removed", { characterId: item.id, pictureIds: ids });
      const members = membersById.value?.[item.key];
      if (members) ids.forEach((id) => members.delete(String(id)));
      closeMenu();
    } catch (e) {
      statusMessage.value = reportToggleFailure(e, "Failed to remove");
    }
  } else {
    const members = membersById.value?.[item.key];
    const idsToAdd = members
      ? ids.filter((id) => !members.has(String(id)))
      : ids;
    if (!idsToAdd.length) return;
    statusMessage.value = "Assigning...";
    try {
      await addCharacterFaces(item.id, idsToAdd, apiOpts.value);
      statusMessage.value = `Assigned to ${item.name}`;
      emit("added", { characterId: item.id, pictureIds: ids });
      // Only update the optimistic member cache for pictures that actually have
      // faces - faceless pictures can't be reflected in the membership state.
      if (members) {
        idsToAdd
          .filter((id) => picturesWithFaces.value.has(String(id)))
          .forEach((id) => members.add(String(id)));
      }
      closeMenu();
    } catch (e) {
      statusMessage.value = reportToggleFailure(e, "Failed to assign");
    }
  }
  scheduleStatusClear();
}

function scheduleStatusClear(delay = 2000) {
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => {
    statusMessage.value = "";
  }, delay);
}

// Set-only: add to last used set without opening menu
async function addToLastSet() {
  if (!lastUsedItem.value) return { error: "no-last-set" };
  const ids = normalisedPictureIds.value;
  if (!ids.length) return { error: "no-pictures" };
  const item = lastUsedItem.value;
  statusMessage.value = `Adding to ${item.name}...`;
  try {
    await Promise.all(
      ids.map((id) => addPictureToSet(item.id, id, apiOpts.value)),
    );
    statusMessage.value = `Added to ${item.name}`;
    const members = membersById.value?.[String(item.id)];
    if (members) ids.forEach((id) => members.add(String(id)));
    emit("added", { setId: item.id, pictureIds: ids, action: "added" });
    scheduleStatusClear();
    return { success: true, setName: item.name };
  } catch (e) {
    const detail = errorDetail(e) || e?.message || String(e);
    statusMessage.value = String(detail).includes("already in set")
      ? `Already in ${item.name}`
      : reportToggleFailure(e, "Failed to add");
    scheduleStatusClear();
    return { error: detail };
  }
}

onBeforeUnmount(() => {
  if (statusTimer) clearTimeout(statusTimer);
  document.removeEventListener("pointerdown", handleOutsideClick, true);
  window.removeEventListener("resize", sizeMenu);
  window.removeEventListener("scroll", sizeMenu, true);
});

watch(
  () => normalisedIdsKey.value,
  () => {
    // Membership belongs to a selection, so it is dropped the moment the
    // selection changes - never carried over - and re-read only if the menu is
    // actually open. The entity list itself is selection-independent and stays.
    membersById.value = {};
    membershipLoaded.value = false;
    if (isCharacter.value) picturesWithFaces.value = new Set();
    if (menuOpen.value) fetchMembers();
  },
);

// Return the keyboard to this control's trigger, e.g. after a host dialog that
// this menu opened has closed.
function focusTrigger() {
  rootRef.value?.querySelector(".ate-btn")?.focus?.();
}

// Expose for external keyboard shortcut access (set type only) and focus return
defineExpose({
  addToLastSet,
  lastUsedSet: lastUsedItem,
  closeMenu,
  focusTrigger,
});
</script>

<style scoped>
.ate {
  position: relative;
  display: inline-flex;
}

.ate-btn {
  background-color: rgba(var(--v-theme-surface), 0.85);
  color: rgb(var(--v-theme-on-surface));
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  line-height: 1.4;
}

.ate--force-dark .ate-btn {
  background-color: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

.ate-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.ate--readonly .ate-btn {
  opacity: 0.5;
}

.ate-btn:hover {
  filter: brightness(1.75);
}

.ate-label {
  white-space: nowrap;
}

.ate-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  min-width: 220px;
  /* Flex column so the search box and status stay pinned while only the item list
     scrolls. The CSS cap is a fallback; sizeMenu() sets an exact viewport-aware
     max-height inline. overflow:hidden keeps the scroll area within the rounded card. */
  display: flex;
  flex-direction: column;
  max-height: 72vh;
  overflow: hidden;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-2);
  opacity: 0;
  transform: translateY(-6px);
  pointer-events: none;
  transition:
    opacity var(--dur-1) var(--ease-standard),
    transform var(--dur-1) var(--ease-standard);
  /* `--z-dropdown` (300), which is the token's own definition: "menus,
     popovers, tooltips anchored to a control". It was a bare `6`, which is
     BELOW `--z-sticky` (100) - so anywhere this menu opens over a sticky
     header it rendered behind it. Found on the model shelf, where the picker
     sits in the selection bar and the folder group headers are sticky; the bug
     was in this shared component, so every caller with a sticky neighbour had
     it. */
  z-index: var(--z-dropdown);
}

/* Floating mode (opt-in, see the `floatMenu` prop): the node has been teleported
   to <body>, so it is positioned against the viewport by sizeMenu() and needs
   its own stacking level. `--z-overlay` is the same token the grid context menu
   uses, and it clears the lightbox (z-index 1000) and its sidebar (4).
   Declared after `.ate-menu` so it wins at equal specificity. */
.ate-menu--floating {
  position: fixed;
  top: 0;
  left: 0;
  z-index: var(--z-overlay);
}

/* The scrolling region: only the item list scrolls when the menu is height-capped.
   min-height:0 lets it shrink inside the flex column so overflow actually engages. */
.ate-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

/* Opaque, not 0.9: the menu teleports to body and can sit over the lightbox
   photo. At 0.9 over a white image region the panel composites to #3a3c3e and
   the create row falls to 4.01:1. */
.ate-menu.force-dark {
  background-color: rgb(var(--v-theme-dark-surface));
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

.ate-menu.force-dark .ate-search {
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
}

.ate-menu.force-dark .ate-search input {
  color: rgb(var(--v-theme-on-dark-surface));
}

.ate-menu.force-dark .ate-item {
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

.ate-menu.force-dark .ate-item:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}

.ate-menu.force-dark .ate-item-count {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.ate-menu.force-dark .ate-item-shortcut {
  border-color: rgba(var(--v-theme-on-dark-surface), 0.32);
  color: rgba(var(--v-theme-on-dark-surface), 0.9);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}

.ate-menu.force-dark .ate-empty {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.ate-menu.force-dark .ate-status {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}

.ate.open .ate-menu,
.ate-menu.open {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.ate-search {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.06);
  margin-bottom: var(--space-3);
}

.ate-search input {
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-surface));
  width: 100%;
  font-size: var(--text-xs);
  outline: none;
}

.ate-item {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
  text-align: left;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  transition:
    background var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard);
}

.ate-item-check {
  color: rgba(var(--v-theme-on-surface), 0.7);
  flex-shrink: 0;
}

.ate-item--checked .ate-item-check {
  color: rgb(var(--v-theme-primary));
}

.ate-item-name {
  flex: 1;
  min-width: 0;
}

.ate-item-meta {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.ate-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.ate-item--disabled {
  opacity: 0.5;
  cursor: default;
  pointer-events: none;
}

.ate-item-count {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.ate-item-lock {
  color: rgba(var(--v-theme-on-surface), 0.5);
}

/* The create row. Colour is one of THREE redundant channels (WCAG 1.4.1), the
   others being the mdi-account-plus icon and the pinned position below the
   hairline; the trailing ellipsis stays for the same reason.

   Weight 500 is load-bearing, not decoration: any colour highlight is darker
   than its near-white neighbours in greyscale, and without the extra weight the
   row reads as disabled. Not 600.

   On the dark panel plain `primary` (#567309) is only 2.79:1 and unusable at
   this text size, so the force-dark skin switches to `dark-surface-primary`
   (#8EA604): 5.50:1 over the light theme's #242628 and 6.25:1 over the dark
   theme's #181b20. The 0.12 hover alpha is a ceiling, not taste: the family's
   neutral 0.08 hover drops the olive to 4.45:1, and 0.14 of the tint drops it
   to 4.40:1. */
.ate-item--create {
  color: rgb(var(--v-theme-primary));
  font-weight: var(--weight-medium);
}

.ate-item--create .ate-item-check {
  color: currentColor;
}

.ate-item--create:hover:not(.ate-item--disabled) {
  background: rgba(var(--v-theme-primary), 0.1);
}

.ate-menu.force-dark .ate-item--create {
  color: rgb(var(--v-theme-dark-surface-primary));
}

.ate-menu.force-dark .ate-item--create:hover:not(.ate-item--disabled) {
  background: rgba(var(--v-theme-dark-surface-primary), 0.12);
}

/* `on-surface` flips with the theme but `dark-surface` does not, so without
   these overrides the glyphs render warm near-black on the dark panel in the
   light theme (about 1.05:1, effectively invisible). */
.ate-menu.force-dark .ate-item-check,
.ate-menu.force-dark .ate-item-lock {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}

/* `primary` is 2.79:1 on the dark panel, under even the 3:1 UI floor for an
   icon, so the checked glyph takes the lighter olive there. */
.ate-menu.force-dark .ate-item--checked .ate-item-check {
  color: rgb(var(--v-theme-dark-surface-primary));
}

.ate-item-shortcut {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.32);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-2xs);
  line-height: 1;
  color: rgba(var(--v-theme-on-surface), 0.9);
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.ate-empty {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* Pinned create row: separated from the scrolling list by a hairline so it
   reads as an action, not another list entry. */
.ate-create-pinned {
  flex: 0 0 auto;
  margin-top: var(--space-1);
  padding-top: var(--space-1);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.14);
}

.ate-menu.force-dark .ate-create-pinned {
  border-top-color: rgba(var(--v-theme-on-dark-surface), 0.14);
}

.ate-status {
  flex: 0 0 auto;
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.ate-shortcut-status {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  max-width: 220px;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-3);
  font-size: var(--text-xs);
  line-height: 1.2;
  /* Above the menu it belongs to, which just moved to `--z-dropdown`. Kept as
     a relative expression rather than a second bare number so the two cannot
     drift apart again. */
  z-index: calc(var(--z-dropdown) + 1);
  pointer-events: none;
}

.ate--force-dark .ate-shortcut-status {
  background: rgba(var(--v-theme-dark-surface), 0.92);
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

/* ── Flyout (right-placement) mode ──────────────────────────── */
.ate--flyout {
  width: 100%;
  display: flex;
}

.ate--flyout .ate-btn {
  width: 100%;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  padding: var(--space-2) var(--space-5);
  border-radius: 0;
  font-size: var(--text-sm);
  gap: var(--space-3);
}

.ate--flyout .ate-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.ate--flyout .ate-chevron {
  margin-left: auto;
  opacity: 0.7;
}

.ate--flyout .ate-menu,
.ate-menu.flyout {
  position: absolute;
  left: 100%;
  top: 0;
  min-width: 185px;
  max-width: 185px;
  padding: var(--space-2) 0;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  box-shadow: var(--elevation-3);
  transform: translateX(-4px);
  z-index: 2500;
}

/* Flip flyout menu leftward when near the right screen edge */
.ate--flip.ate--flyout .ate-menu,
.ate--flip .ate-menu.flyout {
  left: auto;
  right: 100%;
  transform: translateX(4px);
}

.ate--flip.ate--flyout.open .ate-menu,
.ate--flip.ate--flyout .ate-menu.open,
.ate--flip .ate-menu.flyout.open {
  transform: translateX(0);
}

.ate--flyout.open .ate-menu,
.ate--flyout .ate-menu.open,
.ate-menu.flyout.open {
  transform: translateX(0);
}

.ate--flyout .ate-item {
  border-radius: 0;
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
}

.ate--flyout .ate-item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ate--flyout .ate-search {
  margin: var(--space-2) var(--space-2) var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
}

.ate--flyout .ate-empty,
.ate--flyout .ate-status {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
}
</style>
