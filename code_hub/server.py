"""FastAPI web server for Code Hub.

Provides web UI and REST API for browsing projects, searching code, generating
documentation, and managing project scanning. Includes admin functionality for
initiating scans, bulk-generating missing docs (README, USAGE, METADATA),
viewing changed projects, and tracking LOC history. Long-running operations
(scans, bulk generation) run in background threads with live progress via SSE.

Scanning uses DocumentationGenerator.save_to_database() to properly load
METADATA.json content (descriptions, keywords, modules, frameworks) into the
database, ensuring consistency with CLI scanning.
"""
import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import markdown

from code_hub.models import (
    Project, Module, ProjectFile, Keyword, ProjectKeyword,
    LOCHistory, ScanLog, db, create_tables
)
from code_hub.indexer import get_indexer
from code_hub.config import settings


# Pydantic models for API responses
class ModuleResponse(BaseModel):
    name: str
    path: str
    description: str


class ProjectResponse(BaseModel):
    id: int
    name: str
    path: str
    short_description: str
    long_description: Optional[str] = None
    primary_language: Optional[str] = None
    languages: List[str] = []
    frameworks: List[str] = []
    is_git_repo: bool = False
    github_name: Optional[str] = None
    github_url: Optional[str] = None
    file_count: int = 0
    lines_of_code: int = 0
    keywords: List[str] = []
    modules: List[ModuleResponse] = []


class SearchResult(BaseModel):
    project: ProjectResponse
    score: float


class StatsResponse(BaseModel):
    total_projects: int
    total_modules: int
    total_keywords: int
    total_lines_of_code: int
    languages: dict
    top_keywords: List[dict]


class FileResponse(BaseModel):
    path: str
    name: str
    is_directory: bool
    size_bytes: int
    modified_at: Optional[str] = None
    language: Optional[str] = None


class FileContentResponse(BaseModel):
    path: str
    name: str
    content: str
    language: Optional[str] = None
    highlighted_html: Optional[str] = None
    size_bytes: int
    modified_at: Optional[str] = None


# Scan task manager for background scans with progress tracking
@dataclass
class ScanProgress:
    """Tracks progress of a scan operation."""
    scan_id: str
    scan_type: str
    status: str = "pending"  # pending, running, completed, error
    total_projects: int = 0
    scanned_count: int = 0
    current_project: str = ""
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    errors: List[str] = field(default_factory=list)
    log_messages: deque = field(default_factory=lambda: deque(maxlen=500))

    def add_log(self, message: str, level: str = "info"):
        """Add a log message."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "message": message
        }
        self.log_messages.append(entry)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "scan_id": self.scan_id,
            "scan_type": self.scan_type,
            "status": self.status,
            "total_projects": self.total_projects,
            "scanned_count": self.scanned_count,
            "current_project": self.current_project,
            "progress_percent": round(self.scanned_count / max(self.total_projects, 1) * 100, 1),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "errors": self.errors,
            "error_count": len(self.errors)
        }


class ScanTaskManager:
    """Manages background scan tasks with progress tracking."""

    def __init__(self):
        self.current_scan: Optional[ScanProgress] = None
        self.scan_history: deque = deque(maxlen=10)
        self._lock = threading.Lock()
        self._subscribers: List[asyncio.Queue] = []

    def start_scan(self, scan_type: str, total_projects: int = 0) -> ScanProgress:
        """Start a new scan, returning the progress tracker."""
        with self._lock:
            if self.current_scan and self.current_scan.status == "running":
                raise ValueError("A scan is already in progress")

            scan_id = f"{scan_type}_{int(time.time())}"
            self.current_scan = ScanProgress(
                scan_id=scan_id,
                scan_type=scan_type,
                status="running",
                total_projects=total_projects,
                started_at=datetime.now()
            )
            self.current_scan.add_log(f"Starting {scan_type} scan...")
            self._notify_subscribers()
            return self.current_scan

    def update_progress(self, scanned_count: int = None, current_project: str = None,
                        total_projects: int = None, log_message: str = None, level: str = "info"):
        """Update scan progress."""
        with self._lock:
            if not self.current_scan:
                return

            if scanned_count is not None:
                self.current_scan.scanned_count = scanned_count
            if current_project is not None:
                self.current_scan.current_project = current_project
            if total_projects is not None:
                self.current_scan.total_projects = total_projects
            if log_message:
                self.current_scan.add_log(log_message, level)

            self._notify_subscribers()

    def add_error(self, error: str):
        """Add an error to the current scan."""
        with self._lock:
            if self.current_scan:
                self.current_scan.errors.append(error)
                self.current_scan.add_log(f"Error: {error}", "error")
                self._notify_subscribers()

    def complete_scan(self, success: bool = True):
        """Mark the current scan as complete."""
        with self._lock:
            if self.current_scan:
                self.current_scan.status = "completed" if success else "error"
                self.current_scan.completed_at = datetime.now()
                self.current_scan.add_log(
                    f"Scan completed: {self.current_scan.scanned_count} projects scanned",
                    "info" if success else "error"
                )
                self.scan_history.append(self.current_scan)
                self._notify_subscribers()

    def get_status(self) -> Optional[dict]:
        """Get current scan status."""
        with self._lock:
            if self.current_scan:
                return self.current_scan.to_dict()
            return None

    def get_logs(self, limit: int = 100) -> List[dict]:
        """Get recent log messages."""
        with self._lock:
            if self.current_scan:
                logs = list(self.current_scan.log_messages)
                return logs[-limit:]
            return []

    def is_running(self) -> bool:
        """Check if a scan is currently running."""
        with self._lock:
            return self.current_scan is not None and self.current_scan.status == "running"

    async def subscribe(self) -> asyncio.Queue:
        """Subscribe to scan updates."""
        queue = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """Unsubscribe from scan updates."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def _notify_subscribers(self):
        """Notify all subscribers of an update."""
        status = self.current_scan.to_dict() if self.current_scan else None
        logs = list(self.current_scan.log_messages)[-20:] if self.current_scan else []

        for queue in self._subscribers:
            try:
                queue.put_nowait({"status": status, "logs": logs})
            except asyncio.QueueFull:
                pass


# Global scan task manager
scan_manager = ScanTaskManager()


# Lifespan handler for startup/shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    db.connect(reuse_if_open=True)
    create_tables()

    # Start scheduler for daily scans at 07:00
    from code_hub.scheduler import start_scheduler
    start_scheduler(hour=7, minute=0)
    logger.info("Started background scheduler for daily 07:00 scans")

    yield

    # Shutdown
    from code_hub.scheduler import stop_scheduler
    stop_scheduler()
    if not db.is_closed():
        db.close()


app = FastAPI(
    title="Code Hub",
    description="Search and explore your code projects",
    version="1.0.0",
    lifespan=lifespan
)

