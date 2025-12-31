"""Project discovery and analysis.

Discovers projects in ~/Code directory by looking for project markers (git repos,
package files, etc). Scans projects to extract stats, languages, git info, and files.
Supports incremental scanning by detecting which projects have changed since last scan.
Records LOC history for tracking code growth over time.
"""
import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterator
from fnmatch import fnmatch

# tomllib is Python 3.11+, fallback to tomli for older versions
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

from code_hub.config import settings


@dataclass
class GitInfo:
    """Git repository information."""
    is_repo: bool = False
    remote_url: Optional[str] = None
    github_name: Optional[str] = None
    default_branch: Optional[str] = None
    last_commit_at: Optional[datetime] = None


@dataclass
class ProjectStats:
    """Project statistics."""
    file_count: int = 0
    lines_of_code: int = 0
    size_bytes: int = 0


@dataclass
class FileInfo:
    """Information about a file in a project."""
    path: str  # Relative path within project
    name: str  # File name only
    is_directory: bool = False
    size_bytes: int = 0
    modified_at: Optional[datetime] = None
    language: Optional[str] = None


@dataclass
class ScannedProject:
    """Result of scanning a project directory."""
    name: str
    path: Path
    git: GitInfo = field(default_factory=GitInfo)
    stats: ProjectStats = field(default_factory=ProjectStats)
    languages: List[str] = field(default_factory=list)
    existing_readme: Optional[str] = None
    existing_metadata: Optional[Dict[str, Any]] = None
    package_info: Dict[str, Any] = field(default_factory=dict)
    files: List[FileInfo] = field(default_factory=list)
    project_created_at: Optional[datetime] = None
    last_code_modified_at: Optional[datetime] = None


