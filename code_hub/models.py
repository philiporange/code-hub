"""Peewee ORM models for Code Hub.

Defines database models for projects, modules, files, keywords, dependencies,
vector embeddings, LOC history tracking, and scan logs. Uses SQLite with FTS5
for full-text search capabilities.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from peewee import (
    SqliteDatabase, Model, CharField, TextField,
    BooleanField, IntegerField, DateTimeField,
    ForeignKeyField, BlobField, FloatField
)
from playhouse.sqlite_ext import FTS5Model, SearchField, RowIDField

from code_hub.config import settings

# Initialize database with WAL mode for better concurrency
db = SqliteDatabase(
    str(settings.database_path),
    pragmas={
        'journal_mode': 'wal',
        'cache_size': -64 * 1000,  # 64MB cache
        'foreign_keys': 1,
        'synchronous': 'normal'
    }
)


class BaseModel(Model):
    """Base model with database binding."""
    class Meta:
        database = db


class Project(BaseModel):
    """A code project in ~/Code."""

    # Identity
    name = CharField(unique=True, index=True)
    path = CharField(unique=True)

    # Descriptions
    short_description = TextField(default="")
    long_description = TextField(default="")

    # Classification
    primary_language = CharField(null=True, index=True)
    languages = TextField(default="[]")  # JSON array
    frameworks = TextField(default="[]")  # JSON array

    # Git info
    is_git_repo = BooleanField(default=False)
    git_remote_url = CharField(null=True)
    github_name = CharField(null=True, index=True)
    default_branch = CharField(null=True)
    last_commit_at = DateTimeField(null=True)

    # Stats
    file_count = IntegerField(default=0)
    lines_of_code = IntegerField(default=0)
    size_bytes = IntegerField(default=0)

    # Content
    readme_content = TextField(null=True)
    metadata_json = TextField(null=True)  # Full METADATA.json as string

    # Timestamps
    scanned_at = DateTimeField(null=True)
    generated_at = DateTimeField(null=True)
    indexed_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.now)
    updated_at = DateTimeField(default=datetime.now)

    # Project activity dates (from file system, not database)
    project_created_at = DateTimeField(null=True, index=True)  # Earliest file or first commit
    last_code_modified_at = DateTimeField(null=True, index=True)  # Latest code file change

    def get_languages(self) -> List[str]:
        """Get languages as list."""
        try:
            return json.loads(self.languages)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_languages(self, langs: List[str]):
        """Set languages from list."""
        self.languages = json.dumps(langs)

    def get_frameworks(self) -> List[str]:
        """Get frameworks as list."""
        try:
            return json.loads(self.frameworks)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_frameworks(self, fws: List[str]):
        """Set frameworks from list."""
        self.frameworks = json.dumps(fws)

    def get_metadata(self) -> Optional[Dict[str, Any]]:
        """Get parsed metadata dict."""
        if self.metadata_json:
            try:
                return json.loads(self.metadata_json)
            except json.JSONDecodeError:
                return None
        return None

    def save(self, *args, **kwargs):
        """Update timestamp on save."""
        self.updated_at = datetime.now()
        return super().save(*args, **kwargs)

    class Meta:
        table_name = 'projects'


class Module(BaseModel):
    """A module/file within a project."""

    project = ForeignKeyField(Project, backref='modules', on_delete='CASCADE')
    name = CharField()
    path = CharField()  # Relative path within project
    description = TextField(default="")
    language = CharField(null=True)
    lines = IntegerField(default=0)

    class Meta:
        table_name = 'modules'
        indexes = (
            (('project', 'path'), True),  # Unique together
        )


class ProjectFile(BaseModel):
    """A file within a project for browsing."""

    project = ForeignKeyField(Project, backref='files', on_delete='CASCADE')
    path = CharField()  # Relative path within project
    name = CharField()  # File name only
    is_directory = BooleanField(default=False)
    size_bytes = IntegerField(default=0)
    modified_at = DateTimeField(null=True)
    language = CharField(null=True)  # Detected language for syntax highlighting

    class Meta:
        table_name = 'project_files'
        indexes = (
            (('project', 'path'), True),  # Unique together
        )


class Keyword(BaseModel):
    """Normalized keywords for tagging projects."""

    name = CharField(unique=True, index=True)
    count = IntegerField(default=0)  # Number of projects with this keyword

    class Meta:
        table_name = 'keywords'


class ProjectKeyword(BaseModel):
    """Many-to-many relationship between projects and keywords."""

    project = ForeignKeyField(Project, backref='project_keywords', on_delete='CASCADE')
    keyword = ForeignKeyField(Keyword, backref='keyword_projects', on_delete='CASCADE')

    class Meta:
        table_name = 'project_keywords'
        indexes = (
            (('project', 'keyword'), True),  # Unique together
        )


class Dependency(BaseModel):
    """Project dependencies."""

    project = ForeignKeyField(Project, backref='dependencies', on_delete='CASCADE')
    name = CharField()
    dep_type = CharField()  # python, npm, cargo, go, etc.
    version = CharField(null=True)
    is_dev = BooleanField(default=False)

    class Meta:
        table_name = 'dependencies'
        indexes = (
            (('project', 'name', 'dep_type'), True),  # Unique together
        )


class ProjectVector(BaseModel):
    """Vector embeddings for semantic search."""

    project = ForeignKeyField(Project, backref='vectors', on_delete='CASCADE', unique=True)

    # Store different embedding types
    description_embedding = BlobField(null=True)  # Short description embedding
    full_embedding = BlobField(null=True)  # Combined content embedding

    embedding_model = CharField(default="all-MiniLM-L6-v2")
    created_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'project_vectors'


class LOCHistory(BaseModel):
    """Track lines of code over time for each project."""

    project = ForeignKeyField(Project, backref='loc_history', on_delete='CASCADE')
    lines_of_code = IntegerField()
    file_count = IntegerField()
    recorded_at = DateTimeField(default=datetime.now)

    class Meta:
        table_name = 'loc_history'
        indexes = (
            (('project', 'recorded_at'), False),
        )


class ScanLog(BaseModel):
    """Log of scan operations for tracking and scheduling."""

    scan_type = CharField()  # 'full', 'incremental', 'single'
    started_at = DateTimeField(default=datetime.now)
    completed_at = DateTimeField(null=True)
    projects_scanned = IntegerField(default=0)
    projects_changed = IntegerField(default=0)
    errors = TextField(default="[]")  # JSON array of error messages
    triggered_by = CharField(default="manual")  # 'manual', 'scheduled', 'api'

    class Meta:
        table_name = 'scan_logs'


# Full-text search model using FTS5
class ProjectFTS(FTS5Model):
    """Full-text search index for projects."""

    rowid = RowIDField()
    name = SearchField()
    short_description = SearchField()
    long_description = SearchField()
    readme_content = SearchField()
    keywords = SearchField()  # Space-separated keywords

    class Meta:
        database = db
        table_name = 'project_fts'
        options = {
            'tokenize': 'porter unicode61',
        }


def create_tables():
    """Create all database tables."""
    with db:
        db.create_tables([
            Project, Module, ProjectFile, Keyword, ProjectKeyword,
            Dependency, ProjectVector, LOCHistory, ScanLog, ProjectFTS
        ], safe=True)


def drop_tables():
    """Drop all database tables."""
    with db:
        db.drop_tables([
            ProjectFTS, ScanLog, LOCHistory, ProjectVector, Dependency,
            ProjectKeyword, Keyword, ProjectFile, Module, Project
        ], safe=True)


def reset_database():
    """Drop and recreate all tables."""
    drop_tables()
    create_tables()
