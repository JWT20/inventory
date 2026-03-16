"""Tests for the matching service and embedding service.

These are pure unit tests — the Gemini API and pgvector DB calls are mocked.
"""

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# matching.find_best_match
# ---------------------------------------------------------------------------

def _mock_db_with_rows(rows, skus_by_id):
    """Helper to create a mock DB session matching the current matching service.

    The service calls db.execute().fetchall() for the vector query, then
    db.query(SKU).filter(...).all() to load full SKU objects.
    """
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = rows

    mock_query = MagicMock()
    mock_query.filter.return_value.all.return_value = list(skus_by_id.values())
    mock_db.query.return_value = mock_query

    return mock_db


class TestFindBestMatch:
    def test_match_above_threshold(self):
        from app.services.matching import find_best_match

        mock_sku = MagicMock()
        mock_sku.id = 1
        mock_sku.sku_code = "WINE-001"

        mock_db = _mock_db_with_rows([(1, 0.95, "/app/uploads/ref/1/img.jpg")], {1: mock_sku})

        with patch("app.services.matching.settings") as mock_settings:
            mock_settings.match_threshold = 0.92
            sku, confidence, image_path = find_best_match(mock_db, [0.1] * 1536)

        assert sku is mock_sku
        assert confidence == 0.95
        assert image_path == "/app/uploads/ref/1/img.jpg"

    def test_match_below_threshold(self):
        from app.services.matching import find_best_match

        mock_sku = MagicMock()
        mock_sku.id = 1

        mock_db = _mock_db_with_rows([(1, 0.80, "/app/uploads/ref/1/img.jpg")], {1: mock_sku})

        with patch("app.services.matching.settings") as mock_settings:
            mock_settings.match_threshold = 0.92
            sku, confidence, image_path = find_best_match(mock_db, [0.1] * 1536)

        assert sku is None
        assert confidence == 0.80
        assert image_path is None

    def test_no_reference_images(self):
        from app.services.matching import find_best_match

        mock_db = _mock_db_with_rows([], {})

        sku, confidence, image_path = find_best_match(mock_db, [0.1] * 1536)

        assert sku is None
        assert confidence == 0.0
        assert image_path is None

    def test_threshold_boundary_exact_matches(self):
        """Similarity exactly equal to threshold should match (uses strict < for rejection)."""
        from app.services.matching import find_best_match

        mock_sku = MagicMock()
        mock_sku.id = 1
        mock_sku.sku_code = "WINE-001"

        mock_db = _mock_db_with_rows([(1, 0.92, "/app/uploads/ref/1/img.jpg")], {1: mock_sku})

        with patch("app.services.matching.settings") as mock_settings:
            mock_settings.match_threshold = 0.92
            sku, confidence, image_path = find_best_match(mock_db, [0.1] * 1536)

        # 0.92 < 0.92 is False, so the match is NOT rejected
        assert sku is mock_sku
        assert confidence == 0.92
        assert image_path == "/app/uploads/ref/1/img.jpg"


# ---------------------------------------------------------------------------
# matching.find_best_matches
# ---------------------------------------------------------------------------

class TestFindBestMatches:
    def test_returns_top_n_candidates(self):
        from app.services.matching import find_best_matches

        mock_sku1 = MagicMock()
        mock_sku1.id = 1
        mock_sku2 = MagicMock()
        mock_sku2.id = 2

        mock_db = _mock_db_with_rows(
            [(1, 0.95, "/app/uploads/ref/1/a.jpg"), (2, 0.85, "/app/uploads/ref/2/b.jpg")],
            {1: mock_sku1, 2: mock_sku2},
        )

        results = find_best_matches(mock_db, [0.1] * 1536, top_n=2)

        assert len(results) == 2
        assert results[0] == (mock_sku1, 0.95, "/app/uploads/ref/1/a.jpg")
        assert results[1] == (mock_sku2, 0.85, "/app/uploads/ref/2/b.jpg")

    def test_empty_database(self):
        from app.services.matching import find_best_matches

        mock_db = _mock_db_with_rows([], {})

        results = find_best_matches(mock_db, [0.1] * 1536)
        assert results == []


# ---------------------------------------------------------------------------
# embedding.classify_image
# ---------------------------------------------------------------------------

