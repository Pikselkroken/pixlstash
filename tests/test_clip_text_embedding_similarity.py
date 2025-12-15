import shutil
import pytest

import numpy as np
from sqlmodel import Session
import sqlite3
import tempfile
import os

from sentence_transformers import SentenceTransformer, util
from pixlvault.db_models.picture import Picture
from pixlvault.db_models.tag import Tag
from pixlvault.picture_tagger import PictureTagger
from pixlvault.server import Server


def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return np.dot(a, b) / (norm_a * norm_b)


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


# Shared fixture for PictureTagger to avoid repeated model loading
@pytest.fixture(scope="module")
def shared_tagger():
    tagger = PictureTagger("cuda" if not PictureTagger.FORCE_CPU else "cpu")
    yield tagger
    del tagger


@pytest.mark.parametrize("query", ["Clementine holding a black assault rifle"])
def test_clip_text_embedding_similarity_measures(query, shared_tagger):
    import gc

    tagger = shared_tagger
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

    del query_embedding, emb
    gc.collect()


@pytest.fixture(scope="module")
def test_model():
    import gc

    model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    yield model

    del model
    gc.collect()


@pytest.mark.parametrize("query", ["Clementine holding a black assault rifle"])
def test_sbert_text_similarity(test_model, query):
    import gc

    model = test_model

    # Warm up
    model.encode(["Test sentence"], convert_to_numpy=True)

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


@pytest.mark.parametrize(
    "a, b",
    [
        (
            np.random.randn(384).astype(np.float32),
            np.random.randn(384).astype(np.float32),
        ),
        (np.ones(384, dtype=np.float32), np.ones(384, dtype=np.float32)),
        (np.zeros(384, dtype=np.float32), np.ones(384, dtype=np.float32)),
    ],
)
def test_sqlite_cosine_similarity_matches_numpy(a, b):
    # Reference value
    expected = cosine_similarity(a, b)
    # Setup in-memory SQLite DB and register function
    conn = sqlite3.connect(":memory:")
    import pixlvault.picture_utils as pu

    conn.create_function("cosine_similarity", 2, pu.PictureUtils.cosine_similarity)
    # Store as bytes
    a_bytes = a.tobytes()
    b_bytes = b.tobytes()
    # Query
    result = conn.execute(
        "SELECT cosine_similarity(?, ?)", (a_bytes, b_bytes)
    ).fetchone()[0]
    print(f"Numpy: {expected}, SQLite: {result}")
    # Accept small floating point error, and accept 0.0 if expected is nan
    if np.isnan(expected):
        assert result == 0.0
    else:
        assert np.isclose(result, expected, atol=1e-5)
    conn.close()


def test_server_sqlite_cosine_similarity_matches_numpy():
    from sqlmodel import Session
    from sqlalchemy import text

    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")

        # This triggers _import_default_data
        with Server(config_path, server_config_path) as server:
            engine = server.vault.db._engine
            # Two known embeddings
            a = np.random.randn(384).astype(np.float32)
            b = np.random.randn(384).astype(np.float32)
            expected = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
            a_bytes = a.tobytes()
            b_bytes = b.tobytes()
            # Query using SQLModel/SQLAlchemy session and text().bindparams()
            with Session(engine) as session:
                stmt = text("SELECT cosine_similarity(:a, :b)").bindparams(
                    a=a_bytes, b=b_bytes
                )
                result = session.exec(stmt).first()
            print(f"Numpy: {expected}, SQLite: {result}")
            if np.isnan(expected):
                assert result == 0.0
            else:
                assert np.isclose(result, expected, atol=1e-5)


