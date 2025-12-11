import numpy as np
import os
import pytest
import warnings

from pixlvault.picture_tagger import PictureTagger
from sentence_transformers import SentenceTransformer

# Global lists to collect similarities
similarities_sent = []
similarities_naive = []


def test_build_embedding_sentence_print():
    data = {
        "description": "A ninja prowling the night.",
        "tags": ["ninja", "night", "sword"],
        "characters": [
            {
                "name": "Clementine",
                "description": "A skilled ninja.",
                "original_prompt": "prowls the night with her sword.",
            }
        ],
    }
    sent = PictureTagger.build_embedding_sentence(data)
    print("Generated sentence:", sent)


def test_build_embedding_sentence_embedding_similarity():
    from pixlvault.picture_tagger import PictureTagger

    sbert_device = "cpu" if getattr(PictureTagger, "FORCE_CPU", False) else None
    if sbert_device:
        sbert = SentenceTransformer("all-MiniLM-L6-v2", device=sbert_device)
    else:
        sbert = SentenceTransformer("all-MiniLM-L6-v2")
    data = {
        "description": "A ninja prowling the night.",
        "tags": ["ninja", "night", "sword"],
        "characters": [
            {
                "name": "Clementine",
                "description": "A skilled ninja.",
                "original_prompt": "prowls the night with her sword.",
            }
        ],
    }
    generated = PictureTagger.build_embedding_sentence(data)
    reference = "Clementine is a skilled ninja prowling the night with her sword. She moves like a shadow."
    emb1 = sbert.encode(generated)
    emb2 = sbert.encode(reference)
    cosine_sim = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
    print("Generated:", generated)
    print("Reference:", reference)
    print("Cosine similarity:", cosine_sim)
    assert cosine_sim > 0.6  # Adjust threshold as needed