# Static files and templates
static_path = settings.static_dir
templates_path = settings.templates_dir

if static_path.exists():
    app.mount("/static", StaticFiles(directory=static_path), name="static")

templates = Jinja2Templates(directory=templates_path) if templates_path.exists() else None


# Helper functions
def project_to_response(project: Project) -> ProjectResponse:
    """Convert database project to API response."""
    keywords = [pk.keyword.name for pk in project.project_keywords]
    modules = [
        ModuleResponse(name=m.name, path=m.path, description=m.description)
        for m in project.modules
    ]

    github_url = None
    if project.github_name:
        github_url = f"https://github.com/{project.github_name}"

    return ProjectResponse(
        id=project.id,
        name=project.name,
        path=project.path,
        short_description=project.short_description or "",
        long_description=project.long_description,
        primary_language=project.primary_language,
        languages=project.get_languages(),
        frameworks=project.get_frameworks(),
        is_git_repo=project.is_git_repo,
        github_name=project.github_name,
        github_url=github_url,
        file_count=project.file_count,
        lines_of_code=project.lines_of_code,
        keywords=keywords,
        modules=modules
    )


def render_markdown(text: str) -> str:
    """Render markdown to HTML."""
    if not text:
        return ""
    return markdown.markdown(
        text,
        extensions=['fenced_code', 'tables', 'toc', 'codehilite'],
        extension_configs={
            'codehilite': {
                'css_class': 'highlight',
                'guess_lang': True
            }
        }
    )


def highlight_code(content: str, language: Optional[str] = None, filename: Optional[str] = None, hl_lines: List[int] = None) -> str:
    """Syntax highlight code using Pygments."""
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename, TextLexer
    from pygments.formatters import HtmlFormatter

    try:
        if language:
            lexer = get_lexer_by_name(language)
        elif filename:
            lexer = get_lexer_for_filename(filename)
        else:
            lexer = TextLexer()
    except Exception:
        lexer = TextLexer()

    formatter = HtmlFormatter(
        linenos=True,
        cssclass='highlight',
        lineanchors='line',
        anchorlinenos=True,
        hl_lines=hl_lines or []
    )
    return highlight(content, lexer, formatter)


