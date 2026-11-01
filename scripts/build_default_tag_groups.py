"""Build the default tag groups file every new library is seeded with.

Usage::

    python scripts/build_default_tag_groups.py TAGGER_META_JSON WD14_SELECTED_TAGS_CSV TOOLKIT_CSV

The three inputs are the PixlStash anomaly tagger's ``*_meta.json`` (its
``labels``), the WD14 ``selected_tags.csv`` (general tags, category 0, and
character tags, category 4), and the ComfyUI-Danbooru-Tag-Toolkit database as CSV
(``english`` / ``category_en`` / ``subcategory_en`` columns). The output is written to
``pixlstash/data/tag_groups/default_tag_groups.json``. The build is
deterministic: the tags are sorted and the output does not depend on the paths.

Each tag of the union goes through, in order:

1. Anomaly, for every tag of :func:`anomaly_tags` and for the defect tags of the
   database (``group_for`` decides both).
2. The database row, through :func:`group_for`. Adult subcategories and the
   database's "Uncategorized" bucket give no group; the character and artist
   categories file under Character and Artist.
2a. The WD14 character tags, under Character where the database did not place
   them. Each character's franchise (:func:`franchise_of`, its trailing
   parenthetical) files under Copyright, unless an earlier step placed it.
3. :data:`MANUAL_PLACEMENTS`, a hand classification of the tags the database
   cannot place. Every entry must be a tag the earlier steps left ungrouped.
4. :data:`NSFW_DROP` and :data:`NSFW_WORDS`: explicit, sexual, nudity, genital
   and bodily-fluid tags are dropped from every group. Anomaly is exempt: the
   owner settled that every known defect tag is an anomaly, whatever its words.

Anything no step places stays Ungrouped.
"""

import csv
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pixlstash.services.tag_group_defaults import (  # noqa: E402
    ANOMALY,
    ARTIST,
    CHARACTER,
    COPYRIGHT,
    DEFAULT_GROUP_NAMES,
    anomaly_tags,
    franchise_of,
    group_for,
    normalise_tag,
)

OUTPUT_PATH = (
    REPO_ROOT / "pixlstash" / "data" / "tag_groups" / "default_tag_groups.json"
)

SOURCE = (
    "Built by scripts/build_default_tag_groups.py from the PixlStash and WD14 tagger "
    "vocabularies and the MIT ComfyUI-Danbooru-Tag-Toolkit database."
)

# Tags the toolkit cannot place (no row, or an "Uncategorized" row), classified by
# hand. Only SFW tags are listed; anything not listed stays Ungrouped (characters,
# artists, series, adult material, and tags whose meaning is unclear).
# The database files these vocabulary tags as artist rows, and none of them is an
# artist the taggers can see: meta labels, a setting, clothing, and characters.
# Every artist row in the vocabulary must be placed here, so a new one is read by
# a person rather than filed under Artist; the Artist group itself starts empty
# and fills from an import.
ARTIST_ROW_OVERRIDES: dict[str, str] = {
    "abe nana": "Character",
    "artist logo": "Style & meta",
    "artist name": "Style & meta",
    "artist self-insert": "People",
    "bokukawauso": "Character",
    "creator connection": "Style & meta",
    "deviantart username": "Style & meta",
    "egasumi": "Clothing",
    "enpera": "Clothing",
    "kariya masaki": "Character",
    "lain paterson": "Character",
    "oomori yuuko": "Character",
    "oyama mahiro": "Character",
    "patreon username": "Style & meta",
    "pixiv username": "Style & meta",
    "reference sheet": "Style & meta",
    "sato shin": "Character",
    "scenery": "Setting",
    "signature": "Style & meta",
    "takei hisa": "Character",
    "yorita yoshino": "Character",
}