class TestClassifyImage:
    def test_classifies_package(self):
        from app.services.embedding import classify_image

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"is_package": true, "summary": "wine box"}'
        mock_client.models.generate_content.return_value = mock_response

        with patch("app.services.embedding._get_client", return_value=mock_client), \
             patch("app.services.embedding.Image") as mock_pil:
            mock_img = MagicMock()
            mock_img.size = (800, 600)
            mock_pil.open.return_value = mock_img
            mock_pil.LANCZOS = 1
            is_package, summary = classify_image(b"fake-image-bytes")

        assert is_package is True
        assert summary == "wine box"
        mock_client.models.generate_content.assert_called_once()

    def test_classifies_non_package(self):
        from app.services.embedding import classify_image

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '{"is_package": false, "summary": "candles on table"}'
        mock_client.models.generate_content.return_value = mock_response

        with patch("app.services.embedding._get_client", return_value=mock_client), \
             patch("app.services.embedding.Image") as mock_pil:
            mock_img = MagicMock()
            mock_img.size = (800, 600)
            mock_pil.open.return_value = mock_img
            mock_pil.LANCZOS = 1
            is_package, summary = classify_image(b"fake-image-bytes")

        assert is_package is False
        assert summary == "candles on table"


# ---------------------------------------------------------------------------
# embedding.describe_package
# ---------------------------------------------------------------------------

class TestDescribePackage:
    def test_returns_description(self):
        from app.services.embedding import describe_package

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "ROBERT WEIL Junior Pinot Gris 2023. Dark blue box with white text."
        mock_client.models.generate_content.return_value = mock_response

        with patch("app.services.embedding._get_client", return_value=mock_client), \
             patch("app.services.embedding.Image") as mock_pil:
            mock_img = MagicMock()
            mock_img.size = (800, 600)
            mock_pil.open.return_value = mock_img
            mock_pil.LANCZOS = 1
            description = describe_package(b"fake-image-bytes")

        assert "ROBERT WEIL" in description
        mock_client.models.generate_content.assert_called_once()


# ---------------------------------------------------------------------------
# embedding.generate_embedding
# ---------------------------------------------------------------------------

class TestGenerateEmbedding:
    def test_returns_embedding_vector(self):
        from app.services.embedding import generate_embedding

        fake_embedding = [0.01] * 3072

        mock_client = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.values = fake_embedding
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]
        mock_client.models.embed_content.return_value = mock_result

        with patch("app.services.embedding._get_client", return_value=mock_client):
            result = generate_embedding("Château Margaux 2015")

        assert result == fake_embedding
        assert len(result) == 3072


# ---------------------------------------------------------------------------
# embedding.process_image  (full pipeline)
# ---------------------------------------------------------------------------

class TestProcessImage:
    def test_pipeline_single_call_then_embeds(self):
        from app.services.embedding import process_image

        with patch("app.services.embedding.classify_and_describe", return_value=(True, "A fine Bordeaux")) as mock_cd, \
             patch("app.services.embedding.generate_embedding", return_value=[0.5] * 3072) as mock_emb:
            description, embedding, is_package = process_image(b"image-data")

        assert description == "A fine Bordeaux"
        assert is_package is True
        assert len(embedding) == 3072
        mock_cd.assert_called_once_with(b"image-data")
        mock_emb.assert_called_once_with("A fine Bordeaux")

    def test_pipeline_skips_embed_for_non_package(self):
        from app.services.embedding import process_image

        with patch("app.services.embedding.classify_and_describe", return_value=(False, "digital clock")) as mock_cd, \
             patch("app.services.embedding.generate_embedding") as mock_emb:
            description, embedding, is_package = process_image(b"image-data")

        assert description == "digital clock"
        assert is_package is False
        assert embedding is None
        mock_cd.assert_called_once_with(b"image-data")
        mock_emb.assert_not_called()


# ---------------------------------------------------------------------------
# embedding.describe_and_embed (override path)
# ---------------------------------------------------------------------------

class TestDescribeAndEmbed:
    def test_skips_classification(self):
        from app.services.embedding import describe_and_embed

        with patch("app.services.embedding.classify_image") as mock_cls, \
             patch("app.services.embedding.describe_package", return_value="NORTHWAVE CORSAIR 2 shoe box, black with red accents") as mock_desc, \
             patch("app.services.embedding.generate_embedding", return_value=[0.5] * 3072) as mock_emb:
            description, embedding, quality = describe_and_embed(b"image-data")

        mock_cls.assert_not_called()
        mock_desc.assert_called_once_with(b"image-data")
        mock_emb.assert_called_once()
        assert "NORTHWAVE" in description
        assert len(embedding) == 3072
        assert quality in ("high", "medium", "low")