def test_embedding_storage_and_retrieval():
    from sqlmodel import SQLModel, Field, Session
    from sqlalchemy import text
    import numpy as np
    import tempfile
    import os
    from sqlalchemy import create_engine

    class EmbeddingTest(SQLModel, table=True):
        id: int = Field(default=None, primary_key=True)
        embedding: bytes

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        engine = create_engine(f"sqlite:///{db_path}", echo=False)
        SQLModel.metadata.create_all(engine)
        # Create and store embedding
        arr = np.random.randn(384).astype(np.float32)
        arr_bytes = arr.tobytes()
        with Session(engine) as session:
            obj = EmbeddingTest(embedding=arr_bytes)
            session.add(obj)
            session.commit()
            # Retrieve
            loaded = session.exec(
                text("SELECT embedding FROM embeddingtest WHERE id=1")
            ).first()
            arr_loaded = np.frombuffer(loaded[0], dtype=np.float32)
            print(f"Original shape: {arr.shape}, Loaded shape: {arr_loaded.shape}")
            print(f"First 5 values original: {arr[:5]}")
            print(f"First 5 values loaded: {arr_loaded[:5]}")
            assert arr_loaded.shape == arr.shape
            assert np.allclose(arr_loaded, arr)


def test_picture_embedding_storage_and_retrieval():
    from sqlmodel import Session
    from pixlvault.db_models.picture import Picture
    import numpy as np
    import tempfile
    import os
    from sqlalchemy import create_engine

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, "test.db")
        engine = create_engine(f"sqlite:///{db_path}", echo=False)
        # Create tables
        from sqlmodel import SQLModel

        SQLModel.metadata.create_all(engine)
        # Create and store embedding
        arr = np.random.randn(384).astype(np.float32)
        with Session(engine) as session:
            # Always provide a valid string id
            pic = Picture(id=0, description="Test", text_embedding=arr)
            assert pic.id == 0, f"Picture.id should be a non-None integer, got {pic.id}"
            session.add(pic)
            session.commit()
            session.refresh(pic)
            # Retrieve using Picture.find
            found = Picture.find(
                session, select_fields=["id", "text_embedding"], id=pic.id
            )
            assert found, f"Picture.find did not return any results for id={pic.id}"
            loaded_pic = found[0]
            arr_loaded = np.frombuffer(
                loaded_pic.text_embedding, dtype=np.float32
            ).reshape(arr.shape)
            print(f"Original shape: {arr.shape}, Loaded shape: {arr_loaded.shape}")
            print(f"First 5 values original: {arr[:5]}")
            print(f"First 5 values loaded: {arr_loaded[:5]}")
            assert arr_loaded.shape == arr.shape
            assert np.allclose(arr_loaded, arr)


@pytest.fixture
def test_server():
    # Force CPU for all models during test
    tmpdir = tempfile.mkdtemp()
    config_path = os.path.join(tmpdir, "config.json")
    server_config_path = os.path.join(tmpdir, "server_config.json")
    server = Server(config_path, server_config_path)
    yield server
    shutil.rmtree(tmpdir)


def test_picture_semantic_search_returns_relevant_result(test_server):
    # Dummy embedding function: returns fixed vectors for known descriptions
    def dummy_text_to_embedding(text):
        if "assault rifle" in text:
            return np.ones(384, dtype=np.float32)
        elif "reading a book" in text:
            return np.full(384, 2.0, dtype=np.float32)
        else:
            return np.zeros(384, dtype=np.float32)

    server = test_server

    engine = server.vault.db._engine
    with Session(engine) as session:
        # Create pictures with known descriptions and embeddings
        pic1 = Picture(
            id=0,
            description="Clementine holding a black assault rifle",
            text_embedding=dummy_text_to_embedding("assault rifle"),
        )
        pic2 = Picture(
            id=1,
            description="A young woman named Clementine is sitting on a bench, reading a book.",
            text_embedding=dummy_text_to_embedding("reading a book"),
        )
        pic3 = Picture(
            id=2,
            description="Clementine is running through a field of flowers, wearing a white dress.",
            text_embedding=dummy_text_to_embedding("other"),
        )
        session.add(pic1)
        session.add(pic2)
        session.add(pic3)
        session.commit()

        # Query for "Clementine holding a black assault rifle"
        results = Picture.semantic_search(
            session,
            query="Clementine holding a black assault rifle",
            query_words=["Clementine", "holding", "black", "assault", "rifle"],
            text_to_embedding=dummy_text_to_embedding,
            fuzzy_weight=0.0,  # Only embedding similarity
            embedding_weight=1.0,
            threshold=0.0,
            limit=3,
        )
        assert results, "semantic_search returned no results"
        top_result, score = results[0]
        print(f"Top result description: {top_result.description} with score {score}")
        assert "assault rifle" in top_result.description.lower(), (
            "Top result should be the most relevant by embedding similarity."
        )