MANUAL_PLACEMENTS: dict[str, tuple[str, ...]] = {
    "People": (
        "3people",
        "4people",
        "age",
        "elderly person",
        "elderly woman",
        "man",
        "men",
        "middle-aged",
        "middle-aged woman",
        "mature",
        "multiple people",
        "older",
        "older man",
        "older woman",
        "six or more boys",
        "six or more girls",
        "three women",
        "two women",
        "four women",
        "woman",
        "young woman",
        "younger",
        "younger woman",
    ),
    "Face & expression": (
        "3:",
        "big smile",
        "cross eyed",
        "eyebrows visible through hair",
        "eyes visible through-hair",
        "eyeliner butterfly",
        "eyeliner on lower eyelid",
        "eyeliner puddles",
        "eyeliner stripe",
        "face",
        "forehead visible",
        "light blue eyes",
        "looking at the camera",
        "make-up",
        "medium pupils",
        "parted eyelashes",
        "round face",
        "round nose",
        "serious expression",
        "thick eyeliner",
        "thick eyeshadow",
        "thick teeth",
        "thin eyelashes",
        "unfocused eyes",
        "white teeth",
    ),
    "Hair": (
        "afro hairstyle",
        "grey short hair",
        "light brown hair",
        "light gray hair",
        "medium bangs",
        "medium short hair",
        "silver hair",
        "thin hair",
        "twin tails",
        "undercut hairstyle",
    ),
    "Body": (
        "abdominal muscles",
        "alternate pectoral size",
        "alternate breast size",
        "bare pectorals",
        "bear tail",
        "belly button hair",
        "bouncing breasts",
        "breast conscious",
        "breast envy",
        "breast expansion",
        "breasts",
        "breasts apart",
        "buttocks",
        "cleavage",
        "flat chest",
        "foot",
        "gigantic breasts",
        "hand",
        "hands",
        "hanging breasts",
        "hedgehog ears",
        "hedgehog tail",
        "hips",
        "huge breasts",
        "large pectoral muscles",
        "large pectorals",
        "leg",
        "large breasts",
        "medium breasts",
        "medium skin",
        "medium skin tone",
        "medium thighs",
        "midriff showing",
        "mole on body",
        "pectoral muscles",
        "pectorals",
        "perky breasts",
        "plump body",
        "pointy breasts",
        "pokemon ears",
        "pregnant belly",
        "ridiculous breasts",
        "sagging breasts",
        "shoulder blade",
        "shoulder blade tattoo",
        "slim hips",
        "slim thighs",
        "small breasts",
        "short thighs",
        "sparse chest hair",
        "sunburnt",
        "taut abdomen",
        "tattoo on hand",
        "thick fingernails",
        "thick nails",
        "painted toenails",
        "blue painted nails",
        "pink painted nails",
        "purple painted nails",
        "red painted nails",
        "unaligned breasts",
        "upper body tattoo",
        "veiny breasts",
        "bursting breasts",
    ),
    "Clothing": (
        "argyle legwear",
        "beanie hat",
        "black tie",
        "black track pants",
        "blue bowtie (different style)",
        "blue burqa",
        "burqa",
        "purple burqa",
        "blue denim",
        "denim pants",
        "blue floral print",
        "floral print top",
        "floral skirt",
        "flower print",
        "floral vest",
        "white floral print",
        "printed dress",
        "print top",
        "blue short sleeves",
        "blue track pants",
        "green track pants",
        "purple track pants",
        "red track pants",
        "yellow track suit",
        "multicolored track suit",
        "two-tone track suit",
        "track suit (jacket)",
        "track suit jacket",
        "track suit pants",
        "blue trousers",
        "light gray pants",
        "medium pants",
        "trousers",
        "bra with lace trim",
        "collared sleeves",
        "collared t-shirt",
        "collared top",
        "collared tshirt",
        "cutoff shorts",
        "dress shirt no tie",
        "dress tie",
        "dress vest",
        "front-button shirt",
        "front shirt",
        "front vest",
        "front-tie",
        "front-tuck",
        "front-tucked",
        "front buttons",
        "white tie",
        "double vest",
        "vest (no sleeves)",
        "vest on dress",
        "vest over shirt",
        "vest shirt",
        "short vest",
        "thin vest",
        "unbuttoned vest",
        "medium vest",
        "light blue vest",
        "light gray vest",
        "polka dot vest",
        "short tank top",
        "sleeveless outfit",
        "sleeveless sleeves",
        "sleeveless tank top",
        "sleeveless top",
        "strapless sleeveless",
        "strapless top",
        "strapless vest",
        "strapless pajamas",
        "short sleeves (on shirt)",
        "short sleeves (on shirt and long sleeves)",
        "short tight sleeves",
        "medium sleeves",
        "thick sleeves",
        "tan sleeves",
        "skintight",
        "skintight shirt",
        "skintight shorts",
        "skintight vest",
        "skirt over pajamas",
        "skirt over pants",
        "shirt under jacket",
        "shirt vest",
        "t-shirt under dress",
        "t-shirt under shirt",
        "turtleneck sublayer",
        "two-tone clothes",
        "taut top",
        "taut topwear",
        "v-neck dress",
        "vietnamese dress",
        "egyptian clothes",
        "dougi (martial arts uniform)",
        "formal attire",
        "formalwear",
        "robe bow",
        "robe bowtie",
        "robe dress",
        "robe tied at waist",
        "side-tie clothes",
        "side-tie top",
        "high collar chest",
        "high waist",
        "high waist pants",
        "high waisted",
        "high waisted pants",
        "high-leg swimsuit",
        "jacket removed",
        "off-shoulder",
        "see-through",
        "see-through swimsuit",
        "knee-high socks",
        "ribbon-trimmed legwear",
        "sarashi (cloth wrap)",
        "striped",
        "strap dress",
        "strap over shoulder",
        "strap slipping",
        "shirt tied at waist",
        "wearing only a bikini top",
        "clothes lifted",
        "shirt lifted",
        "ridiculous clothing",
    ),
    "Accessories": (
        "baseball cap worn",
        "black wristwatch",
        "watch on wrist",
        "white wristwatch",
        "dangle earrings",
        "drop earrings",
        "floral earrings",
        "floral stud earrings",
        "flower stud earrings",
        "hair bowtie",
        "hair stud",
        "headband removed",
        "headwear removed",
        "hip bars",
        "hoop piercing",
        "holstered weapon",
        "no hat",
        "phoenix crown",
        "sun glasses",
        "thick necklace",
        "single necklace",
        "thick ribbon",
        "thick rimmed eyewear",
        "wimple headwear",
        "wrist ring",
        "wristband on right wrist",
    ),
    "Pose & action": (
        "bubble blowing",
        "carrying bag",
        "ear biting",
        "glove biting",
        "hair tucking",
        "hair twirling",
        "hands at sides",
        "hands by side",
        "hands clasped together",
        "hands down",
        "hands on own hands",
        "hands on own lap",
        "hands on own pockets",
        "hands on own side",
        "hands on own sides",
        "hands on own waist",
        "hands on pockets",
        "hands on side",
        "hands on thighs on both sides",
        "hands on thighs on opposite side",
        "hands on thighs on opposite thigh",
        "hands on thighs on own hips",
        "hands on thighs on own side",
        "hands on thighs on own thigh",
        "hands on thighs on own thighs",
        "hands on thighs on side",
        "hands on thighs on sides",
        "hands on thighs on waist",
        "hands on thighs pull",
        "hands on thorax",
        "hands up hips",
        "hands up own hips",
        "hands up thighs",
        "head resting",
        "head tilt to the right",
        "head towards camera",
        "head towards viewer",
        "headturn",
        "facing side",
        "facing the viewer",
        "holding a ball",
        "holding a bag",
        "holding a book",
        "holding a flower",
        "holding a knife",
        "holding a phone",
        "holding a strap",
        "holding clothes",
        "holding orange",
        "holding soil",
        "lying down",
        "lying on back",
        "lying on stomach",
        "on all fours",
        "own hands on own hands",
        "own hands on own shoulders",
        "own hands on shoulders",
        "planted arm",
        "planted arms",
        "planted braid",
        "planted hand",
        "sitting on lap",
        "sitting on top of person",
        "standing on floor",
        "standing on grass",
        "standing tight rope",
        "standing together",
        "wading in water",
        "yokozuwari (kneeling pose)",
        "japanese sitting style (wariza)",
        "knees together",
        "knees together feet apart (position)",
        "knees together feet in opposite direction",
        "v-shaped arms",
    ),
    "Setting": (
        "chain link fence",
        "city street",
        "cloudy",
        "door in background",
        "green tree",
        "office lounge",
        "photo wall",
        "road surface",
        "room",
        "sand on ground",
        "sandcastle",
        "shopping street",
        "sunlight through-window",
        "silhouette against window",
        "wall",
        "window grid",
        "wintery",
        "foggy",
    ),
    "Objects": (
        "bolt",
        "brown bar stool",
        "camera on body",
        "flower vase",
        "ground vehicle",
        "keyboard (computer)",
        "keyboard (musical instrument)",
        "mechanical parts",
        "motor vehicle present",
        "mouse (computer)",
        "photo display",
        "photo print",
        "photograph (object)",
        "plant pot on ground",
        "plant pot with flower",
        "prosthetic arm",
        "single mechanical arm",
        "small firearm",
        "smartphone visible",
        "stack of books",
        "telstar",
        "telstar (soccer ball)",
    ),
    "Composition": (
        "4others",
        "female point of view",
        "focus on feet",
        "close-up view",
        "from the side",
        "front",
        "point of view (pov)",
        "upper body focus",
    ),
    "Style & meta": (
        "2007",
        "2008",
        "2009",
        "2010",
        "2011",
        "2012",
        "2013",
        "2014",
        "2015",
        "2016",
        "2017",
        "2018",
        "2019",
        "2020",
        "2021",
        "2022",
        "2023",
        "2024",
        "anniversary",
        "birthday",
        "blurry city",
        "blurry object",
        "blurry people",
        "blurry sky",
        "blurry street",
        "blurry tree",
        "blurry water",
        "blurry watermark",
        "blurry writing",
        "cat day",
        "chinese zodiac",
        "depth of field (photography)",
        "film (medium)",
        "happy birthday",
        "happy halloween",
        "happy new year",
        "happy valentine",
        "ink (medium)",
        "koishi day",
        "meta:photo-recreation",
        "meta:photograph",
        "meta:photoshop",
        "meta:photoshop (medium)",
        "merry christmas",
        "real image",
        "shiny",
        "silhouette profile",
        "trick or treat",
        "unfocused",
        "white day",
        "year of the dragon",
        "year of the rabbit",
        "year of the rooster",
        "year of the tiger",
        "zodiac",
    ),
}

