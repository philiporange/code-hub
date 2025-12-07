"""Vector embeddings and similarity search using ChromaDB."""
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
import logging

import chromadb
from chromadb.config import Settings as ChromaSettings

from code_hub.config import settings

logger = logging.getLogger(__name__)

# Lazy load sentence-transformers to speed up imports
_embedding_model = None


def get_embedding_model():
    """Lazy-load the sentence transformer model."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {settings.embedding_model}")
        _embedding_model = SentenceTransformer(settings.embedding_model)
    return _embedding_model


class VectorStore:
    """Manages vector embeddings for semantic search."""

    def __init__(
        self,
        persist_directory: Path = None,
        embedding_model: str = None
    ):
        self.persist_directory = Path(persist_directory or settings.chroma_path)
        self.embedding_model_name = embedding_model or settings.embedding_model

        # Ensure directory exists
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        # Initialize ChromaDB with persistent storage
        self.client = chromadb.PersistentClient(
            path=str(self.persist_directory),
            settings=ChromaSettings(
                anonymized_telemetry=False,
                allow_reset=True
            )
        )

        # Get or create collections
        self.projects = self.client.get_or_create_collection(
            name="projects",
            metadata={"description": "Project descriptions and content"}
        )

        self.modules = self.client.get_or_create_collection(
            name="modules",
            metadata={"description": "Module descriptions"}
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for texts."""
        model = get_embedding_model()
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def add_project(
        self,
        project_id: str,
        name: str,
        short_description: str,
        long_description: str = "",
        keywords: List[str] = None,
        readme: str = ""
    ):
        """Add or update a project in the vector store."""
        # Combine text for embedding - weight important parts
        parts = [name]
        if short_description:
            parts.append(short_description)
        if long_description:
            parts.append(long_description)
        if keywords:
            parts.append(f"Keywords: {', '.join(keywords)}")
        # Include first part of readme for context
        if readme:
            readme_excerpt = readme[:1000]
            parts.append(readme_excerpt)

        combined_text = ". ".join(parts)

        # Generate embedding
        embedding = self.embed([combined_text])[0]

        # Prepare metadata (ChromaDB has limits on metadata size)
        metadata = {
            "name": name,
            "short_description": short_description[:500] if short_description else "",
            "keywords": ",".join(keywords[:20]) if keywords else ""
        }

        # Upsert to collection
        self.projects.upsert(
            ids=[project_id],
            embeddings=[embedding],
            metadatas=[metadata],
            documents=[combined_text[:5000]]  # Limit document size
        )

    def add_module(
        self,
        module_id: str,
        project_id: str,
        project_name: str,
        name: str,
        path: str,
        description: str
    ):
        """Add a module to the vector store."""
        combined_text = f"{project_name}: {name} - {description}"
        embedding = self.embed([combined_text])[0]

        self.modules.upsert(
            ids=[module_id],
            embeddings=[embedding],
            metadatas=[{
                "project_id": project_id,
                "project_name": project_name,
                "name": name,
                "path": path
            }],
            documents=[description[:2000]]
        )

    def search_projects(
        self,
        query: str,
        n_results: int = 10,
        keyword_filter: Optional[str] = None
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """
        Search for similar projects.

        Returns:
            List of (project_id, similarity_score, metadata) tuples
        """
        query_embedding = self.embed([query])[0]

        where = None
        if keyword_filter:
            where = {"keywords": {"$contains": keyword_filter.lower()}}

        try:
            results = self.projects.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where,
                include=["metadatas", "distances", "documents"]
            )
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []

        output = []
        if results['ids'] and results['ids'][0]:
            for i, proj_id in enumerate(results['ids'][0]):
                # Convert L2 distance to similarity score (0-1 range)
                distance = results['distances'][0][i] if results['distances'] else 0
                # L2 distance: lower is better, convert to similarity
                similarity = 1 / (1 + distance)
                metadata = results['metadatas'][0][i] if results['metadatas'] else {}
                output.append((proj_id, similarity, metadata))

        return output

    def search_modules(
        self,
        query: str,
        n_results: int = 20,
        project_id: Optional[str] = None
    ) -> List[Tuple[str, float, Dict[str, Any]]]:
        """Search for similar modules."""
        query_embedding = self.embed([query])[0]

        where = None
        if project_id:
            where = {"project_id": project_id}

        try:
            results = self.modules.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where,
                include=["metadatas", "distances"]
            )
        except Exception as e:
            logger.error(f"Module search error: {e}")
            return []

        output = []
        if results['ids'] and results['ids'][0]:
            for i, mod_id in enumerate(results['ids'][0]):
                distance = results['distances'][0][i] if results['distances'] else 0
                similarity = 1 / (1 + distance)
                metadata = results['metadatas'][0][i] if results['metadatas'] else {}
                output.append((mod_id, similarity, metadata))

        return output

    def delete_project(self, project_id: str):
        """Remove a project from the store."""
        try:
            self.projects.delete(ids=[project_id])
        except Exception as e:
            logger.warning(f"Error deleting project {project_id}: {e}")

    def delete_modules_for_project(self, project_id: str):
        """Remove all modules for a project."""
        try:
            # ChromaDB where clause for deletion
            self.modules.delete(where={"project_id": project_id})
        except Exception as e:
            logger.warning(f"Error deleting modules for project {project_id}: {e}")

    def clear(self):
        """Clear all data from the vector store."""
        try:
            self.client.delete_collection("projects")
            self.client.delete_collection("modules")
        except Exception:
            pass

        self.projects = self.client.get_or_create_collection(
            name="projects",
            metadata={"description": "Project descriptions and content"}
        )
        self.modules = self.client.get_or_create_collection(
            name="modules",
            metadata={"description": "Module descriptions"}
        )

    def get_stats(self) -> Dict[str, int]:
        """Get collection statistics."""
        return {
            "projects": self.projects.count(),
            "modules": self.modules.count()
        }


# Singleton instance
_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    """Get the vector store singleton."""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store


def reset_vector_store():
    """Reset the vector store singleton."""
    global _vector_store
    if _vector_store:
        _vector_store.clear()
    _vector_store = None
