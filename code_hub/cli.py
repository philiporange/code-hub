#!/usr/bin/env python3
"""Command-line interface for Code Hub."""
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.panel import Panel

console = Console()


def setup_logging(verbose: bool = False):
    """Configure logging with rich output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)]
    )


@click.group()
@click.version_option(version="1.0.0")
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose/debug logging')
@click.pass_context
def cli(ctx, verbose):
    """Code Hub - Manage and search your code projects."""
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    setup_logging(verbose)


@cli.command()
@click.option('--path', '-p', type=click.Path(exists=True), help='Path to scan (default: ~/Code)')
@click.option('--save/--no-save', default=True, help='Save results to database')
def scan(path, save):
    """Scan directory for projects and save to database."""
    from code_hub.scanner import ProjectScanner
    from code_hub.generator import DocumentationGenerator
    from code_hub.models import create_tables, db

    if save:
        db.connect(reuse_if_open=True)
        create_tables()

    scanner = ProjectScanner(base_path=path if path else None)
    generator = DocumentationGenerator() if save else None

    console.print(f"[blue]Scanning:[/blue] {scanner.base_path}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        # First, discover all projects
        task = progress.add_task("Discovering projects...", total=None)
        projects = list(scanner.discover_projects())
        progress.update(task, total=len(projects), completed=0)

        scanned = []
        for i, proj_path in enumerate(projects):
            progress.update(task, description=f"Scanning {proj_path.name}...", completed=i)
            scanned_proj = scanner.scan_project(proj_path)
            scanned.append(scanned_proj)

            if save and generator:
                generator.save_to_database(scanned_proj)

            progress.advance(task)

    # Summary
    git_count = sum(1 for p in scanned if p.git.is_repo)
    total_loc = sum(p.stats.lines_of_code for p in scanned)

    console.print()
    console.print(Panel(
        f"[green]✓[/green] Found [bold]{len(projects)}[/bold] projects\n"
        f"  Git repos: {git_count}\n"
        f"  Total lines of code: {total_loc:,}",
        title="Scan Complete"
    ))


@cli.command()
@click.option('--project', '-p', help='Generate for specific project only')
@click.option('--with-readme', is_flag=True, help='Also generate README.md')
@click.option('--with-usage', is_flag=True, help='Also generate USAGE.md (slow)')
@click.option('--force', '-f', is_flag=True, help='Regenerate even if files exist')
@click.option('--all', 'generate_all', is_flag=True, help='Generate README, METADATA, and USAGE')
@click.option('--dry-run', is_flag=True, help='Show what would be generated without doing it')
def generate(project, with_readme, with_usage, force, generate_all, dry_run):
    """Generate METADATA.json for projects using Claude.

    By default, only generates METADATA.json (fast).
    Use --with-readme and --with-usage to generate additional docs.
    Use --all to generate everything.
    """
    from code_hub.scanner import ProjectScanner
    from code_hub.generator import DocumentationGenerator
    from code_hub.models import create_tables, db

    db.connect(reuse_if_open=True)
    create_tables()

    if generate_all:
        with_readme = True
        with_usage = True

    scanner = ProjectScanner()
    generator = DocumentationGenerator(
        skip_readme=not with_readme,
        skip_usage=not with_usage,
        force=force
    )

    if project:
        # Single project
        proj_path = scanner.base_path / project
        if not proj_path.exists():
            console.print(f"[red]Project not found:[/red] {project}")
            sys.exit(1)

        scanned = scanner.scan_project(proj_path)

        will_readme = generator.should_generate_readme(scanned)
        will_metadata = generator.should_generate_metadata(scanned)
        will_usage = generator.should_generate_usage(scanned)

        if dry_run:
            console.print(f"[blue]Would generate for {project}:[/blue]")
            readme_status = 'Yes' if will_readme else ('Skipped' if generator.skip_readme else 'Exists')
            usage_status = 'Yes' if will_usage else ('Skipped' if generator.skip_usage else 'Exists')
            metadata_status = 'Yes' if will_metadata else 'Exists'
            console.print(f"  METADATA.json: {metadata_status}")
            console.print(f"  README.md: {readme_status}")
            console.print(f"  USAGE.md: {usage_status}")
            return

        if not will_readme and not will_metadata and not will_usage:
            console.print(f"[yellow]Nothing to generate for {project}[/yellow] (use --force to regenerate)")
            return

        with console.status(f"Generating docs for {project}..."):
            result = generator.generate_for_project(scanned)
            generator.save_to_database(scanned)

        if result.readme_generated:
            console.print(f"[green]✓[/green] Generated README.md")
        elif result.readme_error:
            console.print(f"[red]✗[/red] README error: {result.readme_error}")

        if result.metadata_generated:
            console.print(f"[green]✓[/green] Generated METADATA.json")
        elif result.metadata_error:
            console.print(f"[red]✗[/red] METADATA error: {result.metadata_error}")

        if result.usage_generated:
            console.print(f"[green]✓[/green] Generated USAGE.md")
        elif result.usage_error:
            console.print(f"[red]✗[/red] USAGE error: {result.usage_error}")

    else:
        # All projects - scan first to get stats, then sort by lines of code (descending)
        project_paths = list(scanner.discover_projects())

        console.print(f"[blue]Scanning {len(project_paths)} projects...[/blue]")
        scanned_projects = []
        for proj_path in project_paths:
            scanned_projects.append(scanner.scan_project(proj_path))

        # Sort by lines of code descending (largest projects first)
        scanned_projects.sort(key=lambda p: p.stats.lines_of_code, reverse=True)

        if dry_run:
            need_metadata = sum(1 for s in scanned_projects if generator.should_generate_metadata(s))
            need_readme = sum(1 for s in scanned_projects if generator.should_generate_readme(s))
            need_usage = sum(1 for s in scanned_projects if generator.should_generate_usage(s))

            console.print(f"[blue]Would generate:[/blue]")
            console.print(f"  METADATA.json: {need_metadata} projects")
            if not generator.skip_readme:
                console.print(f"  README.md: {need_readme} projects")
            else:
                console.print(f"  README.md: [dim]skipped (use --with-readme)[/dim]")
            if not generator.skip_usage:
                console.print(f"  USAGE.md: {need_usage} projects")
            else:
                console.print(f"  USAGE.md: [dim]skipped (use --with-usage)[/dim]")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Generating documentation...", total=len(scanned_projects))

            success_readme = 0
            success_metadata = 0
            success_usage = 0
            errors = 0

            for scanned in scanned_projects:
                progress.update(task, description=f"Processing {scanned.name} ({scanned.stats.lines_of_code:,} LOC)...")

                result = generator.generate_for_project(scanned)
                generator.save_to_database(scanned)

                if result.readme_generated:
                    success_readme += 1
                if result.metadata_generated:
                    success_metadata += 1
                if result.usage_generated:
                    success_usage += 1
                if result.readme_error or result.metadata_error or result.usage_error:
                    errors += 1

                progress.advance(task)

        console.print()
        summary_lines = [f"[green]✓[/green] Generated METADATA: {success_metadata}"]
        if not generator.skip_readme:
            summary_lines.append(f"[green]✓[/green] Generated READMEs: {success_readme}")
        if not generator.skip_usage:
            summary_lines.append(f"[green]✓[/green] Generated USAGE: {success_usage}")
        if errors:
            summary_lines.append(f"[yellow]![/yellow] Errors: {errors}")
        console.print(Panel("\n".join(summary_lines), title="Generation Complete"))


@cli.command()
@click.option('--rebuild', '-r', is_flag=True, help='Rebuild all indexes from scratch')
def index(rebuild):
    """Build search indexes (FTS and vector)."""
    from code_hub.indexer import get_indexer
    from code_hub.models import create_tables, db, Project

    db.connect(reuse_if_open=True)
    create_tables()

    # Check if we have projects
    count = Project.select().count()
    if count == 0:
        console.print("[yellow]No projects in database. Run 'scan' first.[/yellow]")
        return

    indexer = get_indexer()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Indexing...", total=None)

        def on_progress(msg, current, total):
            progress.update(task, description=msg, completed=current, total=total)

        indexer.index_all(rebuild=rebuild, on_progress=on_progress)

    console.print("[green]✓[/green] Indexing complete")


@cli.command()
@click.argument('query')
@click.option('--semantic', '-s', is_flag=True, help='Use semantic (vector) search')
@click.option('--fts', is_flag=True, help='Use full-text search only')
@click.option('--limit', '-n', default=10, help='Number of results')
def search(query, semantic, fts, limit):
    """Search for projects."""
    from code_hub.indexer import get_indexer
    from code_hub.models import create_tables, db

    db.connect(reuse_if_open=True)
    create_tables()

    indexer = get_indexer()

    if fts:
        results = [(p, 1.0 - i / limit) for i, p in enumerate(indexer.search_fts(query, limit=limit))]
        mode = "Full-text"
    elif semantic:
        results = indexer.search_semantic(query, limit=limit)
        mode = "Semantic"
    else:
        results = indexer.search_hybrid(query, limit=limit)
        mode = "Hybrid"

    if not results:
        console.print("[yellow]No results found[/yellow]")
        return

    table = Table(title=f"{mode} search: {query}")
    table.add_column("Project", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Language", style="green")
    table.add_column("Score", justify="right")

    for project, score in results:
        desc = project.short_description or ""
        if len(desc) > 60:
            desc = desc[:57] + "..."
        table.add_row(
            project.name,
            desc,
            project.primary_language or "-",
            f"{score:.2f}"
        )

    console.print(table)


@cli.command()
@click.option('--port', '-p', default=8000, help='Server port')
@click.option('--host', '-h', default='0.0.0.0', help='Server host')
def serve(port, host):
    """Start the web server."""
    from code_hub.models import create_tables, db

    db.connect(reuse_if_open=True)
    create_tables()

    console.print(f"[green]Starting server at http://{host}:{port}[/green]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    import uvicorn
    uvicorn.run(
        "code_hub.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )


@cli.command()
def stats():
    """Show project statistics."""
    from code_hub.models import Project, Module, Keyword, create_tables, db
    from peewee import fn

    db.connect(reuse_if_open=True)
    create_tables()

    total_projects = Project.select().count()

    if total_projects == 0:
        console.print("[yellow]No projects in database. Run 'scan' first.[/yellow]")
        return

    total_modules = Module.select().count()
    total_keywords = Keyword.select().where(Keyword.count > 0).count()
    total_loc = Project.select(fn.SUM(Project.lines_of_code)).scalar() or 0
    git_repos = Project.select().where(Project.is_git_repo == True).count()
    with_readme = Project.select().where(Project.readme_content.is_null(False)).count()
    with_metadata = Project.select().where(Project.metadata_json.is_null(False)).count()

    # Main stats table
    table = Table(title="Code Hub Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total Projects", str(total_projects))
    table.add_row("Git Repositories", str(git_repos))
    table.add_row("Projects with README", str(with_readme))
    table.add_row("Projects with METADATA", str(with_metadata))
    table.add_row("Total Modules", str(total_modules))
    table.add_row("Total Keywords", str(total_keywords))
    table.add_row("Lines of Code", f"{total_loc:,}")

    console.print(table)
    console.print()

    # Top languages
    lang_results = (
        Project
        .select(Project.primary_language, fn.COUNT(Project.id).alias('count'))
        .where(Project.primary_language.is_null(False))
        .group_by(Project.primary_language)
        .order_by(fn.COUNT(Project.id).desc())
        .limit(10)
    )

    if lang_results:
        lang_table = Table(title="Top Languages")
        lang_table.add_column("Language", style="green")
        lang_table.add_column("Projects", justify="right")

        for r in lang_results:
            lang_table.add_row(r.primary_language, str(r.count))

        console.print(lang_table)
        console.print()

    # Top keywords
    top_keywords = (
        Keyword
        .select()
        .where(Keyword.count > 0)
        .order_by(Keyword.count.desc())
        .limit(15)
    )

    if top_keywords:
        kw_table = Table(title="Top Keywords")
        kw_table.add_column("Keyword", style="blue")
        kw_table.add_column("Projects", justify="right")

        for kw in top_keywords:
            kw_table.add_row(kw.name, str(kw.count))

        console.print(kw_table)


@cli.command()
@click.argument('project_name')
def show(project_name):
    """Show details for a specific project."""
    from code_hub.models import Project, create_tables, db

    db.connect(reuse_if_open=True)
    create_tables()

    try:
        project = Project.get(Project.name == project_name)
    except Project.DoesNotExist:
        console.print(f"[red]Project not found:[/red] {project_name}")
        sys.exit(1)

    # Basic info
    console.print(Panel(
        f"[bold]{project.name}[/bold]\n"
        f"{project.short_description or '[No description]'}",
        title="Project"
    ))

    # Details table
    table = Table(show_header=False, box=None)
    table.add_column("Key", style="dim")
    table.add_column("Value")

    table.add_row("Path", project.path)
    table.add_row("Language", project.primary_language or "-")
    table.add_row("Languages", ", ".join(project.get_languages()) or "-")
    table.add_row("Frameworks", ", ".join(project.get_frameworks()) or "-")
    table.add_row("Files", str(project.file_count))
    table.add_row("Lines of Code", f"{project.lines_of_code:,}")
    table.add_row("Git Repo", "Yes" if project.is_git_repo else "No")

    if project.github_name:
        table.add_row("GitHub", f"https://github.com/{project.github_name}")

    console.print(table)
    console.print()

    # Keywords
    keywords = [pk.keyword.name for pk in project.project_keywords]
    if keywords:
        console.print("[bold]Keywords:[/bold]", ", ".join(keywords))
        console.print()

    # Modules
    modules = list(project.modules)
    if modules:
        mod_table = Table(title="Modules")
        mod_table.add_column("Name", style="cyan")
        mod_table.add_column("Path")
        mod_table.add_column("Description")

        for mod in modules[:15]:
            desc = mod.description or ""
            if len(desc) > 50:
                desc = desc[:47] + "..."
            mod_table.add_row(mod.name, mod.path, desc)

        console.print(mod_table)


@cli.command()
@click.confirmation_option(prompt='Are you sure you want to reset the database?')
def reset():
    """Reset the database (delete all data)."""
    from code_hub.models import reset_database, db
    from code_hub.vectorstore import reset_vector_store

    db.connect(reuse_if_open=True)
    reset_database()
    reset_vector_store()

    console.print("[green]✓[/green] Database reset complete")


if __name__ == "__main__":
    cli()
