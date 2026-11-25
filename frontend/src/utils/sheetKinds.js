/**
 * What the sheet dialog draws besides a character: an outfit, a location or a
 * prop, each four angles that agree, drawn together on one 5:3 sheet and cut
 * (`POST /character-sheets/prompt` with `kind`, `POST /item-sheets/rank` and
 * `/use`). The step (CharacterSheetStep) is the same for every kind; this is
 * what differs: the words and the panels' names. An item's form is its
 * description and its name alone.
 */

export const ITEM_KINDS = {
  outfit: {
    noun: "outfit",
    title: "Outfit sheets",
    newName: "New outfit",
    intro: () =>
      "Each sheet is the outfit drawn four times on an invisible mannequin, from the collar to the shoes: front, three-quarter, side and back. Panels within one sheet agree, so you keep the sheet that looks right.",
    describe: (who) => `Describe ${who ? `${who}'s` : "the"} outfit and draw sheets`,
    descriptionPlaceholder:
      "a mustard-yellow hooded raincoat, knee length, open over a black roll-neck, dark slim jeans, black rubber boots",
    descriptionHint:
      "The whole outfit, top to shoes. Every panel's caption carries it, so every panel draws the same clothes.",
    angles: ["Front", "¾", "Side", "Back"],
    views: "angles",
  },
  location: {
    noun: "location",
    title: "Location sheets",
    newName: "New location",
    intro: () =>
      "Each sheet is one place drawn four ways: wide, the reverse view, and looking left and right. Always empty, in plain daylight, since each scene sets its own time and weather. Views within one sheet agree, so you keep the sheet that looks right.",
    describe: () => "Describe the location and draw sheets",
    descriptionPlaceholder:
      "a narrow trail through a snowy pine forest, a fallen log across the path",
    descriptionHint:
      "The place, what is in it and around it. Every view's caption carries it, so every view draws the same place.",
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
    descriptionPlaceholder:
      "a red nylon stick umbrella, about 1 m across, open, with a curved wooden handle",
    descriptionHint:
      "The prop, its material and size. Every panel's caption carries it, so every panel draws the same thing.",
    angles: ["Front", "¾", "Side", "Back"],
    views: "angles",
  },
};

/** The width and height of one panel of *kind* on a `width`×`height` sheet:
 * an outfit's four side by side, the others two by two. */
export function panelSize(kind, width, height) {
  return kind === "outfit"
    ? [Math.round(width / 4), height]
    : [Math.round(width / 2), Math.round(height / 2)];
}
