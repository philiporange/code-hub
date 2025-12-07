# Code Hub

A self-documenting code project management system that uses Claude Code in headless mode to automatically generate documentation, extract metadata, and provide intelligent search across your entire codebase.

## Overview

Code Hub scans your `~/Code` directory, uses Claude to generate README.md, USAGE.md, and METADATA.json files, stores everything in a searchable database with vector embeddings, and provides a web interface for exploration.

## Features

- **Automated Documentation**: Uses Claude Code headless mode to generate:
  - `README.md` - Project overview and documentation
  - `USAGE.md` - Self-contained usage guide with examples
  - `METADATA.json` - Structured metadata for indexing
- **Full-Text Search**: SQLite FTS5-powered search across all project content
- **Vector Search (RAG)**: Semantic search using sentence embeddings
- **Deep Code Search**: Grep-based search within project files with context
- **File Browser**: GitHub-like file browser with modification dates
- **Code Viewer**: Syntax-highlighted code viewing with line highlighting
- **Web Interface**: Browse, search, and explore all your projects
- **Incremental Updates**: Only processes changed projects on subsequent runs

## Project Structure

```
code_hub/
├── pyproject.toml          # Package configuration
├── README.md
├── run.py                  # Convenience startup script
├── code_hub/               # Main package
│   ├── __init__.py
│   ├── config.py           # Configuration with dotenv
│   ├── models.py           # Peewee ORM models
│   ├── claude_wrapper.py   # Claude CLI headless wrapper
│   ├── scanner.py          # Project discovery and analysis
│   ├── generator.py        # Documentation generation
│   ├── vectorstore.py      # Vector embeddings (ChromaDB)
│   ├── indexer.py          # FTS and vector indexing
│   ├── server.py           # FastAPI web server
│   ├── cli.py              # Command-line interface
│   ├── prompts/            # Claude prompt templates
│   │   ├── readme.txt
│   │   ├── metadata.txt
│   │   └── usage.txt
│   └── templates/          # Jinja2 HTML templates
│       ├── base.html
│       ├── index.html
│       ├── browse.html
│       ├── project.html
│       ├── files.html
│       ├── file_view.html
│       ├── search.html
│       └── deep_search.html
```

## Installation

```bash
cd ~/Code/code_hub
python -m venv venv
source venv/bin/activate
pip install -e .
```

Or install dependencies directly:
```bash
pip install -e ".[dev]"  # Include development dependencies
```

## Requirements

- Python 3.11+
- Claude Code CLI (`claude`) installed and authenticated
- ~500MB disk space for database and embeddings

## Data Storage

All data is stored in `~/.code_hub/`:
- `code_hub.db` - SQLite database with FTS5 indexes
- `chroma/` - ChromaDB vector store

## Quick Start

```bash
# 1. Scan all projects in ~/Code (saves to database)
code-hub scan

# 2. Generate docs for projects that don't have them
code-hub generate

# 3. Build search indexes
code-hub index

# 4. Start the web server
code-hub serve
# Open http://localhost:8000
```

Or use the convenience script:
```bash
python run.py --scan  # Scan, index, and serve
```

## CLI Commands

### Scan Projects
```bash
# Scan all projects in ~/Code
code-hub scan

# Scan specific directory
code-hub scan --path ~/Code/python-projects

# Scan without saving to database
code-hub scan --no-save
```

### Generate Documentation
```bash
# Generate missing docs for all projects
code-hub generate

# Generate for specific project
code-hub generate --project my-project

# Force regenerate everything
code-hub generate --force

# Force regenerate specific types
code-hub generate --force-readme
code-hub generate --force-metadata
code-hub generate --force-usage

# Preview what would be generated
code-hub generate --dry-run
```

### Search
```bash
# Hybrid search (FTS + semantic)
code-hub search "machine learning"

# Semantic search only
code-hub search --semantic "projects that handle authentication"

# Full-text search only
code-hub search --fts "flask api"

# Limit results
code-hub search -n 20 "web scraping"
```

### Other Commands
```bash
# Show statistics
code-hub stats

# Show project details
code-hub show my-project

# Build/rebuild indexes
code-hub index
code-hub index --rebuild

# Start web server
code-hub serve
code-hub serve --port 3000

# Reset database
code-hub reset
```

## Web Interface

The web interface provides:

- **Dashboard**: Overview with statistics and language breakdown
- **Browse**: Paginated project list with sorting (name, recent, created, LOC, files) and language/keyword filtering
- **Project Detail**: View README, metadata, modules, keywords, and inline file browser
- **File Browser**: GitHub-style file listing with modification dates and file sizes
- **Code Viewer**: Syntax-highlighted code with Pygments, line highlighting for search results
- **Search**: Full-text and semantic search with mode selection
- **Deep Search**: Grep-based code search within projects with context snippets
- **Generate Buttons**: Generate missing README.md or USAGE.md directly from the UI

### Project Page Features

Each project page includes:
- Project metadata (language, files, LOC, git status)
- Keywords and frameworks
- Inline file browser at the project root
- README content (with option to generate if missing)
- USAGE guide link (with option to generate if missing)
- Unified search bar with "This Project" (deep search) and "All Projects" buttons

### Deep Search

The deep search feature uses grep to search within project files:
- Results grouped by file
- Context lines (2 before and 2 after each match)
- Click on any match to view the file with that line highlighted
- Supports all common code file types

## API Endpoints

```
GET  /api/projects                    # List all projects
GET  /api/projects/{name}             # Get project details
GET  /api/projects/{name}/readme      # Get README content
GET  /api/projects/{name}/files       # List files in directory
GET  /api/projects/{name}/file/{path} # Get file content with highlighting
GET  /api/projects/{name}/search      # Deep search within project
POST /api/projects/{name}/generate/readme  # Generate README
POST /api/projects/{name}/generate/usage   # Generate USAGE
GET  /api/search?q=query              # Search (mode: fts, semantic, hybrid)
GET  /api/keywords                    # List all keywords
GET  /api/languages                   # List languages with counts
GET  /api/stats                       # System statistics
```

## Generated Files

### METADATA.json
```json
{
  "name": "project-name",
  "short_description": "One-line description",
  "long_description": "Detailed paragraph...",
  "keywords": ["python", "cli", "automation"],
  "primary_language": "python",
  "languages": ["python", "javascript"],
  "frameworks": ["flask", "react"],
  "modules": [
    {
      "name": "auth",
      "path": "src/auth.py",
      "description": "Handles user authentication"
    }
  ],
  "git": {
    "is_repo": true,
    "remote_url": "https://github.com/user/repo",
    "github_name": "user/repo"
  },
  "stats": {
    "files": 42,
    "lines_of_code": 3500
  }
}
```

### USAGE.md

The USAGE.md file is a self-contained usage guide that allows someone to use the project without reading all the source code. It includes:

- Quick Start examples
- Installation instructions
- Basic usage patterns
- API reference with examples
- Configuration options
- Common patterns and best practices

## Configuration

Create a `.env` file or set environment variables:

```bash
# Paths
CODE_BASE_PATH=~/Code
DATA_DIR=~/.code_hub

# Claude Models (use aliases or full names)
CLAUDE_README_MODEL=sonnet
CLAUDE_METADATA_MODEL=haiku
CLAUDE_USAGE_MODEL=sonnet
CLAUDE_TIMEOUT=180
CLAUDE_RATE_LIMIT=10

# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black code_hub/
ruff check --fix code_hub/

# Type checking
mypy code_hub/
```

## License

CC0
