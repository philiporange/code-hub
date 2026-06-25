"""Tests for search fallback behavior."""
from types import SimpleNamespace


def test_semantic_search_returns_empty_when_vector_store_fails(monkeypatch):
    """Semantic search should fail closed when embeddings are unavailable."""
    from code_hub.indexer import Indexer

    indexer = Indexer()

    def raise_vector_store_error():
        raise RuntimeError("embedding stack unavailable")

    monkeypatch.setattr(indexer, "_get_vector_store", raise_vector_store_error)

    assert indexer.search_semantic("anything") == []


def test_module_search_returns_empty_when_vector_store_fails(monkeypatch):
    """Module search should fail closed when embeddings are unavailable."""
    from code_hub.indexer import Indexer

    indexer = Indexer()

    def raise_vector_store_error():
        raise RuntimeError("embedding stack unavailable")

    monkeypatch.setattr(indexer, "_get_vector_store", raise_vector_store_error)

    assert indexer.search_modules("anything") == []


def test_hybrid_search_keeps_fts_results_when_semantic_fails(monkeypatch):
    """Hybrid search should still return FTS matches if semantic search fails."""
    import code_hub.indexer as indexer_module
    from code_hub.indexer import Indexer

    project = SimpleNamespace(id=1, name="alpha")
    indexer = Indexer()

    monkeypatch.setattr(indexer, "search_fts", lambda query, limit: [project])

    def raise_semantic_error(query, limit):
        raise RuntimeError("embedding stack unavailable")

    monkeypatch.setattr(indexer, "search_semantic", raise_semantic_error)

    class FakeProjectId:
        def in_(self, ids):
            return ids

    class FakeProjectQuery:
        def where(self, condition):
            return [project]

    class FakeProjectModel:
        id = FakeProjectId()

        @staticmethod
        def select():
            return FakeProjectQuery()

    monkeypatch.setattr(indexer_module, "Project", FakeProjectModel)

    assert indexer.search_hybrid("alpha", limit=5) == [(project, 0.3)]
