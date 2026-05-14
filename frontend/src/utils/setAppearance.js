/**
 * Shared icon and color palette for picture sets.
 * Must be kept in sync with the palette in pixlstash/routes/picture_sets.py.
 */

/** Sentinel value — use the animated card-stack thumbnail instead of an icon. */
export const ICON_CARDS = "cards";

/**
 * Icons grouped by category. The cards sentinel is the first entry of
 * "Photography" so it always appears at position [0][0] in the grid.
 */
export const SET_ICON_CATEGORIES = [
  {
    label: "Photography",
    icons: [
      { value: ICON_CARDS, label: "Thumbnail Stack" },
      { value: "mdi-camera", label: "Camera" },
      { value: "mdi-image-multiple", label: "Photos" },
      { value: "mdi-image-album", label: "Album" },
      { value: "mdi-bookmark", label: "Bookmark" },
      { value: "mdi-folder-image", label: "Folder" },
      { value: "mdi-camera-iris", label: "Aperture" },
      { value: "mdi-film", label: "Film" },
      { value: "mdi-image-frame", label: "Frame" },
    ],
  },
  {
    label: "Favourites",
    icons: [
      { value: "mdi-star", label: "Star" },
      { value: "mdi-heart", label: "Heart" },
      { value: "mdi-crown", label: "Crown" },
      { value: "mdi-trophy", label: "Trophy" },
      { value: "mdi-flag", label: "Important" },
      { value: "mdi-alert", label: "Alert" },
      { value: "mdi-fire", label: "Fire" },
      { value: "mdi-diamond-stone", label: "Diamond" },
    ],
  },
  {
    label: "Family",
    icons: [
      { value: "mdi-account-group", label: "Family" },
      { value: "mdi-human-male-female-child", label: "Parents & Child" },
      { value: "mdi-baby-face-outline", label: "Baby" },
      { value: "mdi-human-child", label: "Child" },
      { value: "mdi-dog", label: "Dog" },
      { value: "mdi-cat", label: "Cat" },
      { value: "mdi-baby-carriage", label: "Stroller" },
      { value: "mdi-home-heart", label: "Family Home" },
    ],
  },
  {
    label: "Clothing",
    icons: [
      { value: "mdi-hanger", label: "Hanger" },
      { value: "mdi-tshirt-crew", label: "T-Shirt" },
      { value: "mdi-shoe-heel", label: "Heels" },
      { value: "mdi-sunglasses", label: "Sunglasses" },
      { value: "mdi-hat-fedora", label: "Hat" },
      { value: "mdi-bag-personal", label: "Handbag" },
      { value: "mdi-watch", label: "Watch" },
      { value: "mdi-tie", label: "Tie" },
    ],
  },
  {
    label: "Home",
    icons: [
      { value: "mdi-home", label: "Home" },
      { value: "mdi-bed", label: "Bedroom" },
      { value: "mdi-sofa", label: "Living Room" },
      { value: "mdi-music", label: "Music" },
      { value: "mdi-television", label: "TV" },
      { value: "mdi-shower", label: "Bathroom" },
      { value: "mdi-desk-lamp", label: "Lamp" },
      { value: "mdi-fireplace", label: "Fireplace" },
    ],
  },
  {
    label: "Food & Drink",
    icons: [
      { value: "mdi-silverware-fork-knife", label: "Dining" },
      { value: "mdi-cup", label: "Drinks" },
      { value: "mdi-glass-cocktail", label: "Cocktails" },
      { value: "mdi-food-apple", label: "Food" },
      { value: "mdi-cake-variant", label: "Celebration" },
      { value: "mdi-coffee", label: "Coffee" },
      { value: "mdi-pizza", label: "Pizza" },
      { value: "mdi-beer", label: "Beer" },
    ],
  },
  {
    label: "Travel",
    icons: [
      { value: "mdi-airplane", label: "Travel" },
      { value: "mdi-beach", label: "Beach" },
      { value: "mdi-hiking", label: "Hiking" },
      { value: "mdi-city-variant", label: "City" },
      { value: "mdi-pine-tree", label: "Nature" },
      { value: "mdi-flower", label: "Flowers" },
      { value: "mdi-map-marker", label: "Location" },
      { value: "mdi-tent", label: "Camping" },
    ],
  },
  {
    label: "Transport",
    icons: [
      { value: "mdi-car", label: "Car" },
      { value: "mdi-bike", label: "Cycling" },
      { value: "mdi-run", label: "Running" },
      { value: "mdi-bus", label: "Bus" },
      { value: "mdi-train", label: "Train" },
      { value: "mdi-motorbike", label: "Motorcycle" },
      { value: "mdi-walk", label: "Walking" },
      { value: "mdi-tram", label: "Tram" },
    ],
  },
  {
    label: "Sports",
    icons: [
      { value: "mdi-basketball", label: "Basketball" },
      { value: "mdi-football", label: "Football" },
      { value: "mdi-weight-lifter", label: "Gym" },
      { value: "mdi-swim", label: "Swimming" },
      { value: "mdi-table-tennis", label: "Table Tennis" },
      { value: "mdi-ski", label: "Skiing" },
      { value: "mdi-bowling", label: "Bowling" },
      { value: "mdi-golf", label: "Golf" },
    ],
  },
  {
    label: "Work & Tech",
    icons: [
      { value: "mdi-briefcase", label: "Work" },
      { value: "mdi-gamepad-variant", label: "Gaming" },
      { value: "mdi-monitor", label: "Screen" },
      { value: "mdi-school", label: "Education" },
      { value: "mdi-code-braces", label: "Coding" },
      { value: "mdi-chart-bar", label: "Analytics" },
      { value: "mdi-stethoscope", label: "Medical" },
      { value: "mdi-flask", label: "Science" },
    ],
  },
];

