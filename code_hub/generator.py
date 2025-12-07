"""Documentation generation orchestrator."""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, List
from dataclasses import dataclass
import logging

from code_hub.claude_wrapper import ClaudeWrapper, ClaudeResponse
from code_hub.scanner import ProjectScanner, ScannedProject
from code_hub.models import Project, Module, ProjectFile, Keyword, ProjectKeyword, Dependency, db
from code_hub.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """Result of documentation generation."""
    project_name: str
    readme_generated: bool = False
    metadata_generated: bool = False
    usage_generated: bool = False
    readme_error: Optional[str] = None
    metadata_error: Optional[str] = None
    usage_error: Optional[str] = None
    duration: float = 0.0


class DocumentationGenerator:
    """Generates README, METADATA, and USAGE for projects using Claude."""

    def __init__(
        self,
        claude: Optional[ClaudeWrapper] = None,
        skip_readme: bool = True,
        skip_usage: bool = True,
        force: bool = False,
    ):
        self.claude = claude or ClaudeWrapper()
        self.skip_readme = skip_readme
        self.skip_usage = skip_usage
        self.force = force

    def should_generate_readme(self, project: ScannedProject) -> bool:
        """Check if README should be generated."""
        if self.skip_readme:
            return False
        if self.force:
            return True
        # Generate if no README exists or it's very short
        if not project.existing_readme:
            return True
        if len(project.existing_readme.strip()) < 50:
            return True
        return False

    def should_generate_metadata(self, project: ScannedProject) -> bool:
        """Check if METADATA should be generated."""
        if self.force:
            return True
        return project.existing_metadata is None

    def should_generate_usage(self, project: ScannedProject) -> bool:
        """Check if USAGE.md should be generated."""
        if self.skip_usage:
            return False
        if self.force:
            return True
        usage_path = project.path / 'USAGE.md'
        return not usage_path.exists()

    def generate_for_project(
        self,
        project: ScannedProject,
        on_progress: Optional[Callable[[str], None]] = None
    ) -> GenerationResult:
        """Generate documentation for a single project."""
        result = GenerationResult(project_name=project.name)
        start_time = datetime.now()

        logger.info(f"=== Processing {project.name} ({project.stats.lines_of_code:,} LOC) ===")

        # Generate README if needed
        if self.should_generate_readme(project):
            if on_progress:
                on_progress(f"Generating README for {project.name}...")

            logger.info(f"[1/3] README: generating...")
            response = self.claude.generate_readme(project.path)

            if response.success and response.content.strip():
                readme_path = project.path / 'README.md'
                try:
                    # Clean up the response - remove any JSON wrapper if present
                    content = self._clean_markdown_content(response.content)
                    readme_path.write_text(content)
                    result.readme_generated = True
                    logger.info(f"[1/3] README: saved ({len(content)} chars)")
                except IOError as e:
                    result.readme_error = str(e)
                    logger.error(f"[1/3] README: write failed - {e}")
            else:
                result.readme_error = response.error or "Empty response"
                logger.warning(f"[1/3] README: generation failed - {result.readme_error}")
        else:
            logger.info(f"[1/3] README: skipped (already exists)")

        # Generate METADATA if needed
        if self.should_generate_metadata(project):
            if on_progress:
                on_progress(f"Generating METADATA for {project.name}...")

            logger.info(f"[2/3] METADATA: generating...")
            response = self.claude.generate_metadata(project.path)

            if response.success and response.content.strip():
                metadata_path = project.path / 'METADATA.json'
                try:
                    # Parse and enhance metadata
                    metadata = self._parse_metadata(response.content, project)
                    metadata_path.write_text(json.dumps(metadata, indent=2))
                    result.metadata_generated = True
                    logger.info(f"[2/3] METADATA: saved")
                except (json.JSONDecodeError, IOError, ValueError) as e:
                    result.metadata_error = str(e)
                    logger.error(f"[2/3] METADATA: parse/write failed - {e}")
            else:
                result.metadata_error = response.error or "Empty response"
                logger.warning(f"[2/3] METADATA: generation failed - {result.metadata_error}")
        else:
            logger.info(f"[2/3] METADATA: skipped (already exists)")

        # Generate USAGE.md if needed
        if self.should_generate_usage(project):
            if on_progress:
                on_progress(f"Generating USAGE.md for {project.name}...")

            logger.info(f"[3/3] USAGE: generating...")
            response = self.claude.generate_usage(project.path)

            if response.success and response.content.strip():
                usage_path = project.path / 'USAGE.md'
                try:
                    content = self._clean_markdown_content(response.content)
                    usage_path.write_text(content)
                    result.usage_generated = True
                    logger.info(f"[3/3] USAGE: saved ({len(content)} chars)")
                except IOError as e:
                    result.usage_error = str(e)
                    logger.error(f"[3/3] USAGE: write failed - {e}")
            else:
                result.usage_error = response.error or "Empty response"
                logger.warning(f"[3/3] USAGE: generation failed - {result.usage_error}")
        else:
            logger.info(f"[3/3] USAGE: skipped (already exists)")

        result.duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"=== Completed {project.name} in {result.duration:.1f}s ===")
        return result

    def _clean_markdown_content(self, content: str) -> str:
        """Clean up markdown content from Claude response."""
        # Try to extract from JSON if wrapped
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                if 'result' in data:
                    content = data['result']
                elif 'content' in data:
                    content = data['content']
                elif 'readme' in data:
                    content = data['readme']
                elif 'usage' in data:
                    content = data['usage']
        except json.JSONDecodeError:
            pass

        # Remove markdown code fences if the whole thing is wrapped
        content = content.strip()
        if content.startswith('```markdown'):
            content = content[11:]
        elif content.startswith('```md'):
            content = content[5:]
        elif content.startswith('```'):
            content = content[3:]

        if content.endswith('```'):
            content = content[:-3]

        return content.strip()

    def _parse_metadata(self, content: str, project: ScannedProject) -> dict:
        """Parse and enhance metadata from Claude response."""
        # Try to parse JSON
        try:
            metadata = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    metadata = json.loads(match.group())
                except json.JSONDecodeError:
                    raise ValueError(f"Could not parse metadata JSON: {content[:200]}")
            else:
                raise ValueError(f"No JSON found in response: {content[:200]}")

        # Handle nested result structure
        if 'result' in metadata and isinstance(metadata['result'], dict):
            metadata = metadata['result']

        # Ensure required fields exist
        if 'name' not in metadata:
            metadata['name'] = project.name
        if 'short_description' not in metadata:
            metadata['short_description'] = ""
        if 'keywords' not in metadata:
            metadata['keywords'] = []
        if 'primary_language' not in metadata:
            metadata['primary_language'] = project.languages[0] if project.languages else None
        if 'languages' not in metadata:
            metadata['languages'] = project.languages

        # Normalize keywords to lowercase
        metadata['keywords'] = [k.lower().strip() for k in metadata.get('keywords', [])]

        # Add git info
        metadata['git'] = {
            'is_repo': project.git.is_repo,
            'remote_url': project.git.remote_url,
            'github_name': project.git.github_name,
            'default_branch': project.git.default_branch,
            'last_commit': project.git.last_commit_at.isoformat() if project.git.last_commit_at else None
        }

        # Add stats
        metadata['stats'] = {
            'files': project.stats.file_count,
            'lines_of_code': project.stats.lines_of_code,
            'size_bytes': project.stats.size_bytes
        }

        # Add generation info
        metadata['generated_at'] = datetime.now().isoformat()
        metadata['generator_version'] = '1.0.0'

        return metadata

    def save_to_database(self, project: ScannedProject) -> Project:
        """Save or update project in database."""
        # Read current files
        readme_content = None
        readme_path = project.path / 'README.md'
        if readme_path.exists():
            try:
                readme_content = readme_path.read_text(errors='ignore')
            except IOError:
                pass

        metadata = None
        metadata_path = project.path / 'METADATA.json'
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text())
            except (json.JSONDecodeError, IOError):
                pass

        with db.atomic():
            # Create or update project
            db_project, created = Project.get_or_create(
                name=project.name,
                defaults={'path': str(project.path)}
            )

            # Update fields
            db_project.path = str(project.path)
            db_project.is_git_repo = project.git.is_repo
            db_project.git_remote_url = project.git.remote_url
            db_project.github_name = project.git.github_name
            db_project.default_branch = project.git.default_branch
            db_project.last_commit_at = project.git.last_commit_at
            db_project.file_count = project.stats.file_count
            db_project.lines_of_code = project.stats.lines_of_code
            db_project.size_bytes = project.stats.size_bytes
            db_project.readme_content = readme_content
            db_project.scanned_at = datetime.now()

            # Project activity dates
            db_project.project_created_at = project.project_created_at
            db_project.last_code_modified_at = project.last_code_modified_at

            # Save files
            ProjectFile.delete().where(ProjectFile.project == db_project).execute()
            for file_info in project.files:
                ProjectFile.create(
                    project=db_project,
                    path=file_info.path,
                    name=file_info.name,
                    is_directory=file_info.is_directory,
                    size_bytes=file_info.size_bytes,
                    modified_at=file_info.modified_at,
                    language=file_info.language
                )

            if metadata:
                db_project.short_description = metadata.get('short_description', '')
                db_project.long_description = metadata.get('long_description', '')
                db_project.primary_language = metadata.get('primary_language')
                db_project.set_languages(metadata.get('languages', project.languages))
                db_project.set_frameworks(metadata.get('frameworks', []))
                db_project.metadata_json = json.dumps(metadata)
                db_project.generated_at = datetime.now()

                # Save modules (deduplicate by path)
                Module.delete().where(Module.project == db_project).execute()
                seen_paths = set()
                for mod in metadata.get('modules', []):
                    if isinstance(mod, dict) and 'name' in mod and 'path' in mod:
                        mod_path = mod['path']
                        if mod_path not in seen_paths:
                            seen_paths.add(mod_path)
                            Module.create(
                                project=db_project,
                                name=mod['name'],
                                path=mod_path,
                                description=mod.get('description', '')
                            )

                # Save keywords
                ProjectKeyword.delete().where(
                    ProjectKeyword.project == db_project
                ).execute()

                for kw_name in metadata.get('keywords', []):
                    kw_name = kw_name.lower().strip()
                    if kw_name:
                        keyword, _ = Keyword.get_or_create(name=kw_name)
                        ProjectKeyword.get_or_create(project=db_project, keyword=keyword)

                # Update keyword counts
                for kw in Keyword.select():
                    kw.count = ProjectKeyword.select().where(
                        ProjectKeyword.keyword == kw
                    ).count()
                    kw.save()
            else:
                db_project.set_languages(project.languages)

            db_project.save()
            return db_project


def generate_all(
    force_readme: bool = False,
    force_metadata: bool = False,
    force_usage: bool = False,
    on_progress: Optional[Callable[[str, int, int], None]] = None
) -> List[GenerationResult]:
    """Generate documentation for all projects."""
    scanner = ProjectScanner()
    generator = DocumentationGenerator(
        force_readme=force_readme,
        force_metadata=force_metadata,
        force_usage=force_usage
    )
    results = []

    projects = list(scanner.discover_projects())
    total = len(projects)

    for i, path in enumerate(projects):
        if on_progress:
            on_progress(f"Processing {path.name}", i + 1, total)

        scanned = scanner.scan_project(path)
        result = generator.generate_for_project(scanned)
        generator.save_to_database(scanned)
        results.append(result)

    return results