class ProjectScanner:
    """Discovers and analyzes projects in a directory."""

    # File patterns that indicate a project root
    PROJECT_MARKERS = [
        '.git', 'package.json', 'pyproject.toml', 'setup.py', 'setup.cfg',
        'Cargo.toml', 'go.mod', 'Makefile', 'CMakeLists.txt',
        'pom.xml', 'build.gradle', 'Gemfile', 'composer.json',
        'mix.exs', 'deno.json', 'bun.lockb', 'gleam.toml'
    ]

    # Language detection by file extension
    LANGUAGE_EXTENSIONS = {
        '.py': 'python',
        '.pyw': 'python',
        '.pyi': 'python',
        '.js': 'javascript',
        '.mjs': 'javascript',
        '.cjs': 'javascript',
        '.ts': 'typescript',
        '.mts': 'typescript',
        '.cts': 'typescript',
        '.jsx': 'javascript',
        '.tsx': 'typescript',
        '.rs': 'rust',
        '.go': 'go',
        '.java': 'java',
        '.rb': 'ruby',
        '.php': 'php',
        '.c': 'c',
        '.cpp': 'cpp',
        '.cc': 'cpp',
        '.cxx': 'cpp',
        '.h': 'c',
        '.hpp': 'cpp',
        '.hxx': 'cpp',
        '.cs': 'csharp',
        '.swift': 'swift',
        '.kt': 'kotlin',
        '.kts': 'kotlin',
        '.scala': 'scala',
        '.sh': 'shell',
        '.bash': 'shell',
        '.zsh': 'shell',
        '.fish': 'shell',
        '.lua': 'lua',
        '.r': 'r',
        '.R': 'r',
        '.jl': 'julia',
        '.ex': 'elixir',
        '.exs': 'elixir',
        '.erl': 'erlang',
        '.hs': 'haskell',
        '.ml': 'ocaml',
        '.mli': 'ocaml',
        '.clj': 'clojure',
        '.cljs': 'clojure',
        '.cljc': 'clojure',
        '.elm': 'elm',
        '.vue': 'vue',
        '.svelte': 'svelte',
        '.dart': 'dart',
        '.nim': 'nim',
        '.zig': 'zig',
        '.v': 'v',
        '.cr': 'crystal',
        '.pl': 'perl',
        '.pm': 'perl',
        '.raku': 'raku',
        '.fs': 'fsharp',
        '.fsx': 'fsharp',
        '.gleam': 'gleam',
    }

    def __init__(self, base_path: Path = None, exclude_patterns: List[str] = None):
        self.base_path = Path(base_path or settings.code_base_path).expanduser().resolve()
        self.exclude_patterns = exclude_patterns or settings.exclude_dirs

    def _should_exclude(self, name: str) -> bool:
        """Check if a directory name should be excluded."""
        for pattern in self.exclude_patterns:
            if fnmatch(name, pattern):
                return True
        # Also exclude hidden directories (except .git which we check for)
        if name.startswith('.') and name != '.git':
            return True
        return False

    def discover_projects(self) -> Iterator[Path]:
        """Discover all project directories."""
        if not self.base_path.exists():
            return

        for entry in sorted(self.base_path.iterdir()):
            if not entry.is_dir():
                continue
            if self._should_exclude(entry.name):
                continue

            # Check if it looks like a project
            if self._is_project(entry):
                yield entry

    def _is_project(self, path: Path) -> bool:
        """Check if a directory is a project."""
        # Check for project markers
        for marker in self.PROJECT_MARKERS:
            if (path / marker).exists():
                return True

        # Check if it has source files (not just in subdirs)
        for ext in self.LANGUAGE_EXTENSIONS:
            try:
                # Check top-level and one level deep
                if list(path.glob(f'*{ext}')) or list(path.glob(f'*/*{ext}')):
                    return True
            except PermissionError:
                continue

        return False

    def scan_project(self, path: Path) -> ScannedProject:
        """Perform detailed scan of a project."""
        project = ScannedProject(
            name=path.name,
            path=path
        )

        # Git info
        project.git = self._get_git_info(path)

        # Stats and languages
        project.stats, project.languages = self._analyze_files(path)

        # Collect files and project dates
        project.files, project.project_created_at, project.last_code_modified_at = \
            self._collect_files(path)

        # Existing docs
        project.existing_readme = self._read_readme(path)
        project.existing_metadata = self._read_metadata(path)

        # Package info
        project.package_info = self._read_package_info(path)

        return project

    def _get_git_info(self, path: Path) -> GitInfo:
        """Extract Git repository information."""
        git_dir = path / '.git'
        if not git_dir.exists():
            return GitInfo(is_repo=False)

        info = GitInfo(is_repo=True)

        try:
            # Get remote URL
            result = subprocess.run(
                ['git', 'remote', 'get-url', 'origin'],
                cwd=path, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                info.remote_url = result.stdout.strip()
                # Extract GitHub name
                url = info.remote_url
                if 'github.com' in url:
                    # Handle both HTTPS and SSH URLs
                    if url.startswith('git@'):
                        # git@github.com:user/repo.git
                        parts = url.split(':')[1].replace('.git', '')
                    elif 'github.com/' in url:
                        # https://github.com/user/repo.git
                        parts = url.split('github.com/')[1].replace('.git', '')
                    else:
                        parts = None
                    if parts:
                        info.github_name = parts

            # Get default branch
            result = subprocess.run(
                ['git', 'symbolic-ref', '--short', 'HEAD'],
                cwd=path, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                info.default_branch = result.stdout.strip()

            # Get last commit time
            result = subprocess.run(
                ['git', 'log', '-1', '--format=%cI'],
                cwd=path, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                try:
                    # Handle timezone format variations
                    timestamp = result.stdout.strip()
                    info.last_commit_at = datetime.fromisoformat(
                        timestamp.replace('Z', '+00:00')
                    )
                except ValueError:
                    pass

        except (subprocess.TimeoutExpired, Exception):
            pass

        return info

    def _analyze_files(self, path: Path) -> tuple[ProjectStats, List[str]]:
        """Analyze files in project."""
        stats = ProjectStats()
        language_counts: Dict[str, int] = {}

        try:
            for root, dirs, files in os.walk(path):
                # Filter excluded directories in-place
                dirs[:] = [d for d in dirs if not self._should_exclude(d)]

                for file in files:
                    file_path = Path(root) / file

                    try:
                        stat = file_path.stat()
                        stats.file_count += 1
                        stats.size_bytes += stat.st_size

                        # Count lines for code files
                        ext = file_path.suffix.lower()
                        if ext in self.LANGUAGE_EXTENSIONS:
                            lang = self.LANGUAGE_EXTENSIONS[ext]
                            try:
                                with open(file_path, 'r', errors='ignore') as f:
                                    lines = sum(1 for _ in f)
                                stats.lines_of_code += lines
                                language_counts[lang] = language_counts.get(lang, 0) + lines
                            except (IOError, OSError):
                                pass
                    except (OSError, IOError):
                        pass
        except PermissionError:
            pass

        # Sort languages by line count
        languages = sorted(language_counts.keys(), key=lambda x: -language_counts[x])

        return stats, languages

    # Files to exclude when calculating last_code_modified_at
    GENERATED_FILES = {'METADATA.json', 'README.md', 'USAGE.md', 'readme.md'}

    def _collect_files(self, path: Path) -> tuple[List[FileInfo], Optional[datetime], Optional[datetime]]:
        """Collect all files with metadata and calculate project dates."""
        files: List[FileInfo] = []
        earliest_date: Optional[datetime] = None
        latest_code_date: Optional[datetime] = None

        try:
            for root, dirs, filenames in os.walk(path):
                # Filter excluded directories in-place
                dirs[:] = [d for d in dirs if not self._should_exclude(d)]

                rel_root = Path(root).relative_to(path)

                for filename in filenames:
                    # Skip hidden files and macOS metadata
                    if filename.startswith('.') or filename.startswith('._'):
                        continue

                    file_path = Path(root) / filename
                    rel_path = str(rel_root / filename) if str(rel_root) != '.' else filename

                    try:
                        stat = file_path.stat()
                        modified_at = datetime.fromtimestamp(stat.st_mtime)

                        # Detect language
                        ext = file_path.suffix.lower()
                        language = self.LANGUAGE_EXTENSIONS.get(ext)

                        files.append(FileInfo(
                            path=rel_path,
                            name=filename,
                            is_directory=False,
                            size_bytes=stat.st_size,
                            modified_at=modified_at,
                            language=language
                        ))

                        # Track earliest modification (proxy for creation date)
                        if earliest_date is None or modified_at < earliest_date:
                            earliest_date = modified_at

                        # Track latest code file modification (excluding generated files)
                        if filename not in self.GENERATED_FILES:
                            if language or ext in {'.json', '.yaml', '.yml', '.toml', '.xml', '.html', '.css', '.sql'}:
                                if latest_code_date is None or modified_at > latest_code_date:
                                    latest_code_date = modified_at

                    except (OSError, IOError):
                        continue

        except PermissionError:
            pass

        # Sort files by path for consistent ordering
        files.sort(key=lambda f: f.path.lower())

        return files, earliest_date, latest_code_date

    def _read_readme(self, path: Path) -> Optional[str]:
        """Read existing README if present."""
        for name in ['README.md', 'README.rst', 'README.txt', 'README', 'readme.md']:
            readme_path = path / name
            if readme_path.exists():
                try:
                    return readme_path.read_text(errors='ignore')
                except (IOError, OSError):
                    pass
        return None

    def _read_metadata(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read existing METADATA.json if present."""
        metadata_path = path / 'METADATA.json'
        if metadata_path.exists():
            try:
                return json.loads(metadata_path.read_text())
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def _read_package_info(self, path: Path) -> Dict[str, Any]:
        """Read package configuration files."""
        info = {}

        # package.json (Node.js)
        pkg_json = path / 'package.json'
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text())
                info['npm'] = {
                    'name': data.get('name'),
                    'version': data.get('version'),
                    'description': data.get('description'),
                    'dependencies': list(data.get('dependencies', {}).keys()),
                    'devDependencies': list(data.get('devDependencies', {}).keys())
                }
            except (json.JSONDecodeError, IOError):
                pass

        # pyproject.toml (Python)
        pyproject = path / 'pyproject.toml'
        if pyproject.exists():
            try:
                with open(pyproject, 'rb') as f:
                    data = tomllib.load(f)
                project = data.get('project', {})
                poetry = data.get('tool', {}).get('poetry', {})
                # Merge project and poetry configs
                merged = {**poetry, **project}
                if merged:
                    deps = merged.get('dependencies', {})
                    if isinstance(deps, dict):
                        dep_list = list(deps.keys())
                    elif isinstance(deps, list):
                        dep_list = deps
                    else:
                        dep_list = []
                    info['python'] = {
                        'name': merged.get('name'),
                        'version': merged.get('version'),
                        'description': merged.get('description'),
                        'dependencies': dep_list
                    }
            except Exception:
                pass

        # setup.py fallback - just check if it exists
        setup_py = path / 'setup.py'
        if setup_py.exists() and 'python' not in info:
            info['python'] = {'name': path.name, 'has_setup_py': True}

        # Cargo.toml (Rust)
        cargo = path / 'Cargo.toml'
        if cargo.exists():
            try:
                with open(cargo, 'rb') as f:
                    data = tomllib.load(f)
                pkg = data.get('package', {})
                info['cargo'] = {
                    'name': pkg.get('name'),
                    'version': pkg.get('version'),
                    'description': pkg.get('description')
                }
            except Exception:
                pass

        # go.mod (Go)
        go_mod = path / 'go.mod'
        if go_mod.exists():
            try:
                content = go_mod.read_text()
                # Parse module name from first line
                for line in content.splitlines():
                    if line.startswith('module '):
                        info['go'] = {'module': line.split()[1]}
                        break
            except Exception:
                pass

        # Gemfile (Ruby)
        gemfile = path / 'Gemfile'
        if gemfile.exists():
            info['ruby'] = {'has_gemfile': True}

        # composer.json (PHP)
        composer = path / 'composer.json'
        if composer.exists():
            try:
                data = json.loads(composer.read_text())
                info['composer'] = {
                    'name': data.get('name'),
                    'description': data.get('description')
                }
            except Exception:
                pass

        return info


def scan_all_projects(base_path: Path = None) -> List[ScannedProject]:
    """Convenience function to scan all projects."""
    scanner = ProjectScanner(base_path=base_path)
    projects = []
    for path in scanner.discover_projects():
        projects.append(scanner.scan_project(path))
    return projects


def get_changed_projects(base_path: Path = None) -> List[tuple[Path, datetime]]:
    """Find projects that have changed since their last scan.

    Returns list of (project_path, last_modified) tuples for projects
    where filesystem mtime is newer than the database scanned_at timestamp.
    """
    from code_hub.models import Project, db

    scanner = ProjectScanner(base_path=base_path)
    changed = []

    with db:
        # Get all known projects with their scan times
        known_projects = {p.path: p.scanned_at for p in Project.select(Project.path, Project.scanned_at)}

    for project_path in scanner.discover_projects():
        path_str = str(project_path)
        scanned_at = known_projects.get(path_str)

        # Get latest modification time from the project directory
        latest_mtime = _get_project_mtime(project_path, scanner.exclude_patterns)

        if latest_mtime is None:
            continue

        # Project is changed if:
        # 1. Never scanned before (not in DB)
        # 2. Files modified after last scan
        if scanned_at is None or latest_mtime > scanned_at:
            changed.append((project_path, latest_mtime))

    return changed


def _get_project_mtime(path: Path, exclude_patterns: List[str]) -> Optional[datetime]:
    """Get the most recent modification time of files in a project."""
    from fnmatch import fnmatch

    latest: Optional[datetime] = None

    def should_exclude(name: str) -> bool:
        for pattern in exclude_patterns:
            if fnmatch(name, pattern):
                return True
        if name.startswith('.') and name != '.git':
            return True
        return False

    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not should_exclude(d)]

            for filename in files:
                if filename.startswith('.') or filename.startswith('._'):
                    continue
                # Skip generated files that we create
                if filename in {'METADATA.json', 'README.md', 'USAGE.md'}:
                    continue

                try:
                    file_path = Path(root) / filename
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if latest is None or mtime > latest:
                        latest = mtime
                except (OSError, IOError):
                    continue
    except PermissionError:
        pass

    return latest


def record_loc_history(project_name: str) -> None:
    """Record current LOC stats for a project in history table."""
    from code_hub.models import Project, LOCHistory, db

    with db:
        try:
            project = Project.get(Project.name == project_name)
            LOCHistory.create(
                project=project,
                lines_of_code=project.lines_of_code,
                file_count=project.file_count
            )
        except Project.DoesNotExist:
            pass


def scan_changed_projects(base_path: Path = None, triggered_by: str = "manual") -> dict:
    """Scan only projects that have changed since last scan.

    Returns a summary dict with scan statistics.
    """
    import json
    from code_hub.models import Project, LOCHistory, ScanLog, db, create_tables

    create_tables()

    scanner = ProjectScanner(base_path=base_path)
    changed = get_changed_projects(base_path)

    # Create scan log entry
    scan_log = ScanLog.create(
        scan_type='incremental',
        triggered_by=triggered_by
    )

    errors = []
    scanned_count = 0

    for project_path, last_modified in changed:
        try:
            scanned = scanner.scan_project(project_path)
            path_str = str(project_path)

            with db.atomic():
                # Check if project exists
                try:
                    project = Project.get(Project.path == path_str)
                    old_loc = project.lines_of_code

                    # Update existing project
                    project.file_count = scanned.stats.file_count
                    project.lines_of_code = scanned.stats.lines_of_code
                    project.size_bytes = scanned.stats.size_bytes
                    project.set_languages(scanned.languages)
                    if scanned.languages:
                        project.primary_language = scanned.languages[0]
                    project.is_git_repo = scanned.git.is_repo
                    project.git_remote_url = scanned.git.remote_url
                    project.github_name = scanned.git.github_name
                    project.default_branch = scanned.git.default_branch
                    project.last_commit_at = scanned.git.last_commit_at
                    project.project_created_at = scanned.project_created_at
                    project.last_code_modified_at = scanned.last_code_modified_at
                    project.scanned_at = datetime.now()
                    project.save()

                    # Record LOC history if changed
                    if project.lines_of_code != old_loc:
                        LOCHistory.create(
                            project=project,
                            lines_of_code=project.lines_of_code,
                            file_count=project.file_count
                        )

                except Project.DoesNotExist:
                    # Create new project
                    project = Project.create(
                        name=scanned.name,
                        path=path_str,
                        file_count=scanned.stats.file_count,
                        lines_of_code=scanned.stats.lines_of_code,
                        size_bytes=scanned.stats.size_bytes,
                        languages=json.dumps(scanned.languages),
                        primary_language=scanned.languages[0] if scanned.languages else None,
                        is_git_repo=scanned.git.is_repo,
                        git_remote_url=scanned.git.remote_url,
                        github_name=scanned.git.github_name,
                        default_branch=scanned.git.default_branch,
                        last_commit_at=scanned.git.last_commit_at,
                        project_created_at=scanned.project_created_at,
                        last_code_modified_at=scanned.last_code_modified_at,
                        scanned_at=datetime.now()
                    )

                    # Record initial LOC
                    LOCHistory.create(
                        project=project,
                        lines_of_code=project.lines_of_code,
                        file_count=project.file_count
                    )

            scanned_count += 1

        except Exception as e:
            errors.append(f"{project_path.name}: {str(e)}")

    # Update scan log
    scan_log.completed_at = datetime.now()
    scan_log.projects_scanned = scanned_count
    scan_log.projects_changed = len(changed)
    scan_log.errors = json.dumps(errors)
    scan_log.save()

    return {
        "scan_type": "incremental",
        "projects_found": len(changed),
        "projects_scanned": scanned_count,
        "errors": errors,
        "triggered_by": triggered_by
    }