/** Flat list derived from categories (excludes the ICON_CARDS sentinel). */
export const SET_ICONS = SET_ICON_CATEGORIES.flatMap((cat) =>
  cat.icons.filter((ic) => ic.value !== ICON_CARDS),
);

export const SET_COLORS = [
  { value: "#e53935", label: "Red" },
  { value: "#00acc1", label: "Cyan" },
  { value: "#f4511e", label: "Burnt Orange" },
  { value: "#039be5", label: "Light Blue" },
  { value: "#ff7043", label: "Deep Orange" },
  { value: "#546e7a", label: "Blue Grey" },
  { value: "#fb8c00", label: "Orange" },
  { value: "#1e88e5", label: "Blue" },
  { value: "#fdd835", label: "Yellow" },
  { value: "#3949ab", label: "Indigo" },
  { value: "#c0ca33", label: "Lime" },
  { value: "#9c27b0", label: "Deep Purple" },
  { value: "#7cb342", label: "Light Green" },
  { value: "#8e24aa", label: "Purple" },
  { value: "#43a047", label: "Green" },
  { value: "#d81b60", label: "Magenta" },
  { value: "#00897b", label: "Teal" },
  { value: "#f06292", label: "Pink" },
  { value: "#00bfa5", label: "Teal Accent" },
  { value: "#6d4c41", label: "Brown" },
  { value: "#ff5252", label: "Coral" },
  { value: "#00e5ff", label: "Aqua" },
  { value: "#ff6d00", label: "Vivid Orange" },
  { value: "#2979ff", label: "Bright Blue" },
  { value: "#ffd740", label: "Amber" },
  { value: "#651fff", label: "Violet" },
  { value: "#64dd17", label: "Bright Lime" },
  { value: "#e040fb", label: "Vivid Purple" },
  { value: "#1de9b6", label: "Mint" },
  { value: "#f50057", label: "Hot Pink" },
  { value: "#b71c1c", label: "Dark Red" },
  { value: "#006064", label: "Dark Teal" },
  { value: "#e65100", label: "Dark Orange" },
  { value: "#0d47a1", label: "Dark Blue" },
  { value: "#827717", label: "Olive" },
  { value: "#4a148c", label: "Dark Purple" },
  { value: "#1b5e20", label: "Forest" },
  { value: "#880e4f", label: "Dark Magenta" },
  { value: "#004d40", label: "Dark Teal Deep" },
  { value: "#37474f", label: "Slate" },
  { value: "#ce93d8", label: "Lavender" },
  { value: "#ef9a9a", label: "Rose" },
  { value: "#81d4fa", label: "Sky Blue" },
  { value: "#ff8a65", label: "Peach" },
  { value: "#ffb300", label: "Gold" },
  { value: "#80cbc4", label: "Soft Teal" },
  { value: "#a1887f", label: "Warm Brown" },
  { value: "#76ff03", label: "Electric Green" },
];