def format_file_size(size_bytes: int) -> str:
    """Format file size for display."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != 'B' else f"{size_bytes} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# API Routes
@app.get("/api/projects", response_model=List[ProjectResponse])
async def list_projects(
    language: Optional[str] = None,
    keyword: Optional[str] = None,
    limit: int = Query(50, le=500),
    offset: int = 0
):
    """List all projects with optional filtering."""
    query = Project.select()

    if language:
        query = query.where(Project.primary_language == language)

    if keyword:
        query = (
            query
            .join(ProjectKeyword)
            .join(Keyword)
            .where(Keyword.name == keyword.lower())
            .switch(Project)
        )

    projects = query.order_by(Project.name).offset(offset).limit(limit)
    return [project_to_response(p) for p in projects]


@app.get("/api/projects/{name}", response_model=ProjectResponse)
async def get_project(name: str):
    """Get a specific project by name."""
    try:
        project = Project.get(Project.name == name)
        return project_to_response(project)
    except Project.DoesNotExist:
        raise HTTPException(status_code=404, detail="Project not found")


@app.get("/api/projects/{name}/readme")
async def get_project_readme(name: str):
    """Get project README content."""
    try:
        project = Project.get(Project.name == name)
        return {
            "content": project.readme_content or "No README available",
            "html": render_markdown(project.readme_content) if project.readme_content else ""
        }
    except Project.DoesNotExist:
        raise HTTPException(status_code=404, detail="Project not found")


@app.get("/api/projects/{name}/files", response_model=List[FileResponse])
async def list_project_files(
    name: str,
    dir: str = Query("", description="Subdirectory path to list")
):
    """List files in a project, optionally in a subdirectory."""
    try:
        project = Project.get(Project.name == name)
    except Project.DoesNotExist:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get files from database
    query = ProjectFile.select().where(ProjectFile.project == project)

    # Filter by directory if specified
    if dir:
        dir = dir.rstrip('/')
        # Get files in this directory (not subdirectories)
        query = query.where(
            (ProjectFile.path.startswith(dir + '/')) |
            (ProjectFile.path == dir)
        )

    files = []
    seen_dirs = set()

    for f in query.order_by(ProjectFile.path):
        rel_path = f.path
        if dir:
            rel_path = f.path[len(dir) + 1:] if f.path.startswith(dir + '/') else f.path

        # Check if this is in a subdirectory
        if '/' in rel_path:
            subdir = rel_path.split('/')[0]
            if subdir not in seen_dirs:
                seen_dirs.add(subdir)
                files.append(FileResponse(
                    path=f"{dir}/{subdir}" if dir else subdir,
                    name=subdir,
                    is_directory=True,
                    size_bytes=0,
                    modified_at=None,
                    language=None
                ))
        else:
            files.append(FileResponse(
                path=f.path,
                name=f.name,
                is_directory=f.is_directory,
                size_bytes=f.size_bytes,
                modified_at=f.modified_at.isoformat() if f.modified_at else None,
                language=f.language
            ))

    # Sort: directories first, then files
    files.sort(key=lambda x: (not x.is_directory, x.name.lower()))
    return files


@app.get("/api/projects/{name}/file/{file_path:path}", response_model=FileContentResponse)
async def get_file_content(name: str, file_path: str):
    """Get file content with syntax highlighting."""
    try:
        project = Project.get(Project.name == name)
    except Project.DoesNotExist:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get file info from database
    try:
        file_info = ProjectFile.get(
            (ProjectFile.project == project) &
            (ProjectFile.path == file_path)
        )
    except ProjectFile.DoesNotExist:
        raise HTTPException(status_code=404, detail="File not found")

    # Read file content from disk
    full_path = Path(project.path) / file_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Check file size (limit to 1MB for viewing)
    if file_info.size_bytes > 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large to view (max 1MB)")

    try:
        content = full_path.read_text(errors='replace')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read file: {e}")

    # Syntax highlight
    highlighted = highlight_code(content, file_info.language, file_info.name)

    return FileContentResponse(
        path=file_path,
        name=file_info.name,
        content=content,
        language=file_info.language,
        highlighted_html=highlighted,
        size_bytes=file_info.size_bytes,
        modified_at=file_info.modified_at.isoformat() if file_info.modified_at else None
    )


class GenerateResponse(BaseModel):
    success: bool
    message: str
    content: Optional[str] = None


@app.post("/api/projects/{name}/generate/readme", response_model=GenerateResponse)
def generate_readme(name: str):
    """Generate README.md for a project using Claude."""
    from code_hub.claude_wrapper import ClaudeWrapper
    from code_hub.generator import DocumentationGenerator

    try:
        project = Project.get(Project.name == name)
    except Project.DoesNotExist:
        raise HTTPException(status_code=404, detail="Project not found")

    project_path = Path(project.path)
    readme_path = project_path / 'README.md'

    # Check if README already exists
    if readme_path.exists() and readme_path.stat().st_size > 50:
        return GenerateResponse(
            success=False,
            message="README.md already exists. Delete it first to regenerate."
        )

    try:
        claude = ClaudeWrapper()
        response = claude.generate_readme(project_path)

        if response.success and response.content.strip():
            generator = DocumentationGenerator()
            content = generator._clean_markdown_content(response.content)
            readme_path.write_text(content)

            # Update database
            project.readme_content = content
            project.save()

            return GenerateResponse(
                success=True,
                message="README.md generated successfully",
                content=content
            )
        else:
            return GenerateResponse(
                success=False,
                message=f"Generation failed: {response.error or 'Empty response'}"
            )
    except Exception as e:
        logger.error(f"README generation error: {e}")
        return GenerateResponse(
            success=False,
            message=f"Generation error: {str(e)}"
        )


@app.post("/api/projects/{name}/generate/usage", response_model=GenerateResponse)
def generate_usage(name: str):
    """Generate USAGE.md for a project using Claude."""
    from code_hub.claude_wrapper import ClaudeWrapper
    from code_hub.generator import DocumentationGenerator

    try:
        project = Project.get(Project.name == name)
    except Project.DoesNotExist:
        raise HTTPException(status_code=404, detail="Project not found")

    project_path = Path(project.path)
    usage_path = project_path / 'USAGE.md'

    # Check if USAGE already exists
    if usage_path.exists() and usage_path.stat().st_size > 50:
        return GenerateResponse(
            success=False,
            message="USAGE.md already exists. Delete it first to regenerate."
        )

    try:
        claude = ClaudeWrapper()
        response = claude.generate_usage(project_path)

        if response.success and response.content.strip():
            generator = DocumentationGenerator()
            content = generator._clean_markdown_content(response.content)
            usage_path.write_text(content)

            return GenerateResponse(
                success=True,
                message="USAGE.md generated successfully",
                content=content
            )
        else:
            return GenerateResponse(
                success=False,
                message=f"Generation failed: {response.error or 'Empty response'}"
            )
    except Exception as e:
        logger.error(f"USAGE generation error: {e}")
        return GenerateResponse(
            success=False,
            message=f"Generation error: {str(e)}"
        )


class DeepSearchMatch(BaseModel):
    file_path: str
    file_name: str
    line_number: int
    line_content: str
    context_before: List[str] = []
    context_after: List[str] = []
    language: Optional[str] = None


class DeepSearchResponse(BaseModel):
    query: str
    total_matches: int
    matches: List[DeepSearchMatch]


@app.get("/api/projects/{name}/search", response_model=DeepSearchResponse)
async def deep_search_project(
    name: str,
    q: str = Query(..., min_length=1),
    limit: int = Query(100, le=500),
    context: int = Query(2, le=5)
):
    """Search within project files using grep."""
    import subprocess
    import shlex

    try:
        project = Project.get(Project.name == name)
    except Project.DoesNotExist:
        raise HTTPException(status_code=404, detail="Project not found")

    project_path = Path(project.path)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail="Project path not found")

    # Build grep command with context lines
    # Using grep -rn for recursive search with line numbers
    # -I to skip binary files, --include for common code files
    try:
        cmd = [
            'grep', '-rn', '-I',
            f'-C{context}',  # Context lines before and after
            '--include=*.py', '--include=*.js', '--include=*.ts', '--include=*.jsx', '--include=*.tsx',
            '--include=*.java', '--include=*.c', '--include=*.cpp', '--include=*.h', '--include=*.hpp',
            '--include=*.go', '--include=*.rs', '--include=*.rb', '--include=*.php',
            '--include=*.html', '--include=*.css', '--include=*.scss', '--include=*.sass',
            '--include=*.json', '--include=*.yaml', '--include=*.yml', '--include=*.toml',
            '--include=*.xml', '--include=*.md', '--include=*.txt', '--include=*.sh',
            '--include=*.sql', '--include=*.lua', '--include=*.swift', '--include=*.kt',
            '--include=*.scala', '--include=*.r', '--include=*.R', '--include=*.jl',
            '--',  # End of options
            q,
            str(project_path)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_path
        )

        # Parse grep output
        matches = []
        current_match = None
        current_context_before = []
        current_context_after = []

        # Grep with context outputs in format:
        # file:linenum:content (for matches, uses : before and after line number)
        # file-linenum-content (for context, uses - before and after line number)
        # -- (separator between match groups)

        # Regex patterns to match from the end (line number is always numeric)
        # Match line: filepath:linenum:content
        match_pattern = re.compile(r'^(.+?):(\d+):(.*)$')
        # Context line: filepath-linenum-content
        context_pattern = re.compile(r'^(.+?)-(\d+)-(.*)$')

        # Language detection map
        lang_map = {
            '.py': 'python', '.js': 'javascript', '.ts': 'typescript',
            '.jsx': 'javascript', '.tsx': 'typescript', '.java': 'java',
            '.c': 'c', '.cpp': 'cpp', '.h': 'c', '.hpp': 'cpp',
            '.go': 'go', '.rs': 'rust', '.rb': 'ruby', '.php': 'php',
            '.html': 'html', '.css': 'css', '.json': 'json',
            '.yaml': 'yaml', '.yml': 'yaml', '.md': 'markdown',
            '.sh': 'bash', '.sql': 'sql'
        }

        lines = result.stdout.split('\n')
        for line in lines:
            if len(matches) >= limit:
                break

            if line == '--' or line == '':
                # End of a match group - save current match
                if current_match:
                    current_match['context_after'] = current_context_after[:context]
                    matches.append(DeepSearchMatch(**current_match))
                    current_match = None
                    current_context_before = []
                    current_context_after = []
                continue

            # Try to parse as a match line first (uses : separator)
            match_result = match_pattern.match(line)
            if match_result:
                filepath, linenum_str, content = match_result.groups()
                try:
                    linenum = int(linenum_str)

                    # Make path relative to project
                    if filepath.startswith(str(project_path)):
                        filepath = filepath[len(str(project_path)) + 1:]

                    # Save previous match if exists
                    if current_match:
                        current_match['context_after'] = current_context_after[:context]
                        matches.append(DeepSearchMatch(**current_match))

                    ext = Path(filepath).suffix.lower()
                    current_match = {
                        'file_path': filepath,
                        'file_name': Path(filepath).name,
                        'line_number': linenum,
                        'line_content': content,
                        'context_before': current_context_before[-context:] if current_context_before else [],
                        'language': lang_map.get(ext)
                    }
                    current_context_before = []
                    current_context_after = []
                except ValueError:
                    pass
                continue

            # Try to parse as context line (uses - separator)
            context_result = context_pattern.match(line)
            if context_result:
                filepath, linenum_str, content = context_result.groups()
                try:
                    int(linenum_str)  # Validate it's a number
                    if current_match:
                        current_context_after.append(content)
                    else:
                        current_context_before.append(content)
                except ValueError:
                    pass

        # Don't forget last match
        if current_match:
            current_match['context_after'] = current_context_after[:context]
            matches.append(DeepSearchMatch(**current_match))

        return DeepSearchResponse(
            query=q,
            total_matches=len(matches),
            matches=matches
        )

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Search timed out")
    except Exception as e:
        logger.error(f"Deep search error: {e}")
        raise HTTPException(status_code=500, detail=f"Search error: {str(e)}")


@app.get("/api/search", response_model=List[SearchResult])
async def search_projects(
    q: str = Query(..., min_length=1),
    mode: str = Query("hybrid", pattern="^(fts|semantic|hybrid)$"),
    limit: int = Query(20, le=100)
):
    """Search projects using full-text or semantic search."""
    indexer = get_indexer()

    if mode == "fts":
        results = indexer.search_fts(q, limit=limit)
        return [
            SearchResult(
                project=project_to_response(p),
                score=1.0 - i / max(len(results), 1)
            )
            for i, p in enumerate(results)
        ]
    elif mode == "semantic":
        results = indexer.search_semantic(q, limit=limit)
        return [
            SearchResult(project=project_to_response(p), score=score)
            for p, score in results
        ]
    else:  # hybrid
        results = indexer.search_hybrid(q, limit=limit)
        return [
            SearchResult(project=project_to_response(p), score=score)
            for p, score in results
        ]


@app.get("/api/keywords")
async def list_keywords(limit: int = Query(100, le=500)):
    """List all keywords sorted by usage count."""
    keywords = (
        Keyword
        .select()
        .where(Keyword.count > 0)
        .order_by(Keyword.count.desc())
        .limit(limit)
    )
    return [{"name": k.name, "count": k.count} for k in keywords]


@app.get("/api/languages")
async def list_languages():
    """List all primary languages with project counts."""
    from peewee import fn

    results = (
        Project
        .select(Project.primary_language, fn.COUNT(Project.id).alias('count'))
        .where(Project.primary_language.is_null(False))
        .group_by(Project.primary_language)
        .order_by(fn.COUNT(Project.id).desc())
    )

    return [{"language": r.primary_language, "count": r.count} for r in results]


@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """Get system statistics."""
    from peewee import fn

    total_projects = Project.select().count()
    total_modules = Module.select().count()
    total_keywords = Keyword.select().where(Keyword.count > 0).count()
    total_loc = Project.select(fn.SUM(Project.lines_of_code)).scalar() or 0

    # Language distribution
    lang_results = (
        Project
        .select(Project.primary_language, fn.COUNT(Project.id).alias('count'))
        .where(Project.primary_language.is_null(False))
        .group_by(Project.primary_language)
        .order_by(fn.COUNT(Project.id).desc())
        .limit(15)
    )
    languages = {r.primary_language: r.count for r in lang_results}

    # Top keywords
    top_kw = (
        Keyword
        .select()
        .where(Keyword.count > 0)
        .order_by(Keyword.count.desc())
        .limit(30)
    )
    top_keywords = [{"name": k.name, "count": k.count} for k in top_kw]

    return StatsResponse(
        total_projects=total_projects,
        total_modules=total_modules,
        total_keywords=total_keywords,
        total_lines_of_code=total_loc,
        languages=languages,
        top_keywords=top_keywords
    )


# Admin API Models
class ChangedProjectResponse(BaseModel):
    name: str
    path: str
    last_modified: str
    scanned_at: Optional[str] = None
    is_new: bool


class ScanResultResponse(BaseModel):
    scan_type: str
    projects_found: int
    projects_scanned: int
    errors: List[str]
    triggered_by: str


class ScanLogResponse(BaseModel):
    id: int
    scan_type: str
    started_at: str
    completed_at: Optional[str]
    projects_scanned: int
    projects_changed: int
    errors: List[str]
    triggered_by: str


class LOCHistoryEntry(BaseModel):
    recorded_at: str
    lines_of_code: int
    file_count: int


class SchedulerStatusResponse(BaseModel):
    running: bool
    next_scan: Optional[str]


# Admin API Routes
@app.get("/api/admin/changed-projects", response_model=List[ChangedProjectResponse])
async def get_changed_projects():
    """Get list of projects that have changed since last scan."""
    from code_hub.scanner import get_changed_projects as find_changed

    changed = find_changed()

    # Get known projects from DB
    known_projects = {p.path: p for p in Project.select()}

    results = []
    for project_path, last_modified in changed:
        path_str = str(project_path)
        db_project = known_projects.get(path_str)

        results.append(ChangedProjectResponse(
            name=project_path.name,
            path=path_str,
            last_modified=last_modified.isoformat(),
            scanned_at=db_project.scanned_at.isoformat() if db_project and db_project.scanned_at else None,
            is_new=db_project is None
        ))

    return results


def _run_scan_in_background(scan_type: str, project_paths: List[Path] = None,
                            single_project: str = None, regenerate_metadata: bool = False):
    """Run scan in background thread with progress updates.

    Args:
        scan_type: Type of scan ('full', 'incremental', 'single')
        project_paths: List of project paths to scan (for incremental)
        single_project: Name of single project to scan
        regenerate_metadata: Whether to regenerate METADATA.json for scanned projects
    """
    from code_hub.scanner import ProjectScanner, get_changed_projects
    from code_hub.generator import DocumentationGenerator
    from code_hub.models import Project, LOCHistory, ScanLog

    # Lazy import to avoid circular imports and only load when needed
    claude_wrapper = None
    if regenerate_metadata:
        try:
            from code_hub.claude_wrapper import ClaudeWrapper
            claude_wrapper = ClaudeWrapper()
            scan_manager.update_progress(log_message="Metadata regeneration enabled")
        except Exception as e:
            scan_manager.update_progress(log_message=f"Warning: Could not initialize Claude: {e}", level="warning")
            regenerate_metadata = False

    scanner = ProjectScanner()
    generator = DocumentationGenerator()

    # Determine what to scan
    if single_project:
        project_path = settings.code_base_path / single_project
        if not project_path.exists():
            try:
                proj = Project.get(Project.name == single_project)
                project_path = Path(proj.path)
            except Project.DoesNotExist:
                scan_manager.add_error(f"Project not found: {single_project}")
                scan_manager.complete_scan(success=False)
                return
        paths_to_scan = [(project_path, None)]
    elif project_paths:
        paths_to_scan = project_paths
    elif scan_type == "incremental":
        paths_to_scan = get_changed_projects()
    else:  # full scan
        paths_to_scan = [(p, None) for p in scanner.discover_projects()]

    total = len(paths_to_scan)
    scan_manager.update_progress(total_projects=total, log_message=f"Found {total} projects to scan")

    if total == 0:
        scan_manager.update_progress(log_message="No projects to scan")
        scan_manager.complete_scan(success=True)
        return

    # Create scan log
    scan_log = ScanLog.create(
        scan_type=scan_type,
        triggered_by='api'
    )

    scanned_count = 0
    errors = []

    for i, item in enumerate(paths_to_scan):
        project_path = item[0] if isinstance(item, tuple) else item
        project_name = project_path.name

        scan_manager.update_progress(
            scanned_count=i,
            current_project=project_name,
            log_message=f"Scanning {project_name}..."
        )

        try:
            scanned = scanner.scan_project(project_path)

            # Generate metadata if missing (always) or if regenerate requested
            metadata_path = project_path / 'METADATA.json'
            metadata_exists = metadata_path.exists()
            needs_metadata = not metadata_exists or regenerate_metadata

            if needs_metadata:
                # Lazy-init Claude wrapper if not already done
                if claude_wrapper is None:
                    try:
                        from code_hub.claude_wrapper import ClaudeWrapper
                        claude_wrapper = ClaudeWrapper()
                    except Exception as e:
                        scan_manager.update_progress(
                            log_message=f"  Warning: Could not initialize Claude for metadata: {e}",
                            level="warning"
                        )
                        claude_wrapper = False  # Mark as failed so we don't retry

                if claude_wrapper:
                    try:
                        reason = "missing" if not metadata_exists else "regenerate"
                        scan_manager.update_progress(
                            log_message=f"  Generating METADATA for {project_name} ({reason})..."
                        )
                        response = claude_wrapper.generate_metadata(project_path)
                        if response.success and response.content.strip():
                            # Parse and save metadata
                            try:
                                metadata = json.loads(response.content)
                                metadata_path.write_text(json.dumps(metadata, indent=2))
                                scan_manager.update_progress(
                                    log_message=f"  METADATA saved for {project_name}"
                                )
                                # Update scanned object so save_to_database picks it up
                                scanned.existing_metadata = metadata
                            except json.JSONDecodeError as e:
                                scan_manager.update_progress(
                                    log_message=f"  METADATA parse error for {project_name}: {e}",
                                    level="warning"
                                )
                        else:
                            scan_manager.update_progress(
                                log_message=f"  METADATA generation failed for {project_name}: {response.error or 'Empty response'}",
                                level="warning"
                            )
                    except Exception as e:
                        scan_manager.update_progress(
                            log_message=f"  METADATA error for {project_name}: {e}",
                            level="warning"
                        )

            # Check if project already exists to track LOC changes
            path_str = str(project_path)
            old_loc = None
            try:
                existing_project = Project.get(Project.path == path_str)
                old_loc = existing_project.lines_of_code
                is_new_project = False
            except Project.DoesNotExist:
                is_new_project = True

            # Use generator.save_to_database() which properly loads METADATA.json
            # into the database (short_description, keywords, modules, etc.)
            project = generator.save_to_database(scanned)

            # Record LOC history if changed or new
            if is_new_project:
                LOCHistory.create(
                    project=project,
                    lines_of_code=project.lines_of_code,
                    file_count=project.file_count
                )
                scan_manager.update_progress(
                    log_message=f"  {project_name}: NEW ({project.lines_of_code} LOC)"
                )
            elif old_loc is not None and project.lines_of_code != old_loc:
                LOCHistory.create(
                    project=project,
                    lines_of_code=project.lines_of_code,
                    file_count=project.file_count
                )
                scan_manager.update_progress(
                    log_message=f"  {project_name}: {old_loc} -> {project.lines_of_code} LOC"
                )

            scanned_count += 1

        except Exception as e:
            error_msg = f"{project_name}: {str(e)}"
            errors.append(error_msg)
            scan_manager.add_error(error_msg)

    # Update final progress
    scan_manager.update_progress(
        scanned_count=scanned_count,
        current_project="",
        log_message=f"Scan complete: {scanned_count}/{total} projects"
    )

    # Save scan log
    scan_log.completed_at = datetime.now()
    scan_log.projects_scanned = scanned_count
    scan_log.projects_changed = scanned_count
    scan_log.errors = json.dumps(errors)
    scan_log.save()

    scan_manager.complete_scan(success=len(errors) == 0)


class ScanOptions(BaseModel):
    """Options for scan operations."""
    regenerate_metadata: bool = False


class GenerateMissingOptions(BaseModel):
    """Options for bulk generation of missing docs."""
    readme: bool = True
    usage: bool = True
    metadata: bool = True


def _run_generate_missing_in_background(gen_readme: bool, gen_usage: bool, gen_metadata: bool):
    """Generate missing documentation files for all projects.

    Iterates over all projects in the database, checks which doc files are
    missing on disk, and uses Claude to generate them. Progress is reported
    via the shared scan_manager for SSE streaming to the admin UI.
    """
    from code_hub.claude_wrapper import ClaudeWrapper
    from code_hub.generator import DocumentationGenerator
    from code_hub.scanner import ProjectScanner

    try:
        claude = ClaudeWrapper()
    except Exception as e:
        scan_manager.add_error(f"Could not initialize Claude: {e}")
        scan_manager.complete_scan(success=False)
        return

    generator = DocumentationGenerator()
    scanner = ProjectScanner()

    projects = list(Project.select().order_by(Project.name))
    total = len(projects)
    scan_manager.update_progress(total_projects=total, log_message=f"Checking {total} projects for missing docs")

    generated_count = 0
    errors = []

    for i, project in enumerate(projects):
        project_path = Path(project.path)
        project_name = project.name

        if not project_path.exists():
            scan_manager.update_progress(
                scanned_count=i + 1,
                current_project=project_name,
                log_message=f"  Skipping {project_name}: path not found"
            )
            continue

        # Determine what's missing
        needs_readme = gen_readme and not (project_path / 'README.md').exists()
        needs_usage = gen_usage and not (project_path / 'USAGE.md').exists()
        needs_metadata = gen_metadata and not (project_path / 'METADATA.json').exists()

        if not needs_readme and not needs_usage and not needs_metadata:
            scan_manager.update_progress(
                scanned_count=i + 1,
                current_project=project_name
            )
            continue

        missing = []
        if needs_readme:
            missing.append("README")
        if needs_usage:
            missing.append("USAGE")
        if needs_metadata:
            missing.append("METADATA")

        scan_manager.update_progress(
            scanned_count=i,
            current_project=project_name,
            log_message=f"Generating {', '.join(missing)} for {project_name}..."
        )

        # Generate METADATA first (fastest, needed for DB update)
        if needs_metadata:
            try:
                response = claude.generate_metadata(project_path)
                if response.success and response.content.strip():
                    metadata = json.loads(response.content)
                    (project_path / 'METADATA.json').write_text(json.dumps(metadata, indent=2))
                    scan_manager.update_progress(log_message=f"  METADATA saved for {project_name}")
                    generated_count += 1
                else:
                    scan_manager.update_progress(
                        log_message=f"  METADATA failed for {project_name}: {response.error or 'Empty response'}",
                        level="warning"
                    )
            except Exception as e:
                error_msg = f"{project_name} METADATA: {e}"
                errors.append(error_msg)
                scan_manager.add_error(error_msg)

        if needs_readme:
            try:
                response = claude.generate_readme(project_path)
                if response.success and response.content.strip():
                    content = generator._clean_markdown_content(response.content)
                    (project_path / 'README.md').write_text(content)
                    scan_manager.update_progress(log_message=f"  README saved for {project_name}")
                    generated_count += 1
                else:
                    scan_manager.update_progress(
                        log_message=f"  README failed for {project_name}: {response.error or 'Empty response'}",
                        level="warning"
                    )
            except Exception as e:
                error_msg = f"{project_name} README: {e}"
                errors.append(error_msg)
                scan_manager.add_error(error_msg)

        if needs_usage:
            try:
                response = claude.generate_usage(project_path)
                if response.success and response.content.strip():
                    content = generator._clean_markdown_content(response.content)
                    (project_path / 'USAGE.md').write_text(content)
                    scan_manager.update_progress(log_message=f"  USAGE saved for {project_name}")
                    generated_count += 1
                else:
                    scan_manager.update_progress(
                        log_message=f"  USAGE failed for {project_name}: {response.error or 'Empty response'}",
                        level="warning"
                    )
            except Exception as e:
                error_msg = f"{project_name} USAGE: {e}"
                errors.append(error_msg)
                scan_manager.add_error(error_msg)

        # Re-scan and update DB so new docs are indexed
        try:
            scanned = scanner.scan_project(project_path)
            generator.save_to_database(scanned)
        except Exception as e:
            scan_manager.update_progress(
                log_message=f"  DB update failed for {project_name}: {e}",
                level="warning"
            )

        scan_manager.update_progress(scanned_count=i + 1, current_project=project_name)

    scan_manager.update_progress(
        scanned_count=total,
        current_project="",
        log_message=f"Generation complete: {generated_count} files generated, {len(errors)} errors"
    )
    scan_manager.complete_scan(success=len(errors) == 0)


@app.post("/api/admin/generate/missing")
async def trigger_generate_missing(options: Optional[GenerateMissingOptions] = None):
    """Generate all missing documentation files across projects (background)."""
    if scan_manager.is_running():
        raise HTTPException(status_code=409, detail="A task is already in progress")

    opts = options or GenerateMissingOptions()
    if not opts.readme and not opts.usage and not opts.metadata:
        raise HTTPException(status_code=400, detail="At least one doc type must be selected")

    # Count projects with missing docs
    total = Project.select().count()
    scan_manager.start_scan("generate", total_projects=total)

    thread = threading.Thread(
        target=_run_generate_missing_in_background,
        args=(opts.readme, opts.usage, opts.metadata),
        daemon=True
    )
    thread.start()

    return {"message": "Generation started", "scan_id": scan_manager.current_scan.scan_id}


@app.post("/api/admin/scan/incremental")
async def trigger_incremental_scan(options: Optional[ScanOptions] = None):
    """Trigger an incremental scan of changed projects (background)."""
    if scan_manager.is_running():
        raise HTTPException(status_code=409, detail="A scan is already in progress")

    regenerate_metadata = options.regenerate_metadata if options else False

    from code_hub.scanner import get_changed_projects
    changed = get_changed_projects()

    scan_manager.start_scan("incremental", total_projects=len(changed))

    thread = threading.Thread(
        target=_run_scan_in_background,
        args=("incremental", changed, None, regenerate_metadata),
        daemon=True
    )
    thread.start()

    return {"message": "Incremental scan started", "scan_id": scan_manager.current_scan.scan_id}


@app.post("/api/admin/scan/full")
async def trigger_full_scan(options: Optional[ScanOptions] = None):
    """Trigger a full rescan of all projects in ~/Code (background)."""
    if scan_manager.is_running():
        raise HTTPException(status_code=409, detail="A scan is already in progress")

    regenerate_metadata = options.regenerate_metadata if options else False

    from code_hub.scanner import ProjectScanner
    scanner = ProjectScanner()
    project_count = len(list(scanner.discover_projects()))

    scan_manager.start_scan("full", total_projects=project_count)

    thread = threading.Thread(
        target=_run_scan_in_background,
        args=("full", None, None, regenerate_metadata),
        daemon=True
    )
    thread.start()

    return {"message": "Full scan started", "scan_id": scan_manager.current_scan.scan_id}


@app.post("/api/admin/scan/project/{name}")
async def scan_single_project(name: str, options: Optional[ScanOptions] = None):
    """Scan a specific project by name (background)."""
    if scan_manager.is_running():
        raise HTTPException(status_code=409, detail="A scan is already in progress")

    regenerate_metadata = options.regenerate_metadata if options else False

    # Verify project exists
    try:
        project = Project.get(Project.name == name)
        project_path = Path(project.path)
    except Project.DoesNotExist:
        project_path = settings.code_base_path / name
        if not project_path.exists():
            raise HTTPException(status_code=404, detail="Project not found")

    scan_manager.start_scan("single", total_projects=1)

    thread = threading.Thread(
        target=_run_scan_in_background,
        args=("single", None, name, regenerate_metadata),
        daemon=True
    )
    thread.start()

    return {"message": f"Scan started for {name}", "scan_id": scan_manager.current_scan.scan_id}


@app.get("/api/admin/scan/status")
async def get_scan_status():
    """Get current scan status."""
    status = scan_manager.get_status()
    if status:
        return status
    return {"status": "idle", "message": "No scan in progress"}


@app.get("/api/admin/scan/logs")
async def get_scan_progress_logs(limit: int = Query(100, le=500)):
    """Get recent scan log messages."""
    return scan_manager.get_logs(limit)


@app.get("/api/admin/scan/stream")
async def scan_event_stream():
    """Server-Sent Events stream for live scan updates."""
    async def event_generator():
        queue = await scan_manager.subscribe()
        try:
            # Send initial status
            status = scan_manager.get_status()
            logs = scan_manager.get_logs(50)
            initial_data = json.dumps({"status": status, "logs": logs})
            yield f"data: {initial_data}\n\n"

            while True:
                try:
                    # Wait for updates with timeout
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(data)}\n\n"

                    # Check if scan completed
                    if data.get("status", {}).get("status") in ("completed", "error"):
                        # Send one more update then close
                        await asyncio.sleep(1)
                        break

                except asyncio.TimeoutError:
                    # Send keepalive
                    yield f": keepalive\n\n"

        except asyncio.CancelledError:
            pass
        finally:
            scan_manager.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/api/admin/scan-logs", response_model=List[ScanLogResponse])
async def get_scan_logs(limit: int = Query(20, le=100)):
    """Get recent scan logs."""
    logs = ScanLog.select().order_by(ScanLog.started_at.desc()).limit(limit)

    return [
        ScanLogResponse(
            id=log.id,
            scan_type=log.scan_type,
            started_at=log.started_at.isoformat(),
            completed_at=log.completed_at.isoformat() if log.completed_at else None,
            projects_scanned=log.projects_scanned,
            projects_changed=log.projects_changed,
            errors=json.loads(log.errors) if log.errors else [],
            triggered_by=log.triggered_by
        )
        for log in logs
    ]


@app.get("/api/admin/scheduler-status", response_model=SchedulerStatusResponse)
async def get_scheduler_status():
    """Get scheduler status and next scheduled scan time."""
    from code_hub.scheduler import get_scheduler, get_next_scan_time

    scheduler = get_scheduler()
    next_scan = get_next_scan_time()

    return SchedulerStatusResponse(
        running=scheduler is not None and scheduler.running,
        next_scan=next_scan.isoformat() if next_scan else None
    )


@app.get("/api/projects/{name}/loc-history", response_model=List[LOCHistoryEntry])
async def get_project_loc_history(name: str, limit: int = Query(100, le=500)):
    """Get LOC history for a project."""
    try:
        project = Project.get(Project.name == name)
    except Project.DoesNotExist:
        raise HTTPException(status_code=404, detail="Project not found")

    history = (
        LOCHistory
        .select()
        .where(LOCHistory.project == project)
        .order_by(LOCHistory.recorded_at.desc())
        .limit(limit)
    )

    return [
        LOCHistoryEntry(
            recorded_at=h.recorded_at.isoformat(),
            lines_of_code=h.lines_of_code,
            file_count=h.file_count
        )
        for h in history
    ]


# HTML Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page."""
    if templates:
        from peewee import fn

        # Get stats
        total_projects = Project.select().count()
        total_loc = Project.select(fn.SUM(Project.lines_of_code)).scalar() or 0

        # Recent projects (by scan date)
        recent = Project.select().order_by(Project.scanned_at.desc()).limit(10)

        # Top languages
        lang_results = (
            Project
            .select(Project.primary_language, fn.COUNT(Project.id).alias('count'))
            .where(Project.primary_language.is_null(False))
            .group_by(Project.primary_language)
            .order_by(fn.COUNT(Project.id).desc())
            .limit(10)
        )
        languages = [(r.primary_language, r.count) for r in lang_results]

        # Top keywords
        top_keywords = (
            Keyword
            .select()
            .where(Keyword.count > 0)
            .order_by(Keyword.count.desc())
            .limit(20)
        )

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "total_projects": total_projects,
                "total_loc": total_loc,
                "recent_projects": list(recent),
                "languages": languages,
                "top_keywords": list(top_keywords)
            }
        )

    return HTMLResponse("""
        <html>
        <head><title>Code Hub</title></head>
        <body>
            <h1>Code Hub</h1>
            <p>API available at <a href="/docs">/docs</a></p>
        </body>
        </html>
    """)


