"""Face-related tag vocabulary for PixlStash.

Provides a curated, categorised set of tags that indicate a human (or
humanoid) face is present or depicted in an image.  Two tag systems are
covered:

* **WD14 tagger** – tags use underscores (e.g. ``blue_eyes``).
* **Custom anomaly tagger** – tags use spaces (e.g. ``open mouth``).

Tags were sourced by scanning ``selected_tags.csv`` from the WD14 model and
``pixlstash-anomaly-tagger_meta.json`` from the custom tagger, then filtered
to keep only those that reliably indicate a face is present.

Typical usage::

    from pixlstash.utils.face_tags import FaceTags

    picture_tags = {"blue_eyes", "long_hair", "smile", "outdoors"}
    if FaceTags.has_face_tags(picture_tags):
        print("Picture contains face-related tags")
"""

from collections.abc import Iterable


class FaceTags:
    """Curated vocabulary of face-related tags.

    All members are ``frozenset[str]``.  The combined set is ``FaceTags.ALL``.

    Tag names follow the conventions of their source tagger:
    - WD14: underscore-separated strings (``blue_eyes``)
    - Custom anomaly tagger: space-separated strings (``open mouth``)
    """

    # ── Eye colours ───────────────────────────────────────────────────────────
    EYE_COLORS: frozenset[str] = frozenset(
        {
            # WD14
            "aqua_eyes",
            "black_eyes",
            "blue_eyes",
            "brown_eyes",
            "green_eyes",
            "grey_eyes",
            "orange_eyes",
            "pink_eyes",
            "purple_eyes",
            "red_eyes",
            "white_eyes",
            "yellow_eyes",
            "alternate_eye_color",
            "gradient_eyes",
            "multicolored_eyes",
            "two-tone_eyes",
            "uneven_eyes",
            "ringed_eyes",
            # Custom tagger
            "blue eyes",
            "brown eyes",
            "green eyes",
            "grey eyes",
            "red eyes",
        }
    )

    # ── Pupil styles ──────────────────────────────────────────────────────────
    PUPILS: frozenset[str] = frozenset(
        {
            # WD14
            "blue_pupils",
            "bright_pupils",
            "constricted_pupils",
            "cross-shaped_pupils",
            "dashed_eyes",
            "diamond-shaped_pupils",
            "drop-shaped_pupils",
            "flower-shaped_pupils",
            "heart-shaped_pupils",
            "horizontal_pupils",
            "mismatched_pupils",
            "no_pupils",
            "pink_pupils",
            "purple_pupils",
            "rabbit-shaped_pupils",
            "red_pupils",
            "slit_pupils",
            "solid_circle_pupils",
            "solid_oval_eyes",
            "solid_circle_eyes",
            "solid_eyes",
            "star-shaped_pupils",
            "symbol-shaped_pupils",
            "white_pupils",
            "wide_oval_eyes",
            "x-shaped_pupils",
            "yellow_pupils",
            "v-shaped_eyes",
            "clock_eyes",
        }
    )

    # ── Eye states and actions ─────────────────────────────────────────────────
    EYE_STATES: frozenset[str] = frozenset(
        {
            # WD14
            "averting_eyes",
            "blood_from_eyes",
            "bloodshot_eyes",
            "bulging_eyes",
            "button_eyes",
            "cephalopod_eyes",
            "closed_eyes",
            "covering_another's_eyes",
            "covering_one_eye",
            "covering_own_eyes",
            "crazy_eyes",
            "crying_with_eyes_open",
            "empty_eyes",
            "extra_eyes",
            "eye_contact",
            "eye_focus",
            "eye_reflection",
            "eye_trail",
            "eyeball",
            "eyes_visible_through_hair",
            "flaming_eye",
            "flower_in_eye",
            "flower_over_eye",
            "glowing_eye",
            "glowing_eyes",
            "half-closed_eye",
            "half-closed_eyes",
            "hand_over_eye",
            "heart-shaped_eyes",
            "heart_in_eye",
            "hollow_eyes",
            "mechanical_eye",
            "narrowed_eyes",
            "no_eyes",
            "one-eyed",
            "one_eye_closed",
            "one_eye_covered",
            "rolling_eyes",
            "rubbing_eyes",
            "shading_eyes",
            "sparkling_eyes",
            "squinting",
            "star_in_eye",
            "symbol_in_eye",
            "tearing_up",
            "third_eye",
            "unusually_open_eyes",
            "upturned_eyes",
            "v_over_eye",
            "wavy_eyes",
            "wide-eyed",
            "blank_eyes",
            # Custom tagger
            "closed eyes",
            "one eye closed",
            "cross eyed",
            "cyclops",
            "eye focus",
            "unfocused eyes",
            "weird staring",
        }
    )

    # ── Eyebrows ───────────────────────────────────────────────────────────────
    EYEBROWS: frozenset[str] = frozenset(
        {
            # WD14
            "curly_eyebrows",
            "eyebrow_cut",
            "eyebrow_piercing",
            "eyebrows_hidden_by_hair",
            "forked_eyebrows",
            "furrowed_brow",
            "huge_eyebrows",
            "mismatched_eyebrows",
            "no_eyebrows",
            "raised_eyebrow",
            "raised_eyebrows",
            "short_eyebrows",
            "thick_eyebrows",
            "v-shaped_eyebrows",
            # Custom tagger
            "thick eyebrows",
        }
    )

    # ── Eyelashes and eyeliner ─────────────────────────────────────────────────
    EYELASHES: frozenset[str] = frozenset(
        {
            # WD14
            "colored_eyelashes",
            "eyelashes",
            "eyelid_pull",
            "eyeliner",
            "long_eyelashes",
            "red_eyeliner",
            "thick_eyelashes",
            # Custom tagger
            "eyelashes",
            "eyeshadow",
        }
    )

    # ── Mouth and lips ─────────────────────────────────────────────────────────
    MOUTH: frozenset[str] = frozenset(
        {
            # WD14
            "biting_own_lip",
            "black_lips",
            "blue_lips",
            "blue_tongue",
            "brown_lips",
            "buck_teeth",
            "chestnut_mouth",
            "clenched_teeth",
            "closed_mouth",
            "clothes_in_mouth",
            "colored_tongue",
            "covered_mouth",
            "covering_another's_mouth",
            "covering_own_mouth",
            "dot_mouth",
            "drinking_straw_in_mouth",
            "extra_mouth",
            "false_smile",
            "finger_in_another's_mouth",
            "finger_in_own_mouth",
            "finger_to_another's_mouth",
            "finger_to_mouth",
            "fingersmile",
            "flower_in_mouth",
            "food_in_mouth",
            "forked_tongue",
            "green_lips",
            "hair_in_own_mouth",
            "hair_tie_in_mouth",
            "hand_over_own_mouth",
            "hand_to_own_mouth",
            "heart_in_mouth",
            "licking_lips",
            "lip_piercing",
            "lipgloss",
            "lips",
            "long_tongue",
            "lower_teeth_only",
            "mouth_drool",
            "mouth_hold",
            "mouth_mask",
            "mouth_pull",
            "mouth_veil",
            "no_mouth",
            "open_mouth",
            "parted_lips",
            "pink_lips",
            "pocky_in_mouth",
            "popsicle_in_mouth",
            "puckered_lips",
            "purple_lips",
            "purple_tongue",
            "pursed_lips",
            "red_lips",
            "rectangular_mouth",
            "round_teeth",
            "saliva",
            "saliva_drip",
            "saliva_trail",
            "scarf_over_mouth",
            "sharp_teeth",
            "sideways_mouth",
            "split_mouth",
            "stalk_in_mouth",
            "stitched_mouth",
            "teeth",
            "teeth_hold",
            "thick_lips",
            "toast_in_mouth",
            "tongue",
            "tongue_out",
            "tongue_piercing",
            "tooth",
            "toothbrush",
            "triangle_mouth",
            "upper_teeth_only",
            "utensil_in_mouth",
            "v_over_mouth",
            "wavy_mouth",
            "brushing_teeth",
            "blood_from_mouth",
            "drooling",
            "mouth_drool",
            # Custom tagger
            "open mouth",
            "closed mouth",
            "parted lips",
            "lips",
            "tongue",
            "tongue out",
            "teeth",
            "saliva",
            "saliva trail",
            "sharp teeth",
            "fangs",
            "finger in own mouth",
            "finger to mouth",
            "yawning",
            "brushing teeth",
            "toothbrush",
            "malformed teeth",
            "malformed tongue",
            "uvula",
        }
    )

    # ── Nose ──────────────────────────────────────────────────────────────────
    NOSE: frozenset[str] = frozenset(
        {
            # WD14
            "big_nose",
            "dot_nose",
            "long_nose",
            "no_nose",
            "nose_blush",
            "nose_bubble",
            "nose_piercing",
            "nose_ring",
            "nosebleed",
            "noses_touching",
            "pointy_nose",
            "red_nose",
            "runny_nose",
            "snot",
            # Custom tagger
            "nose",
        }
    )

    # ── Blush ─────────────────────────────────────────────────────────────────
    BLUSH: frozenset[str] = frozenset(
        {
            # WD14
            "blush",
            "blush_stickers",
            "body_blush",
            "ear_blush",
            "full-face_blush",
            "light_blush",
            "nose_blush",
            "spoken_blush",
            # Custom tagger
            "blush",
        }
    )

    # ── Tears and crying ──────────────────────────────────────────────────────
    TEARS: frozenset[str] = frozenset(
        {
            # WD14
            "crying_with_eyes_open",
            "flying_teardrops",
            "happy_tears",
            "streaming_tears",
            "teardrop",
            "teardrop_facial_mark",
            "teardrop_tattoo",
            "tearing_up",
            "tears",
            "wiping_tears",
            # Custom tagger
            "crying",
            "tears",
        }
    )

    # ── Facial structure (cheeks, chin, forehead, jaw) ────────────────────────
    FACE_STRUCTURE: frozenset[str] = frozenset(
        {
            # WD14
            "bandaid_on_cheek",
            "cheek-to-cheek",
            "cheek_bulge",
            "cheek_pinching",
            "cheek_poking",
            "cheek_press",
            "cheek_pull",
            "cheek_squash",
            "cheekbones",
            "chin_strap",
            "finger_to_cheek",
            "finger_to_own_chin",
            "forehead",
            "forehead-to-forehead",
            "forehead_jewel",
            "forehead_mark",
            "forehead_protector",
            "forehead_tattoo",
            "grabbing_another's_chin",
            "hand_on_another's_cheek",
            "hand_on_another's_chin",
            "hand_on_forehead",
            "hand_on_own_cheek",
            "hand_on_own_chin",
            "hand_on_own_forehead",
            "hands_on_another's_cheeks",
            "hands_on_own_cheeks",
            "kissing_cheek",
            "kissing_forehead",
            "scratching_cheek",
            "scar_on_cheek",
            "scar_on_forehead",
            "stroking_own_chin",
            # Custom tagger
            "hands on own cheeks",
            "hand on own chin",
            "stroking own chin",
        }
    )

    # ── Face marks (moles, freckles, scars) ───────────────────────────────────
    FACE_MARKS: frozenset[str] = frozenset(
        {
            # WD14
            "freckles",
            "body_freckles",
            "mole",
            "mole_on_cheek",
            "mole_under_each_eye",
            "mole_under_eye",
            "mole_under_mouth",
            "multiple_moles",
            "no_mole",
            "scar_across_eye",
            "scar_on_cheek",
            "scar_on_face",
            "scar_on_mouth",
            "scar_on_nose",
            # Custom tagger
            "freckles",
            "mole on cheek",
            "mole under mouth",
            "scar across eye",
            "scar on face",
        }
    )

    # ── Beard and facial hair ─────────────────────────────────────────────────
    FACIAL_HAIR: frozenset[str] = frozenset(
        {
            # WD14
            "beard",
            "beard_stubble",
            "fake_mustache",
            "full_beard",
            "long_beard",
            "mustache",
            "mustache_stubble",
            "thick_beard",
            "thick_mustache",
            # Custom tagger
            "beard",
            "mustache",
            "facial hair",
            "stubble",
        }
    )

    # ── Whole-face descriptors ────────────────────────────────────────────────
    FACE_GENERAL: frozenset[str] = frozenset(
        {
            # WD14
            "bandage_on_face",
            "bandage_over_one_eye",
            "bandaid_on_face",
            "bandaid_on_nose",
            "blood_on_face",
            "bruise_on_face",
            "covered_eyes",
            "covered_face",
            "covering_face",
            "cream_on_face",
            "dirty_face",
            "disembodied_head",
            "eye_black",
            "eye_mask",
            "eye_of_horus",
            "face-to-face",
            "face_in_pillow",
            "face_to_breasts",
            "faceless",
            "faceless_female",
            "faceless_male",
            "facepaint",
            "facepalm",
            "food_on_face",
            "hand_on_another's_face",
            "hand_on_own_face",
            "hand_over_face",
            "hands_on_another's_face",
            "hands_on_own_face",
            "in_the_face",
            "licking_another's_face",
            "naughty_face",
            "paint_splatter_on_face",
            "rice_on_face",
            "shaded_face",
            "sitting_on_face",
            "sticker_on_face",
            "stitched_face",
            "wiping_face",
            # Custom tagger
            "face",
            "covered face",
            "faceless female",
            "hand on own face",
            "hands on own face",
            "hand on own chin",
        }
    )

    # ── Expressions ───────────────────────────────────────────────────────────
    EXPRESSIONS: frozenset[str] = frozenset(
        {
            # WD14
            "crazy_smile",
            "evil_grin",
            "evil_smile",
            "false_smile",
            "forced_smile",
            "frown",
            "grin",
            "light_frown",
            "light_smile",
            "nervous_smile",
            "seductive_smile",
            "smile",
            # Custom tagger
            "smile",
            "grin",
            "happy",
            "crying",
            "serious expression",
            "unnatural smile",
        }
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Combined set — derive once at class-definition time.
    # ─────────────────────────────────────────────────────────────────────────
    ALL: frozenset[str] = (
        EYE_COLORS
        | PUPILS
        | EYE_STATES
        | EYEBROWS
        | EYELASHES
        | MOUTH
        | NOSE
        | BLUSH
        | TEARS
        | FACE_STRUCTURE
        | FACE_MARKS
        | FACIAL_HAIR
        | FACE_GENERAL
        | EXPRESSIONS
    )

    @classmethod
    def has_face_tags(cls, tags: "Iterable[str]") -> bool:
        """Return ``True`` if *tags* contains at least one face-related tag.

        Args:
            tags: Any iterable of tag strings (list, set, generator…).

        Returns:
            ``True`` when at least one tag is in :attr:`ALL`.
        """
        return any(tag in cls.ALL for tag in tags)

    @classmethod
    def filter_face_tags(cls, tags: "Iterable[str]") -> "frozenset[str]":
        """Return the subset of *tags* that are face-related.

        Args:
            tags: Any iterable of tag strings.

        Returns:
            A ``frozenset`` containing only the face-related tags.
        """
        return frozenset(tag for tag in tags if tag in cls.ALL)
