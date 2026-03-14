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

        mock_db = _mock_db_with_rows([(1, 0.95)], {1: mock_sku})

        with patch("app.services.matching.settings") as mock_settings:
            mock_settings.match_threshold = 0.92
            sku, confidence = find_best_match(mock_db, [0.1] * 1536)

        assert sku is mock_sku
        assert confidence == 0.95

    def test_match_below_threshold(self):
        from app.services.matching import find_best_match

        mock_sku = MagicMock()
        mock_sku.id = 1

        mock_db = _mock_db_with_rows([(1, 0.80)], {1: mock_sku})

        with patch("app.services.matching.settings") as mock_settings:
            mock_settings.match_threshold = 0.92
            sku, confidence = find_best_match(mock_db, [0.1] * 1536)

        assert sku is None
        assert confidence == 0.80

    def test_no_reference_images(self):
        from app.services.matching import find_best_match

        mock_db = _mock_db_with_rows([], {})

        sku, confidence = find_best_match(mock_db, [0.1] * 1536)

        assert sku is None
        assert confidence == 0.0

    def test_threshold_boundary_exact_matches(self):
        """Similarity exactly equal to threshold should match (uses strict < for rejection)."""
        from app.services.matching import find_best_match

        mock_sku = MagicMock()
        mock_sku.id = 1
        mock_sku.sku_code = "WINE-001"

        mock_db = _mock_db_with_rows([(1, 0.92)], {1: mock_sku})

        with patch("app.services.matching.settings") as mock_settings:
            mock_settings.match_threshold = 0.92
            sku, confidence = find_best_match(mock_db, [0.1] * 1536)

        # 0.92 < 0.92 is False, so the match is NOT rejected
        assert sku is mock_sku
        assert confidence == 0.92


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
            [(1, 0.95), (2, 0.85)], {1: mock_sku1, 2: mock_sku2}
        )

        results = find_best_matches(mock_db, [0.1] * 1536, top_n=2)

        assert len(results) == 2
        assert results[0] == (mock_sku1, 0.95)
        assert results[1] == (mock_sku2, 0.85)

    def test_empty_database(self):
        from app.services.matching import find_best_matches

        mock_db = _mock_db_with_rows([], {})

        results = find_best_matches(mock_db, [0.1] * 1536)
        assert results == []


# ---------------------------------------------------------------------------
# embedding.describe_image
# ---------------------------------------------------------------------------

class TestDescribeImage:
    def test_returns_vision_description(self):
        from app.services.embedding import describe_image

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Château Margaux 2015 Bordeaux"
        mock_client.models.generate_content.return_value = mock_response

        with patch("app.services.embedding._get_client", return_value=mock_client), \
             patch("app.services.embedding.Image") as mock_pil:
            mock_img = MagicMock()
            mock_img.size = (800, 600)
            mock_pil.open.return_value = mock_img
            mock_pil.LANCZOS = 1
            result = describe_image(b"fake-image-bytes")

        assert result == "Château Margaux 2015 Bordeaux"
        mock_client.models.generate_content.assert_called_once()

    def test_sends_image_to_model(self):
        from app.services.embedding import describe_image

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "description"
        mock_client.models.generate_content.return_value = mock_response
        fake_image = MagicMock()
        fake_image.size = (800, 600)

        with patch("app.services.embedding._get_client", return_value=mock_client), \
             patch("app.services.embedding.Image") as mock_pil:
            mock_pil.open.return_value = fake_image
            mock_pil.LANCZOS = 1
            describe_image(b"test")

        # generate_content should receive contents list with prompt and image
        call_kwargs = mock_client.models.generate_content.call_args[1]
        contents = call_kwargs["contents"]
        assert isinstance(contents, list)
        assert len(contents) == 2
        assert contents[1] is fake_image


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
    def test_pipeline_chains_describe_then_embed(self):
        from app.services.embedding import process_image

        with patch("app.services.embedding.describe_image", return_value="A fine Bordeaux") as mock_desc, \
             patch("app.services.embedding.generate_embedding", return_value=[0.5] * 3072) as mock_emb:
            description, embedding = process_image(b"image-data")

        assert description == "A fine Bordeaux"
        assert len(embedding) == 3072
        mock_desc.assert_called_once_with(b"image-data")
        mock_emb.assert_called_once_with("A fine Bordeaux")