@app.get("/project/{name}", response_class=HTMLResponse)
async def project_page(request: Request, name: str):
    """Project detail page."""
    if templates:
        try:
            project = Project.get(Project.name == name)
            readme_html = render_markdown(project.readme_content) if project.readme_content else ""
            keywords = [pk.keyword.name for pk in project.project_keywords]

            # Get root directory files for inline file browser
            root_files = await list_project_files(name=name, dir="")

            # Check if README and USAGE files exist
            project_path = Path(project.path)
            readme_path = project_path / 'README.md'
            usage_path = project_path / 'USAGE.md'
            has_readme = readme_path.exists() and readme_path.stat().st_size > 50
            has_usage = usage_path.exists() and usage_path.stat().st_size > 50

            return templates.TemplateResponse(
                "project.html",
                {
                    "request": request,
                    "project": project,
                    "readme_html": readme_html,
                    "keywords": keywords,
                    "modules": list(project.modules),
                    "languages": project.get_languages(),
                    "frameworks": project.get_frameworks(),
                    "root_files": root_files,
                    "format_file_size": format_file_size,
                    "has_readme": has_readme,
                    "has_usage": has_usage
                }
            )
        except Project.DoesNotExist:
            raise HTTPException(status_code=404, detail="Project not found")

    return HTMLResponse(f"<h1>Project: {name}</h1>")