# Explicit drops, for the tags a group would otherwise take from the database or
# from MANUAL_PLACEMENTS. Adult subcategories are already ungrouped by group_for.
NSFW_DROP: frozenset[str] = frozenset(
    normalise_tag(t)
    for t in (
        # Sexualised people, and sexual-orientation or work descriptors.
        "bisexual female",
        "stripper",
        "slave",
        "futanari",
        "full-package futanari",
        # Body: explicit anatomy and sexualised poses.
        "butt crack",
        "underbutt",
        "ass ripple",
        "ass grab",
        "cameltoe",
        "groin",
        "groin tendon",
        "crotch",
        "crotch rub",
        "bulge",
        "breasts exposed",
        "one breast exposed",
        "one breast out",
        "breasts out",
        "breasts squeezed together",
        "breasts on head",
        "breast slip",
        "breast press",
        "breast smother",
        "breast sucking",
        "breast milk",
        "breast hold",
        "breasts positioned apart",
        "newhalf",
        "chocolate on breasts",
        "object on breast",
        "hands on ass",
        "hands on thighs on pussy",
        # Clothing: undress-by-design garments.
        "ass cutout",
        "breast cutout",
        "breastless clothes",
        "cupless bra",
        "crotch cutout",
        "crotchless",
        "crotchless panties",
        "crotchless pantyhose",
        "panties removed",
        "partially undressed",
        "no bra",
        "no panties",
        "underwear only",
        "untied panties",
        "untied bikini",
        "wet panties",
        "stained panties",
        "male underwear pull",
        "panty lift",
        "panty pull",
        "panty peek",
        "pantyhose pull",
        "sports bra lift",
        "camisole lift",
        "bikini top lift",
        "bra lift",
        "bra pull",
        "bikini pull",
        "skirt lift",
        "bra removed",
        "unworn panties",
        # Accessories: gags and restraints.
        "gag",
        "gag harness",
        "gagged",
        "cloth gag",
        "tape gag",
        "improvised gag",
        "cleave gag",
        "bound wrists",
        "crotch rope",
        "ball and chain restraint",
        "stationary restraints",
        "viewer holding leash",
        # Pose and action.
        "accidental exposure",
        "arm between breasts",
        "arm under breasts",
        "arms under breasts",
        "ass support",
        "bound",
        "bound ankles",
        "bound arms",
        "bound legs",
        "bound together",
        "bra lift",
        "breast lift",
        "breast poke",
        "breast rest",
        "breast suppress",
        "breasts on glass",
        "breasts on table",
        "crotch grab",
        "finger sucking",
        "grabbing another's ass",
        "grabbing another's breast",
        "grabbing own ass",
        "grabbing own breast",
        "hand in panties",
        "hand on another's ass",
        "have to pee",
        "holding condom",
        "lifting another's clothes",
        "licking armpit",
        "pectoral grab",
        "pectoral press",
        "pee",
        "peeing",
        "peeing self",
        "restrained",
        "sitting on face",
        "spread legs",
        "wide spread legs",
        # Setting and objects.
        "mixed-sex bathing",
        "same-sex bathing",
        "shared bathing",
        "milking machine",
        "stripper pole",
        "tentacle pit",
        "tentacles on male",
        "tentacles under clothes",
        # Composition and framing.
        "ass focus",
        "ass visible through thighs",
        "breast focus",
        "framed breasts",
        "pov crotch",
        "pantyshot",
        "upskirt",
        # Style and meta.
        "assisted exposure",
        "nude cover",
        # Animals: genitals.
        "dog penis",
        "horse penis",
        # Face: explicit gaze and humiliation.
        "humiliation",
        "looking at breasts",
        "looking at penis",
        # Audit of the non-Anomaly groups (the review of issue #2469): sexual,
        # fetish, nudity, minors-in-a-suggestive-context and explicit gore or
        # torture the earlier lists missed. Anomaly is exempt, by the owner's rule.
        "child on child",
        "sadism",
        "masochism",
        "dominatrix",
        "female pervert",
        "pervert",
        "nudist",
        "mesugaki",
        "josou seme",
        "giantess",
        "aged down",
        "age regression",
        "age difference",
        "size difference",
        "teacher and student",
        "boy sandwich",
        "girl sandwich",
        "sandwiched",
        "corpse",
        "headless",
        "ahegao",
        "aroused",
        "moaning",
        "heavy breathing",
        "naughty face",
        "seductive smile",
        "severed head",
        "severed limb",
        "disembodied head",
        "intestines",
        "cream on face",
        "mouth drool",
        "saliva trail",
        "saliva drip",
        "buttocks",
        "dimples of venus",
        "toe cleavage",
        "hickey",
        "whip marks",
        "stomach bulge",
        "breast expansion",
        "bouncing breasts",
        "bursting breasts",
        "breasts apart",
        "panties on head",
        "eyepatch bikini",
        "chained",
        "chained wrists",
        "toilet use",
        "under covers",
        "upshirt",
        "x-ray",
        "bara",
        "guro",
        "bridal lingerie",
        "cat lingerie",
        "black babydoll",
        "negligee",
        "garter belt",
        "garter straps",
        "black garter belt",
        "white garter belt",
        "black garter straps",
        "white garter straps",
        "single garter strap",
        "legwear garter",
        "cleavage cutout",
        "underboob cutout",
        "gusset",
        "crotch plate",
        "crotch seam",
        "crotch zipper",
        "panty straps",
        "pantylines",
        "bra peek",
        "male underwear peek",
        "leotard peek",
        "panties around one leg",
        "shorts around one leg",
        "skirt around one leg",
        "apron lift",
        "clothes lift",
        "clothes lifted",
        "dress lift",
        "hoodie lift",
        "jacket lift",
        "kimono lift",
        "sailor collar lift",
        "shirt lift",
        "shirt lifted",
        "strap lift",
        "sweater lift",
        "clothes down",
        "clothing aside",
        "clothes pull",
        "clothes grab",
        "dress pull",
        "shirt pull",
        "sweater pull",
        "jacket pull",
        "kimono pull",
        "skirt flip",
        "skirt pull",
        "pants pull",
        "shorts pull",
        "one-piece swimsuit pull",
        "leotard pull",
        "leotard aside",
        "swimsuit aside",
        "buruma aside",
        "bikini bottom aside",
        "bikini bottom only",
        "bikini top only",
        "wearing only a bikini top",
        "clothes theft",
        "tearing clothes",
        "pulling another's clothes",
        "strap slip",
        "strap slipping",
        "partially unbuttoned",
        "partially unzipped",
        "open fly",
        "open bra",
        "open clothes",
        "impossible clothes",
        "impossible bodysuit",
        "impossible dress",
        "impossible leotard",
        "impossible shirt",
        "impossible swimsuit",
        "micro bikini",
        "micro bra",
        "micro panties",
        "micro shorts",
        "microskirt",
        "highleg panties",
        "lowleg panties",
        "highleg bikini",
        "lowleg bikini",
        "string bikini",
        "string bra",
        "string panties",
        "slingshot swimsuit",
        "bodystocking",
        "torn bodystocking",
        "latex",
        "latex bodysuit",
        "latex legwear",
        "chest harness",
        "strap between breasts",
        "belt collar",
        "o-ring bikini",
        "o-ring bottom",
        "o-ring swimsuit",
        "o-ring top",
        "straitjacket",
        "playboy bunny",
        "male playboy bunny",
        "nontraditional playboy bunny",
        "reverse bunnysuit",
        "maid bikini",
        "virgin killer outfit",
        "virgin killer sweater",
        "virgin destroyer sweater",
        "wardrobe malfunction",
        "dissolving clothes",
        "exploding clothes",
        "frontless outfit",
        "condom belt",
        "diaper",
        "unworn bikini bottom",
        "unworn bikini top",
        "unworn bra",
        "no pants",
        "no shirt",
        "upshorts",
        "downblouse",
        "swastika",
        "tight underwear",
        "holding bra",
        "holding panties",
        "holding underwear",
        "partially undressed",
        "tied up (nonsexual)",
        "hogtie",
        "suspension",
        "asphyxiation",
        "strangling",
        "suicide",
        "self-harm",
        "abuse",
        "decapitation",
        "impaled",
        "torture",
        "chikan",
        "voyeurism",
        "exhibitionism",
        "public indecency",
        "prostitution",
        "frottage",
        "gokkun",
        "ryona",
        "human scabbard",
        "sperm cell",
        "food on body",
        "food on breasts",
        "foot worship",
        "presenting foot",
        "pet play",
        "flat chest grab",
        "covering breasts",
        "covering crotch",
        "covering privates",
        "covering one breast",
        "covering ass",
        "hands on own breasts",
        "hand on own ass",
        "hand under clothes",
        "hand under shirt",
        "hand between legs",
        "arm between legs",
        "arms between legs",
        "between legs",
        "between thighs",
        "leg between thighs",
        "finger in another's mouth",
        "finger in own mouth",
        "pole dancing",
        "m legs",
        "adjusting panties",
        "phallic symbol",
        "handcuffs",
        "shackles",
        "riding crop",
        "leash",
        "chain leash",
        "leash pull",
        "stained sheets",
        "one-piece thong",
        "g-string",
        "underboob",
    )
)

