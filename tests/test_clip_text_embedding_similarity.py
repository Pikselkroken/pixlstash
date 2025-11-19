import pytest
import numpy as np
from pixlvault.picture_tagger import PictureTagger
from sentence_transformers import SentenceTransformer, util


def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def dot_product(a, b):
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b)


def euclidean_distance(a, b):
    a = np.array(a)
    b = np.array(b)
    return np.linalg.norm(a - b)


def max_pooling_similarity(a, b):
    # Compare only the largest values in each embedding
    a = np.array(a)
    b = np.array(b)
    top_a = np.partition(a, -10)[-10:]
    top_b = np.partition(b, -10)[-10:]
    return np.dot(top_a, top_b) / (np.linalg.norm(top_a) * np.linalg.norm(top_b))


def partial_cosine_similarity(a, b, idx=None):
    # Compute cosine similarity on a subset of dimensions
    a = np.array(a)
    b = np.array(b)
    if idx is None:
        idx = np.arange(len(a))
    return np.dot(a[idx], b[idx]) / (np.linalg.norm(a[idx]) * np.linalg.norm(b[idx]))


descriptions = [
    "The image shows a young woman named Clementine in a kitchen. She is wearing a navy blue sleeveless dress and has blonde hair. The woman is smiling and holding a frying pan with a mixture of vegetables in it. The kitchen has a gas stove and a window in the background.",
    "The image shows a young woman named Clementine standing in a garden, holding a black assault rifle. She is wearing a black tank top and black shorts with a brown belt around her waist. She has shoulder-length blonde hair and is looking directly at the camera with a serious expression. The background is filled with greenery.",
    "A young woman named Clementine is sitting on a bench, reading a book.",
    "Clementine is running through a field of flowers, wearing a white dress.",
    "Clementine holding a black assault rifle.",
]


@pytest.mark.parametrize("query", ["Clementine holding a black assault rifle"])
def test_clip_text_embedding_similarity_measures(query):
    import gc

    tagger = PictureTagger(device="cpu")
    query_embedding = (
        tagger._clip_model.encode_text(
            tagger._clip_tokenizer([query]).to(tagger._clip_device)
        )
        .detach()
        .cpu()
        .numpy()[0]
    )

    print("\nCosine Similarity:")
    cosine_scores = []
    for desc in descriptions:
        emb = (
            tagger._clip_model.encode_text(
                tagger._clip_tokenizer([desc]).to(tagger._clip_device)
            )
            .detach()
            .cpu()
            .numpy()[0]
        )
        score = cosine_similarity(query_embedding, emb)
        cosine_scores.append((desc, score))
    for desc, score in sorted(cosine_scores, key=lambda x: -x[1]):
        print(f"Score: {score:.4f}\nDescription: {desc}\n---")
    best_cosine = max(cosine_scores, key=lambda x: x[1])[0]
    assert "assault rifle" in best_cosine.lower(), (
        "Most literal match should be ranked highest by cosine similarity."
    )

    print("\nDot Product:")
    dot_scores = []
    for desc in descriptions:
        emb = (
            tagger._clip_model.encode_text(
                tagger._clip_tokenizer([desc]).to(tagger._clip_device)
            )
            .detach()
            .cpu()
            .numpy()[0]
        )
        score = dot_product(query_embedding, emb)
        dot_scores.append((desc, score))
    for desc, score in sorted(dot_scores, key=lambda x: -x[1]):
        print(f"Score: {score:.4f}\nDescription: {desc}\n---")

    print("\nEuclidean Distance (lower is better):")
    euclid_scores = []
    for desc in descriptions:
        emb = (
            tagger._clip_model.encode_text(
                tagger._clip_tokenizer([desc]).to(tagger._clip_device)
            )
            .detach()
            .cpu()
            .numpy()[0]
        )
        score = euclidean_distance(query_embedding, emb)
        euclid_scores.append((desc, score))
    for desc, score in sorted(euclid_scores, key=lambda x: x[1]):
        print(f"Distance: {score:.4f}\nDescription: {desc}\n---")

    print("\nMax Pooling Cosine Similarity (top 10 dims):")
    maxpool_scores = []
    for desc in descriptions:
        emb = (
            tagger._clip_model.encode_text(
                tagger._clip_tokenizer([desc]).to(tagger._clip_device)
            )
            .detach()
            .cpu()
            .numpy()[0]
        )
        score = max_pooling_similarity(query_embedding, emb)
        maxpool_scores.append((desc, score))
    for desc, score in sorted(maxpool_scores, key=lambda x: -x[1]):
        print(f"Score: {score:.4f}\nDescription: {desc}\n---")

    print("\nPartial Cosine Similarity (first 32 dims):")
    partial_scores = []
    for desc in descriptions:
        emb = (
            tagger._clip_model.encode_text(
                tagger._clip_tokenizer([desc]).to(tagger._clip_device)
            )
            .detach()
            .cpu()
            .numpy()[0]
        )
        score = partial_cosine_similarity(query_embedding, emb, idx=np.arange(32))
        partial_scores.append((desc, score))
    for desc, score in sorted(partial_scores, key=lambda x: -x[1]):
        print(f"Score: {score:.4f}\nDescription: {desc}\n---")

    del tagger, query_embedding, emb
    gc.collect()


@pytest.mark.parametrize("query", ["Clementine holding a black assault rifle"])
def test_sbert_text_similarity(query):
    import gc

    model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    # Use numpy arrays to reduce RAM
    desc_embeddings = model.encode(descriptions, convert_to_numpy=True)
    query_embedding = model.encode(query, convert_to_numpy=True)

    print("\nSBERT Cosine Similarity:")
    sbert_scores = []
    for desc, emb in zip(descriptions, desc_embeddings):
        score = util.cos_sim(query_embedding, emb).item()
        sbert_scores.append((desc, score))
    for desc, score in sorted(sbert_scores, key=lambda x: -x[1]):
        print(f"Score: {score:.4f}\nDescription: {desc}\n---")
    best_desc = max(sbert_scores, key=lambda x: x[1])[0]
    assert "assault rifle" in best_desc.lower(), (
        "Most literal match should be ranked highest by SBERT similarity."
    )

    del model, desc_embeddings, query_embedding, emb
    gc.collect()