@app.get("/project/{name}/search", response_class=HTMLResponse)
async def project_deep_search_page(request: Request, name: str, q: str = ""):
    """Deep search results page for a project."""
    if templates:
        try:
            project = Project.get(Project.name == name)
        except Project.DoesNotExist:
            raise HTTPException(status_code=404, detail="Project not found")

        grouped_matches = []
        total_matches = 0

        if q:
            try:
                search_results = await deep_search_project(name=name, q=q, limit=100, context=2)
                total_matches = search_results.total_matches

                # Group matches by file
                file_groups = {}
                for match in search_results.matches:
                    if match.file_path not in file_groups:
                        file_groups[match.file_path] = {
                            "file_path": match.file_path,
                            "file_name": match.file_name,
                            "language": match.language,
                            "matches": []
                        }
                    file_groups[match.file_path]["matches"].append({
                        "line_number": match.line_number,
                        "line_content": match.line_content,
                        "context_before": match.context_before,
                        "context_after": match.context_after
                    })

                grouped_matches = list(file_groups.values())
            except HTTPException:
                pass
            except Exception as e:
                logger.error(f"Deep search page error: {e}")

        return templates.TemplateResponse(
            "deep_search.html",
            {
                "request": request,
                "project": project,
                "query": q,
                "grouped_matches": grouped_matches,
                "total_matches": total_matches
            }
        )

    return HTMLResponse(f"<h1>Search: {name}</h1>")