@pytest.mark.parametrize(
    "fuzzy_weight,embedding_weight,threshold,expected_top_desc",
    [
        (1.0, 0.0, 0.0, "assault rifle"),  # Fuzzy only
        (0.0, 1.0, 0.0, "assault rifle"),  # Embedding only
        (0.5, 0.5, 0.0, "assault rifle"),  # Both
        (1.0, 0.0, 0.8, "assault rifle"),  # Fuzzy, high threshold
        (0.0, 1.0, 0.8, "assault rifle"),  # Embedding, high threshold
        (1.0, 0.0, 1.1, None),  # Fuzzy, threshold too high
        (0.0, 0.0, 0.1, None),  # All weights zero
    ],
)
def test_picture_semantic_search_with_tags_and_weights(
    test_server, fuzzy_weight, embedding_weight, threshold, expected_top_desc
):
    def dummy_text_to_embedding(text):
        if "assault rifle" in text:
            return np.ones(384, dtype=np.float32)
        elif "reading a book" in text:
            return np.full(384, 2.0, dtype=np.float32)
        else:
            return np.zeros(384, dtype=np.float32)

    server = test_server
    engine = server.vault.db._engine
    with Session(engine) as session:
        pic1 = Picture(
            id=0,
            description="Clementine holding a black assault rifle",
            text_embedding=dummy_text_to_embedding("assault rifle"),
        )
        pic2 = Picture(
            id=1,
            description="A young woman named Clementine is sitting on a bench, reading a book.",
            text_embedding=dummy_text_to_embedding("reading a book"),
        )
        pic3 = Picture(
            id=2,
            description="Clementine is running through a field of flowers, wearing a white dress.",
            text_embedding=dummy_text_to_embedding("other"),
        )
        session.add(pic1)
        session.add(pic2)
        session.add(pic3)
        session.commit()

        tag1 = Tag(picture_id=pic1.id, tag="assault rifle")
        tag2 = Tag(picture_id=pic2.id, tag="book")
        tag3 = Tag(picture_id=pic3.id, tag="flowers")
        session.add(tag1)
        session.add(tag2)
        session.add(tag3)
        session.commit()

        results = Picture.semantic_search(
            session,
            query="assault rifle",
            query_words=["assault", "rifle"],
            text_to_embedding=dummy_text_to_embedding,
            fuzzy_weight=fuzzy_weight,
            embedding_weight=embedding_weight,
            threshold=threshold,
            limit=3,
        )
        if expected_top_desc is None:
            assert not results, f"Expected no results, but got {len(results)}"
        else:
            assert results, (
                f"semantic_search returned no results for weights=({fuzzy_weight},{embedding_weight}), threshold={threshold}"
            )
            top_result, score = results[0]
            print(
                f"Top result description: {top_result.description} with score {score}"
            )
            assert expected_top_desc in top_result.description.lower(), (
                f"Top result should match '{expected_top_desc}' for weights=({fuzzy_weight},{embedding_weight}), threshold={threshold}"
            )