@pytest.mark.parametrize(
    "data,reference",
    [
        # Simple cases
        (
            {
                "description": "A ninja prowling the night.",
                "tags": ["ninja", "night", "sword"],
                "characters": [
                    {
                        "name": "Clementine",
                        "description": "A skilled ninja.",
                        "original_prompt": "prowls the night with her sword.",
                    }
                ],
            },
            "Clementine is a skilled ninja prowling the night with her sword. She moves like a shadow.",
        ),
        (
            {
                "description": "A cat sleeping on a sunny windowsill.",
                "tags": ["cat", "window", "sunny"],
                "characters": [],
            },
            "A cat enjoys the warmth of the sun on a windowsill.",
        ),
        (
            {
                "description": "A group of friends hiking in the mountains.",
                "tags": ["friends", "hiking", "mountains"],
                "characters": [
                    {
                        "name": "Alex",
                        "description": "An experienced hiker.",
                        "original_prompt": "leads the group.",
                    },
                    {
                        "name": "Jamie",
                        "description": "Loves nature.",
                        "original_prompt": "takes photos along the way.",
                    },
                ],
            },
            "Alex and Jamie hike with friends in the mountains, enjoying nature and taking photos.",
        ),
        # More difficult cases
        (
            {
                "description": "A bustling city street at night, neon lights reflecting off wet pavement, crowds moving in every direction.",
                "tags": ["city", "night", "neon", "crowd", "rain"],
                "characters": [
                    {
                        "name": "Morgan",
                        "description": "A street photographer",
                        "original_prompt": "captures candid moments in the chaos.",
                    },
                    {
                        "name": "Riley",
                        "description": "A lost tourist",
                        "original_prompt": "searches for a familiar landmark.",
                    },
                ],
            },
            "Morgan, a street photographer, and Riley, a lost tourist, navigate a neon-lit city street at night, surrounded by crowds and rain-slicked pavement.",
        ),
        (
            {
                "description": "A medieval banquet hall filled with nobles, jesters performing tricks, and servants carrying trays of food.",
                "tags": [
                    "banquet",
                    "medieval",
                    "nobles",
                    "jesters",
                    "servants",
                    "food",
                ],
                "characters": [
                    {
                        "name": "Sir Gareth",
                        "description": "A noble knight",
                        "original_prompt": "raises a toast to the king.",
                    },
                    {
                        "name": "Jester Pip",
                        "description": "A mischievous entertainer",
                        "original_prompt": "juggles flaming torches.",
                    },
                    {
                        "name": "Mira",
                        "description": "A diligent servant",
                        "original_prompt": "balances a tray of pastries.",
                    },
                ],
            },
            "Sir Gareth toasts the king while Jester Pip entertains the nobles and Mira serves pastries in a lively medieval banquet hall.",
        ),
        (
            {
                "description": "A spaceship cockpit with blinking controls, two pilots arguing over the best route through an asteroid field.",
                "tags": [
                    "spaceship",
                    "cockpit",
                    "pilots",
                    "asteroid field",
                    "argument",
                ],
                "characters": [
                    {
                        "name": "Captain Vega",
                        "description": "A seasoned spacefarer",
                        "original_prompt": "insists on the risky shortcut.",
                    },
                    {
                        "name": "Lieutenant Sato",
                        "description": "A cautious navigator",
                        "original_prompt": "prefers the safer path.",
                    },
                ],
            },
            "Captain Vega and Lieutenant Sato debate the safest route through an asteroid field in their spaceship cockpit, surrounded by blinking controls.",
        ),
        (
            {
                "description": "A quiet library where a child reads under a blanket fort, while an elderly librarian shelves ancient tomes.",
                "tags": ["library", "child", "blanket fort", "librarian", "books"],
                "characters": [
                    {
                        "name": "Evelyn",
                        "description": "A curious child",
                        "original_prompt": "reads adventure stories in her blanket fort.",
                    },
                    {
                        "name": "Mr. Finch",
                        "description": "An elderly librarian",
                        "original_prompt": "carefully shelves ancient tomes.",
                    },
                ],
            },
            "Evelyn reads adventure stories in her blanket fort while Mr. Finch shelves ancient tomes in a quiet library.",
        ),
    ],
)
def test_embedding_sentence_vs_naive_flatten(data, reference):
    sbert_device = "cpu" if getattr(PictureTagger, "FORCE_CPU", False) else None
    if sbert_device:
        sbert = SentenceTransformer("all-MiniLM-L6-v2", device=sbert_device)
    else:
        sbert = SentenceTransformer("all-MiniLM-L6-v2")

    # Sentence builder
    sent = PictureTagger.build_embedding_sentence(data)
    emb_sent = sbert.encode(sent)
    emb_ref = sbert.encode(reference)
    sim_sent = np.dot(emb_sent, emb_ref) / (
        np.linalg.norm(emb_sent) * np.linalg.norm(emb_ref)
    )

    # Naive flatten
    def naive_flatten(d):
        flat = []
        if d.get("description"):
            flat.append(d["description"])
        flat.extend(d.get("tags") or [])
        for char in d.get("characters") or []:
            for v in char.values():
                if v:
                    flat.append(str(v))
        return " ".join(flat)

    naive = naive_flatten(data)
    emb_naive = sbert.encode(naive)
    sim_naive = np.dot(emb_naive, emb_ref) / (
        np.linalg.norm(emb_naive) * np.linalg.norm(emb_ref)
    )

    print("Reference:", reference)
    print("Sentence builder:", sent)
    print("Naive flatten:", naive)
    print("Cosine similarity (sentence builder):", sim_sent)
    print("Cosine similarity (naive):", sim_naive)

    similarities_sent.append(sim_sent)
    similarities_naive.append(sim_naive)

    if not sim_sent > sim_naive:
        warnings.warn(
            f"Soft-failure: Sentence builder similarity ({sim_sent:.4f}) not greater than naive flattening ({sim_naive:.4f}) for reference: {reference}"
        )


@pytest.fixture(scope="session", autouse=True)
def print_similarity_stats(request):
    yield
    import numpy as np

    if similarities_sent and similarities_naive:
        print("\n==== Aggregated SBERT Similarity Results ====")
        print(
            f"Sentence builder: avg={np.mean(similarities_sent):.4f}, min={np.min(similarities_sent):.4f}, max={np.max(similarities_sent):.4f}"
        )
        print(
            f"Naive flatten:    avg={np.mean(similarities_naive):.4f}, min={np.min(similarities_naive):.4f}, max={np.max(similarities_naive):.4f}"
        )


