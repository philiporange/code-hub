"""Full-text and vector indexing for projects."""
import logging
from datetime import datetime
from typing import Optional, Callable, List, Tuple

from peewee import fn

from code_hub.models import (
    Project, Module, ProjectFTS, ProjectKeyword, Keyword, db
)
from code_hub.vectorstore import get_vector_store, VectorStore

logger = logging.getLogger(__name__)


class Indexer:
    """Builds and maintains search indexes."""

    def __init__(self, vector_store: Optional[VectorStore] = None):
        self.vector_store = vector_store

    def _get_vector_store(self) -> VectorStore:
        """Lazy-load vector store."""
        if self.vector_store is None:
            self.vector_store = get_vector_store()
        return self.vector_store

    def index_project(self, project: Project):
        """Index a single project for search."""
        # Get keywords for this project
        keywords = [pk.keyword.name for pk in project.project_keywords]
        keywords_text = " ".join(keywords)

        # Update FTS index
        try:
            # Delete existing FTS entry if exists
            ProjectFTS.delete().where(ProjectFTS.rowid == project.id).execute()

            # Insert new FTS entry
            ProjectFTS.insert({
                ProjectFTS.rowid: project.id,
                ProjectFTS.name: project.name,
                ProjectFTS.short_description: project.short_description or "",
                ProjectFTS.long_description: project.long_description or "",
                ProjectFTS.readme_content: project.readme_content or "",
                ProjectFTS.keywords: keywords_text
            }).execute()
        except Exception as e:
            logger.warning(f"FTS indexing error for {project.name}: {e}")

        # Vector index
        try:
            vs = self._get_vector_store()
            vs.add_project(
                project_id=str(project.id),
                name=project.name,
                short_description=project.short_description or "",
                long_description=project.long_description or "",
                keywords=keywords,
                readme=project.readme_content or ""
            )

            # Index modules
            for module in project.modules:
                vs.add_module(
                    module_id=f"{project.id}_{module.id}",
                    project_id=str(project.id),
                    project_name=project.name,
                    name=module.name,
                    path=module.path,
                    description=module.description or ""
                )
        except Exception as e:
            logger.warning(f"Vector indexing error for {project.name}: {e}")

        # Mark as indexed
        project.indexed_at = datetime.now()
        project.save()

        logger.debug(f"Indexed project: {project.name}")

    def index_all(
        self,
        rebuild: bool = False,
        on_progress: Optional[Callable[[str, int, int], None]] = None
    ):
        """Index all projects."""
        if rebuild:
            logger.info("Rebuilding all indexes...")
            try:
                self._get_vector_store().clear()
            except Exception as e:
                logger.warning(f"Error clearing vector store: {e}")

            # Clear FTS table
            try:
                ProjectFTS.delete().execute()
            except Exception:
                pass

            # Clear indexed_at timestamps
            Project.update(indexed_at=None).execute()

        # Get projects needing indexing
        query = Project.select()
        if not rebuild:
            query = query.where(
                (Project.indexed_at.is_null()) |
                (Project.indexed_at < Project.updated_at)
            )

        projects = list(query)
        total = len(projects)

        if total == 0:
            logger.info("No projects need indexing")
            return

        logger.info(f"Indexing {total} projects...")

        for i, project in enumerate(projects):
            if on_progress:
                on_progress(f"Indexing {project.name}", i + 1, total)

            try:
                self.index_project(project)
            except Exception as e:
                logger.error(f"Error indexing {project.name}: {e}")

        logger.info(f"Indexing complete: {total} projects")

    def search_fts(
        self,
        query: str,
        limit: int = 20
    ) -> List[Project]:
        """Perform full-text search."""
        try:
            # Use FTS5 MATCH syntax
            # Escape special FTS5 characters
            safe_query = query.replace('"', '""')

            results = (
                Project
                .select()
                .join(ProjectFTS, on=(Project.id == ProjectFTS.rowid))
                .where(ProjectFTS.match(f'"{safe_query}"'))
                .order_by(ProjectFTS.bm25())
                .limit(limit)
            )
            return list(results)
        except Exception as e:
            logger.warning(f"FTS search error: {e}")
            # Fallback to LIKE search
            return list(
                Project
                .select()
                .where(
                    (Project.name.contains(query)) |
                    (Project.short_description.contains(query))
                )
                .limit(limit)
            )

    def search_semantic(
        self,
        query: str,
        limit: int = 10
    ) -> List[Tuple[Project, float]]:
        """Perform semantic similarity search."""
        vs = self._get_vector_store()
        results = vs.search_projects(query, n_results=limit)

        if not results:
            return []

        # Fetch full project objects
        project_ids = [int(r[0]) for r in results]
        projects = {p.id: p for p in Project.select().where(Project.id.in_(project_ids))}

        output = []
        for proj_id, score, metadata in results:
            proj = projects.get(int(proj_id))
            if proj:
                output.append((proj, score))

        return output

    def search_hybrid(
        self,
        query: str,
        limit: int = 20,
        fts_weight: float = 0.3,
        semantic_weight: float = 0.7
    ) -> List[Tuple[Project, float]]:
        """Combined FTS and semantic search with weighted scores."""
        # Get FTS results
        fts_results = self.search_fts(query, limit=limit * 2)
        fts_scores = {}
        for i, p in enumerate(fts_results):
            # Higher rank = higher score (inverse of position)
            fts_scores[p.id] = 1.0 - (i / max(len(fts_results), 1))

        # Get semantic results
        semantic_results = self.search_semantic(query, limit=limit * 2)
        semantic_scores = {p.id: score for p, score in semantic_results}

        # Combine scores
        all_ids = set(fts_scores.keys()) | set(semantic_scores.keys())
        combined = []

        for pid in all_ids:
            fts_score = fts_scores.get(pid, 0)
            sem_score = semantic_scores.get(pid, 0)
            combined_score = (fts_weight * fts_score) + (semantic_weight * sem_score)
            combined.append((pid, combined_score))

        # Sort by combined score (descending)
        combined.sort(key=lambda x: -x[1])

        # Fetch projects
        top_ids = [pid for pid, _ in combined[:limit]]
        if not top_ids:
            return []

        projects = {p.id: p for p in Project.select().where(Project.id.in_(top_ids))}

        return [(projects[pid], score) for pid, score in combined[:limit] if pid in projects]

    def search_modules(
        self,
        query: str,
        limit: int = 20,
        project_name: Optional[str] = None
    ) -> List[Tuple[Module, float]]:
        """Search for modules by description."""
        vs = self._get_vector_store()

        project_id = None
        if project_name:
            try:
                project = Project.get(Project.name == project_name)
                project_id = str(project.id)
            except Project.DoesNotExist:
                pass

        results = vs.search_modules(query, n_results=limit, project_id=project_id)

        if not results:
            return []

        # Parse module IDs and fetch
        output = []
        for mod_id, score, metadata in results:
            try:
                # module_id format: "{project_id}_{module_id}"
                parts = mod_id.split("_")
                if len(parts) >= 2:
                    actual_mod_id = int(parts[1])
                    module = Module.get_by_id(actual_mod_id)
                    output.append((module, score))
            except Exception:
                continue

        return output


def get_indexer() -> Indexer:
    """Get an indexer instance."""
    return Indexer()
