/**
 * What the sheet dialog draws besides a character: an outfit, a location or a
 * prop, each four angles that agree, drawn together on one 5:3 sheet and cut
 * (`POST /character-sheets/prompt` with `kind`, `POST /item-sheets/rank` and
 * `/use`). The step (CharacterSheetStep) is the same for every kind; this is
 * what differs: the words, the form's fields and the panels' names.
 *
 * A field is `{ key, label, placeholder, recent? }`: `key` is the prompt
 * request's field, `recent` that the field offers what earlier sheets were
 * drawn with (the character sheet's own list, shared).
 */

export const ITEM_KINDS = {
  outfit: {
    noun: "outfit",
    title: "Outfit sheets",
    newName: "New outfit",
    intro: (who) =>
      `Each sheet is the outfit drawn four times, worn${who ? ` the way ${who} would wear it` : ""} and cropped at the chin: front, three-quarter, side and back. Panels within one sheet agree, so you keep the sheet that looks right.`,
    describe: (who) => `Describe ${who ? `${who}'s` : "the"} outfit and draw sheets`,
    descriptionPlaceholder:
      "a mustard-yellow hooded raincoat, knee length, open over a black roll-neck",
    descriptionHint:
      "The whole outfit in a sentence. Every panel's caption carries it, so every panel draws the same clothes.",
    fields: [
      { key: "top", label: "Top", placeholder: "a black roll-neck", recent: true },
      { key: "trousers", label: "Bottoms", placeholder: "dark slim jeans", recent: true },
      { key: "shoes", label: "Shoes", placeholder: "black rubber boots", recent: true },
      {
        key: "background",
        label: "Background",
        placeholder: "a plain light-grey studio",
        recent: true,
      },
    ],
    angles: ["Front", "¾", "Side", "Back"],
    views: "angles",
  },
  location: {
    noun: "location",
    title: "Location sheets",
    newName: "New location",
    intro: () =>
      "Each sheet is one place drawn four ways: wide, the reverse view, and looking left and right. Always empty. Views within one sheet agree, so you keep the sheet that looks right.",
    describe: () => "Describe the location and draw sheets",
    descriptionPlaceholder:
      "a narrow trail through a snowy pine forest, a fallen log across the path",
    descriptionHint:
      "The place in a sentence. Every view's caption carries it, so every view draws the same place.",
    fields: [
      { key: "contents", label: "In it", placeholder: "a fallen log, a wooden trail sign" },
      { key: "ground", label: "Ground", placeholder: "packed snow, a few footprints" },
      {
        key: "surroundings",
        label: "Around it",
        placeholder: "tall pines on both sides, a hill behind",
      },
    ],
    angles: ["Wide", "Reverse", "Left", "Right"],
    views: "views",
  },
  prop: {
    noun: "prop",
    title: "Prop sheets",
    newName: "New prop",
    intro: () =>
      "Each sheet is one prop drawn four times, on its own against a plain background: front, three-quarter, side and back. Angles within one sheet agree, so you keep the sheet that looks right.",
    describe: () => "Describe the prop and draw sheets",
    descriptionPlaceholder: "a red stick umbrella with a curved wooden handle, open",
    descriptionHint:
      "The prop in a sentence. Every panel's caption carries it, so every panel draws the same thing.",
    fields: [
      { key: "material", label: "Material", placeholder: "red nylon, a curved wooden handle" },
      { key: "scale", label: "Size", placeholder: "about 1 m across, open" },
      {
        key: "background",
        label: "Background",
        placeholder: "a plain light-grey studio",
        recent: true,
      },
    ],
    angles: ["Front", "¾", "Side", "Back"],
    views: "angles",
  },
};

/** Location sheets: drawn in plain daylight, or as the description says. */
export const DRAWN_AS = [
  { id: true, label: "Plain daylight" },
  { id: false, label: "As described" },
];

/** The width and height of one panel of *kind* on a `width`×`height` sheet:
 * an outfit's four side by side, the others two by two. */
export function panelSize(kind, width, height) {
  return kind === "outfit"
    ? [Math.round(width / 4), height]
    : [Math.round(width / 2), Math.round(height / 2)];
}