# A whole-word match on any of these, in any group but Anomaly, is a failure. It is
# the guard behind NSFW_DROP: a reviewer can add an explicit drop, and this check
# stops a keyword the audit did not see from reaching the defaults. Each phrase
# alternative carries its own word boundaries.
NSFW_WORDS = re.compile(
    r"\b(?:un)?(?:censor\w*)\b|\b(?:nude|nudity|nudist|naked|nipples?|nippleless|"
    r"pussy|penis|penises|vagina|vaginal|cum|cumshot|creampie|sex|sexual|sexually|"
    r"fellatio|cunnilingus|paizuri|handjob|footjob|blowjob|anal|anus|testicles?|"
    r"erection|masturbation|masturbating|orgasm|ejaculation|precum|rape|dildo|"
    r"vibrator|topless|bottomless|pubic|clitoris|clitoral|vulva|futa|futanari|"
    r"genitals?|crotchless|crotch|bondage|shibari|loli|lolicon|shota|yaoi|yuri|"
    r"incest|bdsm|gangbang|threesome|orgy|lactation|lactating|breastfeeding|milking|"
    r"spanking|spanked|groping|molestation|undressing|upskirt|upshirt|upshorts|"
    r"downblouse|pantyshot|urethra|foreskin|labia|bukkake|nsfw|hentai|porn\w*|"
    r"cameltoe|buttjob|hairjob|sexualized|"
    r"thongs?|g-?strings?|underboobs?|sideboobs?|boobs?|boobplate|tits?|titty|titties|"
    r"sperm|butts?|asses|ass|sadism|sadistic|masochism|masochistic|torture\w*|"
    r"dominatrix|femdom|pervert\w*|prostitut\w*|voyeur\w*|exhibition\w*|chikan|"
    r"gokkun|frottage|ryona|bara|guro|aroused|arousal|moaning|ahegao|hogtie|"
    r"asphyxiat\w*|strangl\w*|decapitat\w*|dismember\w*|disembowel\w*|impal\w*|"
    r"severed|intestines?|entrails|gore|gory|headless|lingerie|babydoll|negligee|"
    r"pasties|jockstrap|breastless|diaper|hickey|gusset|see-?through|josou|mesugaki|"
    r"suicide|self-harm|abuse)\b|"
    r"\b(?:child|children|kid|kids|toddler|infant|underage|minors?)[\s_-]*"
    r"(?:on|with|sex\w*|nud\w*|naked|porn\w*|molest\w*|rape|fuck\w*|grop\w*)\b|"
    r"\b(?:on|with|sex\w*|nud\w*|naked|porn\w*|molest\w*|rape|fuck\w*|grop\w*)[\s_-]+"
    r"(?:child|children|kid|kids|toddler|infant|underage|minors?)\b",
    re.IGNORECASE,
)