@app.get("/project/{name}/stats", response_class=HTMLResponse)
async def project_stats_page(request: Request, name: str):
    """Project statistics page with LOC history chart."""
    if templates:
        try:
            project = Project.get(Project.name == name)
        except Project.DoesNotExist:
            raise HTTPException(status_code=404, detail="Project not found")

        return templates.TemplateResponse(
            "project_stats.html",
            {
                "request": request,
                "project": project
            }
        )

    return HTMLResponse(f"<h1>Stats: {name}</h1>")


@app.get("/project/{name}/files", response_class=HTMLResponse)
@app.get("/project/{name}/files/{dir_path:path}", response_class=HTMLResponse)
async def project_files_page(request: Request, name: str, dir_path: str = ""):
    """File browser page for a project."""
    if templates:
        try:
            project = Project.get(Project.name == name)
        except Project.DoesNotExist:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get files from API
        files = await list_project_files(name=name, dir=dir_path)

        # Build breadcrumb
        breadcrumbs = [{"name": project.name, "path": ""}]
        if dir_path:
            parts = dir_path.split('/')
            current_path = ""
            for part in parts:
                current_path = f"{current_path}/{part}" if current_path else part
                breadcrumbs.append({"name": part, "path": current_path})

        return templates.TemplateResponse(
            "files.html",
            {
                "request": request,
                "project": project,
                "files": files,
                "current_dir": dir_path,
                "breadcrumbs": breadcrumbs,
                "format_file_size": format_file_size
            }
        )

    return HTMLResponse(f"<h1>Files: {name}</h1>")