@pytest.mark.parametrize(
    "fuzzy_weight,embedding_weight,threshold,expected_top_desc",
    [
        (1.0, 0.0, 0.3, "assault rifle"),  # Fuzzy only
        (0.0, 1.0, 0.3, None),  # Embedding only
        (0.5, 0.5, 0.4, "assault rifle"),  # Both with low threshold
        (0.5, 0.5, 0.6, None),  # Both with high threshold
    ],
)
def test_picture_semantic_search_without_embeddings(
    test_server, fuzzy_weight, embedding_weight, threshold, expected_top_desc
):
    def dummy_text_to_embedding(text):
        if "assault rifle" in text:
            return np.ones(384, dtype=np.float32)
        elif "reading a book" in text:
            return np.full(384, 2.0, dtype=np.float32)
        else:
            return np.zeros(384, dtype=np.float32)

    server = test_server
    engine = server.vault.db._engine
    with Session(engine) as session:
        pic1 = Picture(
            id=0,
            description="Clementine holding a black assault rifle",
        )
        pic2 = Picture(
            id=1,
            description="A young woman named Clementine is sitting on a bench, reading a book.",
        )
        pic3 = Picture(
            id=2,
            description="Clementine is running through a field of flowers, wearing a white dress.",
        )
        session.add(pic1)
        session.add(pic2)
        session.add(pic3)
        session.commit()

        tag1 = Tag(picture_id=pic1.id, tag="assault rifle")
        tag2 = Tag(picture_id=pic2.id, tag="book")
        tag3 = Tag(picture_id=pic3.id, tag="flowers")
        session.add(tag1)
        session.add(tag2)
        session.add(tag3)
        session.commit()

        results = Picture.semantic_search(
            session,
            query="assault rifle",
            query_words=["assault", "rifle"],
            text_to_embedding=dummy_text_to_embedding,
            fuzzy_weight=fuzzy_weight,
            embedding_weight=embedding_weight,
            threshold=threshold,
            limit=3,
        )
        if expected_top_desc is None:
            assert not results, f"Expected no results, but got {len(results)}"
        else:
            assert results, (
                f"semantic_search returned no results for weights=({fuzzy_weight},{embedding_weight}), threshold={threshold}"
            )
            top_result, score = results[0]
            print(
                f"Top result description: {top_result.description} with score {score}"
            )
            assert expected_top_desc in top_result.description.lower(), (
                f"Top result should match '{expected_top_desc}' for weights=({fuzzy_weight},{embedding_weight}), threshold={threshold}"
            )


@pytest.mark.parametrize(
    "fuzzy_weight,embedding_weight,threshold,expected_top_desc",
    [
        (1.0, 0.0, 0.3, None),  # Fuzzy only
        (0.0, 1.0, 0.3, "assault rifle"),  # Embedding only
        (0.5, 0.5, 0.3, "assault rifle"),  # Both with low threshold
        (0.5, 0.5, 0.6, None),  # Both with high threshold
    ],
)
def test_picture_semantic_search_without_tags(
    test_server, fuzzy_weight, embedding_weight, threshold, expected_top_desc
):
    def dummy_text_to_embedding(text):
        if "assault rifle" in text:
            return np.ones(384, dtype=np.float32)
        elif "reading a book" in text:
            return np.full(384, 2.0, dtype=np.float32)
        else:
            return np.zeros(384, dtype=np.float32)

    server = test_server
    engine = server.vault.db._engine
    with Session(engine) as session:
        pic1 = Picture(
            id=0,
            description="Clementine holding a black assault rifle",
            text_embedding=dummy_text_to_embedding("assault rifle"),
        )
        pic2 = Picture(
            id=1,
            description="A young woman named Clementine is sitting on a bench, reading a book.",
            text_embedding=dummy_text_to_embedding("reading a book"),
        )
        pic3 = Picture(
            id=2,
            description="Clementine is running through a field of flowers, wearing a white dress.",
            text_embedding=dummy_text_to_embedding("other"),
        )
        session.add(pic1)
        session.add(pic2)
        session.add(pic3)
        session.commit()

        preprocessed_query_words = ["assault", "rifle"]

        results = Picture.semantic_search(
            session,
            query="assault rifle",
            query_words=preprocessed_query_words,
            text_to_embedding=dummy_text_to_embedding,
            fuzzy_weight=fuzzy_weight,
            embedding_weight=embedding_weight,
            threshold=threshold,
            limit=3,
        )
        if expected_top_desc is None:
            assert not results, f"Expected no results, but got {len(results)}"
        else:
            assert results, (
                f"semantic_search returned no results for weights=({fuzzy_weight},{embedding_weight}), threshold={threshold}"
            )
            top_result, score = results[0]
            print(
                f"Top result description: {top_result.description} with score {score}"
            )
            assert expected_top_desc in top_result.description.lower(), (
                f"Top result should match '{expected_top_desc}' for weights=({fuzzy_weight},{embedding_weight}), threshold={threshold}"
            )