def test_picture_tagger_on_directory():
    """
    Test PictureTagger on pictures/ directory.
    """
    img_path = os.path.join(os.path.dirname(__file__), "../pictures/TaggerTest.png")
    assert os.path.exists(img_path), f"Training directory not found: {img_path}"
    tagger = PictureTagger()
    tags = tagger.tag_images(image_paths=[img_path])
    print("Tags returned:", tags)


def test_brief_search_sentence_similarity():
    sbert_device = "cpu" if getattr(PictureTagger, "FORCE_CPU", False) else None
    if sbert_device:
        sbert = SentenceTransformer("all-MiniLM-L6-v2", device=sbert_device)
    else:
        sbert = SentenceTransformer("all-MiniLM-L6-v2")

    # Example 1: City street scene
    data = {
        "description": "A bustling city street at night, neon lights reflecting off wet pavement, crowds moving in every direction.",
        "tags": ["city", "night", "neon", "crowd", "rain"],
        "characters": [
            {
                "name": "Morgan",
                "description": "A street photographer",
                "original_prompt": "captures candid moments in the chaos.",
            },
            {
                "name": "Riley",
                "description": "A lost tourist",
                "original_prompt": "searches for a familiar landmark.",
            },
        ],
    }
    sent = PictureTagger.build_embedding_sentence(data)
    naive = " ".join(
        [data["description"]]
        + data["tags"]
        + [v for c in data["characters"] for v in c.values() if v]
    )
    search_sentences = [
        "city at night",
        "street photographer",
        "lost tourist",
        "neon rain",
    ]
    print("\n--- City street scene ---")
    for search in search_sentences:
        emb_search = sbert.encode(search)
        emb_sent = sbert.encode(sent)
        emb_naive = sbert.encode(naive)
        sim_sent = np.dot(emb_sent, emb_search) / (
            np.linalg.norm(emb_sent) * np.linalg.norm(emb_search)
        )
        sim_naive = np.dot(emb_naive, emb_search) / (
            np.linalg.norm(emb_naive) * np.linalg.norm(emb_search)
        )
        print(
            f"Search: '{search}' | Sentence builder: {sim_sent:.4f} | Naive: {sim_naive:.4f}"
        )

    # Example 2: Library scene
    data2 = {
        "description": "A quiet library where a child reads under a blanket fort, while an elderly librarian shelves ancient tomes.",
        "tags": ["library", "child", "blanket fort", "librarian", "books"],
        "characters": [
            {
                "name": "Evelyn",
                "description": "A curious child",
                "original_prompt": "reads adventure stories in her blanket fort.",
            },
            {
                "name": "Mr. Finch",
                "description": "An elderly librarian",
                "original_prompt": "carefully shelves ancient tomes.",
            },
        ],
    }
    sent2 = PictureTagger.build_embedding_sentence(data2)
    naive2 = " ".join(
        [data2["description"]]
        + data2["tags"]
        + [v for c in data2["characters"] for v in c.values() if v]
    )
    search_sentences2 = [
        "library books",
        "child reading",
        "blanket fort",
        "elderly librarian",
    ]
    print("\n--- Library scene ---")
    for search in search_sentences2:
        emb_search = sbert.encode(search)
        emb_sent2 = sbert.encode(sent2)
        emb_naive2 = sbert.encode(naive2)
        sim_sent2 = np.dot(emb_sent2, emb_search) / (
            np.linalg.norm(emb_sent2) * np.linalg.norm(emb_search)
        )
        sim_naive2 = np.dot(emb_naive2, emb_search) / (
            np.linalg.norm(emb_naive2) * np.linalg.norm(emb_search)
        )
        print(
            f"Search: '{search}' | Sentence builder: {sim_sent2:.4f} | Naive: {sim_naive2:.4f}"
        )