@app.get("/project/{name}/view/{file_path:path}", response_class=HTMLResponse)
async def view_file_page(
    request: Request,
    name: str,
    file_path: str,
    line: Optional[int] = None,
    lines: Optional[str] = None  # Format: "start-end" e.g., "10-20"
):
    """View a file with syntax highlighting and optional line highlighting."""
    if templates:
        try:
            project = Project.get(Project.name == name)
        except Project.DoesNotExist:
            raise HTTPException(status_code=404, detail="Project not found")

        try:
            file_content = await get_file_content(name=name, file_path=file_path)
        except HTTPException as e:
            return templates.TemplateResponse(
                "file_view.html",
                {
                    "request": request,
                    "project": project,
                    "file_path": file_path,
                    "error": e.detail
                }
            )

        # Parse line highlighting
        highlight_lines = []
        if line:
            highlight_lines = [line]
        elif lines:
            try:
                parts = lines.split('-')
                if len(parts) == 2:
                    start, end = int(parts[0]), int(parts[1])
                    highlight_lines = list(range(start, end + 1))
            except ValueError:
                pass

        # Re-highlight with line highlighting if needed
        if highlight_lines:
            file_content.highlighted_html = highlight_code(
                file_content.content,
                file_content.language,
                file_content.name,
                hl_lines=highlight_lines
            )

        # Build breadcrumb
        breadcrumbs = [{"name": project.name, "path": ""}]
        parts = file_path.split('/')
        current_path = ""
        for i, part in enumerate(parts):
            current_path = f"{current_path}/{part}" if current_path else part
            is_file = i == len(parts) - 1
            breadcrumbs.append({
                "name": part,
                "path": current_path,
                "is_file": is_file
            })

        return templates.TemplateResponse(
            "file_view.html",
            {
                "request": request,
                "project": project,
                "file": file_content,
                "file_path": file_path,
                "breadcrumbs": breadcrumbs,
                "format_file_size": format_file_size,
                "highlight_lines": highlight_lines,
                "scroll_to_line": highlight_lines[0] if highlight_lines else None
            }
        )

    return HTMLResponse(f"<h1>View: {file_path}</h1>")


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", mode: str = "hybrid"):
    """Search page."""
    if templates:
        results = []
        if q:
            try:
                api_results = await search_projects(q=q, mode=mode, limit=20)
                results = api_results
            except Exception as e:
                logger.error(f"Search error: {e}")
                results = []

        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "query": q,
                "mode": mode,
                "results": results
            }
        )

    return HTMLResponse("<h1>Search</h1>")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Admin page for managing scans and viewing project status."""
    if templates:
        from code_hub.scheduler import get_scheduler, get_next_scan_time

        # Get changed projects
        try:
            changed_projects = await get_changed_projects()
        except Exception:
            changed_projects = []

        # Get recent scan logs
        logs = ScanLog.select().order_by(ScanLog.started_at.desc()).limit(10)
        scan_logs = [
            {
                "id": log.id,
                "scan_type": log.scan_type,
                "started_at": log.started_at,
                "completed_at": log.completed_at,
                "projects_scanned": log.projects_scanned,
                "projects_changed": log.projects_changed,
                "errors": json.loads(log.errors) if log.errors else [],
                "triggered_by": log.triggered_by
            }
            for log in logs
        ]

        # Get scheduler status
        scheduler = get_scheduler()
        next_scan = get_next_scan_time()

        # Get all projects for the scan dropdown
        all_projects = Project.select(Project.name).order_by(Project.name)

        return templates.TemplateResponse(
            "admin.html",
            {
                "request": request,
                "changed_projects": changed_projects,
                "scan_logs": scan_logs,
                "scheduler_running": scheduler is not None and scheduler.running,
                "next_scan": next_scan,
                "all_projects": [p.name for p in all_projects]
            }
        )

    return HTMLResponse("<h1>Admin</h1>")


@app.get("/browse", response_class=HTMLResponse)
async def browse_page(
    request: Request,
    language: Optional[str] = None,
    keyword: Optional[str] = None,
    sort: str = "name",
    page: int = 1
):
    """Browse projects page."""
    if templates:
        per_page = 50
        offset = (page - 1) * per_page

        query = Project.select()

        if language:
            query = query.where(Project.primary_language == language)

        if keyword:
            query = (
                query
                .join(ProjectKeyword)
                .join(Keyword)
                .where(Keyword.name == keyword.lower())
                .switch(Project)
            )

        total = query.count()

        # Apply sorting
        sort_options = {
            "name": Project.name,
            "recent": Project.last_code_modified_at.desc(),
            "created": Project.project_created_at.desc(),
            "loc": Project.lines_of_code.desc(),
            "files": Project.file_count.desc()
        }
        order_by = sort_options.get(sort, Project.name)
        projects = list(query.order_by(order_by).offset(offset).limit(per_page))
        total_pages = (total + per_page - 1) // per_page

        return templates.TemplateResponse(
            "browse.html",
            {
                "request": request,
                "projects": projects,
                "language": language,
                "keyword": keyword,
                "sort": sort,
                "page": page,
                "total_pages": total_pages,
                "total": total
            }
        )

    return HTMLResponse("<h1>Browse</h1>")


def run_server(host: str = None, port: int = None):
    """Run the server."""
    import uvicorn
    uvicorn.run(
        "server:app",
        host=host or settings.server_host,
        port=port or settings.server_port,
        reload=False
    )


if __name__ == "__main__":
    run_server()
