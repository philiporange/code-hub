#!/usr/bin/env python3
"""
Code Hub Runner

Manages the Code Hub server and background indexing processes.

Usage:
    python run.py              # Start server with auto-indexing
    python run.py --index-only # Just rebuild indexes, don't start server
    python run.py --no-index   # Start server without indexing
    python run.py --scan       # Scan projects, index, then start server
"""
import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from code_hub.config import settings
from code_hub.models import Project, create_tables
from code_hub.indexer import get_indexer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def scan_projects(save: bool = True):
    """Scan all projects and optionally save to database."""
    from code_hub.scanner import ProjectScanner
    from code_hub.generator import DocumentationGenerator

    logger.info(f"Scanning projects in {settings.code_base_path}...")
    scanner = ProjectScanner()
    generator = DocumentationGenerator()

    projects = list(scanner.discover_projects())
    logger.info(f"Found {len(projects)} projects")

    if save:
        for i, path in enumerate(projects):
            try:
                scanned = scanner.scan_project(path)
                generator.save_to_database(scanned)
                if (i + 1) % 50 == 0:
                    logger.info(f"Saved {i + 1}/{len(projects)} projects...")
            except Exception as e:
                logger.warning(f"Error scanning {path.name}: {e}")

        logger.info(f"Saved {len(projects)} projects to database")

    return len(projects)


def build_indexes(rebuild: bool = False):
    """Build FTS and vector indexes for all projects."""
    logger.info("Building search indexes...")

    indexer = get_indexer()

    def on_progress(msg, current, total):
        if current % 10 == 0 or current == total:
            logger.info(f"  [{current}/{total}] {msg}")

    indexer.index_all(rebuild=rebuild, on_progress=on_progress)
    logger.info("Indexing complete")


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Start the web server."""
    import uvicorn
    logger.info(f"Starting server at http://{host}:{port}")
    uvicorn.run(
        "code_hub.server:app",
        host=host,
        port=port,
        log_level="info",
        reload=False
    )


def main():
    parser = argparse.ArgumentParser(description="Code Hub Runner")
    parser.add_argument("--scan", action="store_true",
                        help="Scan projects before starting")
    parser.add_argument("--index-only", action="store_true",
                        help="Only build indexes, don't start server")
    parser.add_argument("--no-index", action="store_true",
                        help="Start server without indexing")
    parser.add_argument("--rebuild", action="store_true",
                        help="Rebuild all indexes from scratch")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Server port (default: 8000)")
    args = parser.parse_args()

    # Ensure database is initialized
    logger.info("Initializing database...")
    create_tables()

    # Scan projects if requested
    if args.scan:
        scan_projects(save=True)

    # Build indexes unless --no-index
    if not args.no_index:
        project_count = Project.select().count()
        if project_count == 0:
            logger.warning("No projects in database. Run with --scan first.")
        else:
            build_indexes(rebuild=args.rebuild)

    # Start server unless --index-only
    if not args.index_only:
        run_server(host=args.host, port=args.port)
    else:
        logger.info("Index-only mode, not starting server")


if __name__ == "__main__":
    main()