def load_tagger_labels(path: Path) -> set[str]:
    """The PixlStash anomaly tagger's labels, normalised."""
    with path.open(encoding="utf-8") as handle:
        meta = json.load(handle)
    return {normalise_tag(label) for label in meta["labels"]}


def load_wd14_category(path: Path, category: str) -> set[str]:
    """The WD14 tags of one category ("0" general, "4" character), normalised."""
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            normalise_tag(row["name"])
            for row in csv.DictReader(handle)
            if row["category"] == category
        }


def load_toolkit(path: Path) -> dict[str, tuple[str, str]]:
    """English tag -> (category_en, subcategory_en) from the toolkit CSV.

    The first row of a tag wins. Rows the converter wrote short have no
    subcategory, which reads as "".
    """
    rows: dict[str, tuple[str, str]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            tag = normalise_tag(row.get("english"))
            if tag and tag not in rows:
                rows[tag] = (
                    (row.get("category_en") or "").strip(),
                    (row.get("subcategory_en") or "").strip(),
                )
    return rows


def build_groups(
    union: set[str],
    toolkit: dict[str, tuple[str, str]],
    characters: set[str],
) -> dict[str, set[str]]:
    """Group every tag of *union* as the module docstring describes."""
    anomaly = anomaly_tags()
    groups: dict[str, set[str]] = {name: set() for name in DEFAULT_GROUP_NAMES}
    automatic: dict[str, str] = {}
    unplaced_artists: list[str] = []
    for tag in union:
        if tag in anomaly:
            automatic[tag] = ANOMALY
            continue
        if tag in toolkit:
            category, subcategory = toolkit[tag]
            group = group_for(tag, category, subcategory)
            if group == ARTIST:
                group = ARTIST_ROW_OVERRIDES.get(tag)
                if group is None:
                    unplaced_artists.append(tag)
            if group in DEFAULT_GROUP_NAMES:
                automatic[tag] = group
        if tag not in automatic and tag in characters:
            automatic[tag] = CHARACTER

    problems: list[str] = [
        f"{tag!r} is an artist row in the database: place it in ARTIST_ROW_OVERRIDES"
        for tag in sorted(unplaced_artists)
    ]
    for group, tags in MANUAL_PLACEMENTS.items():
        if group not in DEFAULT_GROUP_NAMES:
            problems.append(f"unknown group {group!r}")
            continue
        for raw in tags:
            tag = normalise_tag(raw)
            if tag not in union:
                problems.append(f"{raw!r} ({group}) is not in the tag union")
            elif tag in automatic:
                problems.append(f"{raw!r} ({group}) is already {automatic[tag]!r}")
            elif tag in anomaly:
                problems.append(f"{raw!r} ({group}) is an anomaly tag")
            else:
                automatic[tag] = group
    if problems:
        raise SystemExit("MANUAL_PLACEMENTS: " + "; ".join(problems))

    # A franchise is a Copyright tag even where the vocabulary also lists it as a
    # character ("nier:automata"). Any other placement above, a database category
    # or a manual one, stands.
    for tag in sorted(characters):
        franchise = franchise_of(tag)
        if franchise is not None and automatic.get(franchise, CHARACTER) == CHARACTER:
            automatic[franchise] = COPYRIGHT

    for tag, group in automatic.items():
        if tag in NSFW_DROP or (group != ANOMALY and NSFW_WORDS.search(tag)):
            continue
        groups[group].add(tag)

    for name in DEFAULT_GROUP_NAMES:
        if name == ANOMALY:
            groups[name].update(anomaly)
    return groups


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    tagger_path, wd14_path, toolkit_path = (Path(arg) for arg in argv)
    labels = load_tagger_labels(tagger_path)
    general = load_wd14_category(wd14_path, "0")
    characters = load_wd14_category(wd14_path, "4")
    toolkit = load_toolkit(toolkit_path)
    franchises = {franchise_of(tag) for tag in characters} - {None}
    union = labels | general | characters | franchises
    groups = build_groups(union, toolkit, characters)

    for tag in NSFW_DROP:
        if tag not in union and tag not in anomaly_tags():
            print(
                f"note: NSFW_DROP entry {tag!r} is not in the tag union",
                file=sys.stderr,
            )

    payload = {
        "version": 2,
        "source": SOURCE,
        "groups": [
            {"name": name, "tags": sorted(groups[name])} for name in DEFAULT_GROUP_NAMES
        ],
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    total = sum(len(groups[name]) for name in DEFAULT_GROUP_NAMES)
    print(f"union {len(union)}; grouped {total}; wrote {OUTPUT_PATH}")
    for name in DEFAULT_GROUP_NAMES:
        print(f"  {name}: {len(groups[name])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
