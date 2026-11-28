/**
 * The Studio's categories (#2819): each kind's words, icon and colour token.
 * Never colour alone: every mark keeps its icon and its word.
 */
export const STUDIO_KINDS = {
  scene: {
    label: "Scene",
    plural: "Scenes",
    all: "All scenes",
    icon: "mdi-movie-open-outline",
    color: null,
  },
  cast: {
    label: "Cast",
    plural: "Cast",
    all: "All people",
    icon: "mdi-account-outline",
    color: "--kind-cast",
  },
  outfit: {
    label: "Outfit",
    plural: "Outfits",
    all: "All outfits",
    icon: "mdi-hanger",
    color: "--kind-cast",
  },
  location: {
    label: "Location",
    plural: "Locations",
    all: "All locations",
    icon: "mdi-map-marker-outline",
    color: "--kind-location",
  },
  prop: {
    label: "Prop",
    plural: "Props",
    all: "All props",
    icon: "mdi-umbrella-outline",
    color: "--kind-prop",
  },
};

/** The sidebar's sections, in order: each opens a grid of its kind. */
export const STUDIO_SECTIONS = ["scenes", "favourites", "cast", "locations", "props"];

/** A section's kind, and back. */
export const SECTION_KIND = {
  scenes: "scene",
  cast: "cast",
  locations: "location",
  props: "prop",
};

/** The overview list a kind's items are in. */
export const KIND_LIST = {
  scene: "scenes",
  cast: "cast",
  outfit: "outfits",
  location: "locations",
  prop: "props",
};

/** The section an item's lightbox returns to on Esc. */
export const KIND_SECTION = {
  scene: "scenes",
  cast: "cast",
  outfit: "cast",
  location: "locations",
  prop: "props",
};

/** A location's views and an outfit's or prop's angles, as the sheets cut them. */
export const LOCATION_VIEWS = ["Wide", "Reverse", "Left", "Right"];
export const ITEM_ANGLES = ["Front", "¾", "Side", "Back"];

/** `{"--kind-tile": ...}`, the colour a kind's icon is drawn in, or nothing. */
export function kindStyle(kind) {
  const token = STUDIO_KINDS[kind]?.color;
  return token ? { "--kind-tile": `rgb(var(${token}))` } : {};
}

/**
 * The Studio's query with *patch* applied: a null or undefined value drops
 * that key. The query is the Studio's place (router/index.js).
 */
export function studioQuery(query, patch) {
  const next = { ...query, ...patch };
  for (const key of Object.keys(next)) {
    if (next[key] === null || next[key] === undefined || next[key] === "") {
      delete next[key];
    }
  }
  return next;
}

/**
 * An id or null as AppSelect's native `<select>` holds it: a string, ""
 * for none. It hands back what it holds as a string, so `fromOption` reads
 * it back as a number or null.
 */
export const optionValue = (v) => (v === null || v === undefined ? "" : String(v));
export const fromOption = (s) => (s === "" || s === null || s === undefined ? null : Number(s));
