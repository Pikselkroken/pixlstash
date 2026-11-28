/**
 * What a Studio card's menu does to the item behind it, by kind (#2819).
 *
 * A scene is a Studio record; a person is a character; an outfit, a location
 * and a prop are picture sets listed in the Studio. Each verb goes to the API
 * that owns that thing, so the Studio's grid does what Media's sidebar does to
 * the same person or set.
 */
import { deleteCharacter, patchCharacter } from "../api/characters";
import {
  bulkAddPicturesToSet,
  createPictureSet,
  deletePictureSet,
  patchPictureSet,
} from "../api/pictureSets";
import { createScene, deleteScene, getScene, listSetInStudio, patchScene } from "../api/studio";

/** A card's key in a selection: kinds share ids, so the kind is in it. */
export const cardKey = (card) => `${card.kind}:${card.id}`;

const SET_KINDS = new Set(["outfit", "location", "prop"]);

/** Why a card cannot be duplicated, or "" when it can. */
export function duplicateReason(card) {
  return card.kind === "cast"
    ? "A person can't be duplicated: their faces are theirs alone."
    : "";
}

/** A name not among `taken`: "Pub copy", then "Pub copy 2", … */
export function copyName(name, taken) {
  const used = new Set(taken);
  for (let n = 1; ; n += 1) {
    const candidate = n === 1 ? `${name} copy` : `${name} copy ${n}`;
    if (!used.has(candidate)) return candidate;
  }
}

/**
 * A copy of the card's item, under a free name: a scene's cast, props,
 * setting and camera (not its takes, which are pictures of the original); a
 * set's pictures, project and description, listed in the Studio as the same
 * kind. Returns the copy's `{ id, name }`.
 */
export async function duplicateCard(card, takenNames) {
  const name = copyName(card.name, takenNames);
  if (card.kind === "scene") {
    const scene = await getScene(card.id);
    const made = await createScene({
      name,
      project_id: scene.project_id ?? null,
      location_set_id: scene.location_set_id ?? null,
    });
    try {
      await patchScene(made.id, {
        camera: scene.camera,
        setting: scene.setting,
        cast: scene.cast,
        props: scene.props,
      });
    } catch (e) {
      // Half a copy is not a copy: take the empty scene away again.
      await deleteScene(made.id).catch((err) =>
        console.warn("Couldn't remove a half-made scene copy", made.id, err),
      );
      throw e;
    }
    return { id: made.id, name };
  }
  if (!SET_KINDS.has(card.kind)) throw new Error(duplicateReason(card));
  const { picture_set: made } = await createPictureSet({
    name,
    description: card.description ?? "",
    project_ids: card.project_ids ?? [],
    ...(card.character_id != null ? { parent_character_id: card.character_id } : {}),
  });
  try {
    if (card.picture_ids?.length) await bulkAddPicturesToSet(made.id, card.picture_ids);
    await listSetInStudio(made.id, card.kind);
  } catch (e) {
    // Half a copy is not a copy: take the half-made set away again.
    await deletePictureSet(made.id).catch((err) =>
      console.warn("Couldn't remove a half-made set copy", made.id, err),
    );
    throw e;
  }
  return { id: made.id, name };
}

/** Delete the card's item: the scene, the person or the set. Pictures stay. */
export async function deleteCard(card) {
  if (card.kind === "scene") return deleteScene(card.id);
  if (card.kind === "cast") return deleteCharacter(card.id);
  return deletePictureSet(card.id);
}

/**
 * Put the card's item in a project, or take it out. A scene is in one project
 * at most, so adding moves it; a person or a set can be in several.
 */
export async function setCardProject(card, projectId, add) {
  const current = card.project_ids ?? [];
  if (card.kind === "scene") {
    if (!add && !current.includes(projectId)) return;
    return patchScene(card.id, { project_id: add ? projectId : null });
  }
  const next = add
    ? [...new Set([...current, projectId])]
    : current.filter((id) => id !== projectId);
  const body = { project_ids: next };
  return card.kind === "cast" ? patchCharacter(card.id, body) : patchPictureSet(card.id, body);
}

/** The confirm for deleting `cards`, said by what goes and what stays. */
export function deleteWords(cards) {
  if (cards.length === 1) {
    const [card] = cards;
    const message = {
      scene: `“${card.name}” and its cast, props and setting go. Its takes stay in the library.`,
      cast: `“${card.name}” goes from the Studio and from Media. Their pictures stay in the library, no longer theirs.`,
    }[card.kind] ?? `The set “${card.name}” goes from the Studio and from Media. Its pictures stay in the library.`;
    return {
      title: card.kind === "scene" ? "Delete this scene?" : card.kind === "cast" ? "Delete this person?" : "Delete this set?",
      message,
      confirmLabel: "Delete",
      danger: true,
    };
  }
  return {
    title: `Delete ${cards.length} items?`,
    message:
      "Scenes go with their cast, props and setting; people and sets go from Media too. Every picture stays in the library.",
    confirmLabel: `Delete ${cards.length}`,
    danger: true,
  };
}

/**
 * Put an outfit in *person*'s wardrobe: one that is no one's becomes theirs;
 * another person's is copied for them, the same pictures in a set of their
 * own, so it is taken from nobody. A copy keeps the outfit's name, or names
 * the person when another set already has it. Returns the outfit's id.
 */
export async function addOutfitToWardrobe(outfit, person) {
  if (outfit.character_id == null) {
    await patchPictureSet(outfit.id, { parent_character_id: person.id });
    return outfit.id;
  }
  const body = {
    description: outfit.description ?? "",
    project_ids: outfit.project_ids ?? [],
    parent_character_id: person.id,
  };
  let made;
  try {
    ({ picture_set: made } = await createPictureSet({ ...body, name: outfit.name }));
  } catch (e) {
    if (e?.response?.status !== 409) throw e;
    ({ picture_set: made } = await createPictureSet({
      ...body,
      name: `${outfit.name} (${person.name})`,
    }));
  }
  try {
    if (outfit.picture_ids?.length) await bulkAddPicturesToSet(made.id, outfit.picture_ids);
    await listSetInStudio(made.id, "outfit");
  } catch (e) {
    // Half a copy is not a copy: take the half-made set away again.
    await deletePictureSet(made.id).catch((err) =>
      console.warn("Couldn't remove a half-made outfit copy", made.id, err),
    );
    throw e;
  }
  return made.id;
}
