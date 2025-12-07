# Code Hub - Usage Guide

## Quick Start

Get started with Code Hub in 4 simple commands:

```bash
# 1. Install Code Hub
pip install -e .

# 2. Scan your code projects
code-hub scan

# 3. Generate documentation using Claude
code-hub generate

# 4. Start the web interface
code-hub serve
```

Open your browser to http://localhost:8000 to explore your documented projects.

## Installation

### Prerequisites

- **Python 3.11 or higher**
- **Claude Code CLI** installed and authenticated
  ```bash
  # Install Claude Code CLI (if not already installed)
  # Follow instructions at https://claude.com/claude-code

  # Verify installation
  claude --version
  ```
- **~500MB disk space** for database and embeddings

### Install Code Hub

**Option 1: Install in development mode (recommended)**

```bash
cd ~/Code/code_hub
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e .
```

**Option 2: Install with development dependencies**

```bash
pip install -e ".[dev]"
```

**Option 3: Install from requirements.txt**

```bash
pip install -r requirements.txt
```

### Verify Installation

```bash
code-hub --help
```

Expected output:
```
Usage: code-hub [OPTIONS] COMMAND [ARGS]...

  Code Hub - Self-documenting code project management

Commands:
  generate  Generate documentation for projects
  index     Build/rebuild search indexes
  scan      Scan projects in code base
  search    Search projects
  serve     Start web server
  show      Show project details
  stats     Show statistics
```

### Data Storage

Code Hub stores all data in `~/.code_hub/`:
- `code_hub.db` - SQLite database with FTS5 indexes
- `chroma/` - ChromaDB vector store for semantic search

## Basic Usage

### 1. Scanning Projects

Code Hub scans your `~/Code` directory (configurable) to discover projects:

```bash
# Scan all projects in ~/Code
code-hub scan
```

Expected output:
```
Scanning /home/user/Code for projects...
Found 127 projects

Saving to database...
  ✓ my-python-app
  ✓ react-dashboard
  ✓ rust-cli-tool
  ...
Saved 127 projects
```

**Scan a specific directory:**

```bash
code-hub scan --path ~/Code/python-projects
```

**Scan without saving (dry run):**

```bash
code-hub scan --no-save
```

This displays discovered projects but doesn't update the database.

### 2. Generating Documentation

Code Hub uses Claude to generate three types of documentation:

- **README.md** - Project overview (generated with Sonnet 4.5)
- **METADATA.json** - Structured metadata for indexing (generated with Haiku 4.5)
- **USAGE.md** - Comprehensive usage guide (generated with Sonnet 4.5)

```bash
# Generate missing documentation for all projects
code-hub generate
```

Expected output:
```
Processing 15 projects that need documentation...

=== Processing my-flask-api (2,345 LOC) ===
[1/3] README: generating...
[1/3] README: saved (1,234 chars)
[2/3] METADATA: generating...
[2/3] METADATA: saved
[3/3] USAGE: generating...
[3/3] USAGE: saved (5,678 chars)
=== Completed my-flask-api in 12.3s ===

...

Summary:
  Total: 15 projects
  README: 12 generated
  METADATA: 15 generated
  USAGE: 10 generated
  Errors: 0
```

**Generate for a specific project:**

```bash
code-hub generate --project my-project
```

**Force regenerate all documentation:**

```bash
code-hub generate --force
```

**Force regenerate specific types:**

```bash
# Regenerate only READMEs
code-hub generate --force-readme

# Regenerate only METADATA.json files
code-hub generate --force-metadata

# Regenerate only USAGE.md files
code-hub generate --force-usage
```

**Preview what would be generated (dry run):**

```bash
code-hub generate --dry-run
```

This shows which projects would have documentation generated without actually calling Claude.

### 3. Building Search Indexes

After generating documentation, build the search indexes:

```bash
# Build FTS and vector indexes
code-hub index
```

Expected output:
```
Building search indexes...
  [10/127] Indexing my-flask-api
  [20/127] Indexing react-dashboard
  ...
  [127/127] Indexing rust-cli-tool

Indexing complete
  FTS index: 127 projects
  Vector index: 127 projects
```

**Rebuild indexes from scratch:**

```bash
code-hub index --rebuild
```

This deletes existing indexes and rebuilds them completely.

### 4. Searching Projects

Code Hub provides three search modes:

**Hybrid search (default - combines full-text and semantic):**

```bash
code-hub search "machine learning"
```

Expected output:
```
Found 5 projects:

1. ml-pipeline (python)
   A machine learning pipeline for training and deploying models
   Keywords: machine-learning, pytorch, mlops
   Score: 0.89

2. tensorflow-experiments (python)
   Experiments with TensorFlow and deep learning
   Keywords: machine-learning, tensorflow, neural-networks
   Score: 0.76

...
```

**Semantic search only (best for conceptual queries):**

```bash
code-hub search --semantic "projects that handle authentication"
```

**Full-text search only (best for exact matches):**

```bash
code-hub search --fts "flask api"
```

**Limit number of results:**

```bash
code-hub search -n 20 "web scraping"
```

### 5. Viewing Project Details

**Show details for a specific project:**

```bash
code-hub show my-project
```

Expected output:
```
Project: my-project
Path: /home/user/Code/my-project
Description: A Flask API for managing user authentication

Languages: python (primary), javascript
Frameworks: flask, react
Keywords: api, authentication, jwt, rest

Stats:
  Files: 42
  Lines of Code: 3,456
  Size: 1.2 MB

Git:
  Repository: Yes
  Remote: https://github.com/user/my-project
  Default Branch: main
  Last Commit: 2024-01-15 14:32:00

Modules:
  - auth.py: Handles JWT authentication
  - users.py: User model and database operations
  - api.py: Flask route definitions
  ...

Generated: 2024-01-20 10:15:00
Indexed: 2024-01-20 10:20:00
```

### 6. Viewing Statistics

```bash
code-hub stats
```

Expected output:
```
Code Hub Statistics

Projects: 127
  With README: 115
  With METADATA: 120
  With USAGE: 98
  Indexed: 127

Languages:
  python: 45 projects
  javascript: 32 projects
  rust: 15 projects
  go: 12 projects
  java: 8 projects
  ...

Total Lines of Code: 1,234,567
Total Size: 2.3 GB

Top Keywords:
  api (23)
  cli (18)
  web (15)
  machine-learning (12)
  automation (10)
  ...
```

### 7. Starting the Web Server

```bash
# Start on default port (8000)
code-hub serve
```

Expected output:
```
Starting server at http://0.0.0.0:8000
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**Start on a custom port:**

```bash
code-hub serve --port 3000
```

**Start on localhost only:**

```bash
code-hub serve --host 127.0.0.1
```

### 8. Resetting the Database

```bash
code-hub reset
```

This command:
1. Drops all database tables
2. Deletes the vector store
3. Recreates empty tables

**Warning:** This is destructive and cannot be undone!

## API Reference

### Command-Line Interface

#### `code-hub scan`

Scan projects in the code base directory.

**Options:**
- `--path PATH` - Directory to scan (default: `~/Code`)
- `--no-save` - Don't save to database (dry run)

**Example:**
```bash
code-hub scan --path ~/Code/python-projects
```

#### `code-hub generate`

Generate documentation for projects using Claude.

**Options:**
- `--project NAME` - Generate for specific project only
- `--force` - Force regenerate all documentation
- `--force-readme` - Force regenerate README.md files
- `--force-metadata` - Force regenerate METADATA.json files
- `--force-usage` - Force regenerate USAGE.md files
- `--dry-run` - Show what would be generated without doing it

**Examples:**
```bash
# Generate missing docs
code-hub generate

# Regenerate everything for one project
code-hub generate --project my-app --force

# Regenerate all README files
code-hub generate --force-readme
```

#### `code-hub search`

Search projects using full-text or semantic search.

**Arguments:**
- `QUERY` - Search query string

**Options:**
- `-n, --limit INTEGER` - Maximum results (default: 10)
- `--fts` - Use full-text search only
- `--semantic` - Use semantic search only
- (no flag) - Use hybrid search (default)

**Examples:**
```bash
# Hybrid search
code-hub search "web scraping"

# Semantic search
code-hub search --semantic "tools for data analysis"

# Full-text search with limit
code-hub search --fts "flask" -n 5
```

#### `code-hub index`

Build or rebuild search indexes.

**Options:**
- `--rebuild` - Delete and rebuild all indexes

**Examples:**
```bash
# Build indexes for new projects
code-hub index

# Completely rebuild indexes
code-hub index --rebuild
```

#### `code-hub show`

Show detailed information about a project.

**Arguments:**
- `NAME` - Project name

**Example:**
```bash
code-hub show my-flask-api
```

#### `code-hub stats`

Show database and collection statistics.

**Example:**
```bash
code-hub stats
```

#### `code-hub serve`

Start the web server.

**Options:**
- `--host HOST` - Server host (default: `0.0.0.0`)
- `--port PORT` - Server port (default: `8000`)

**Examples:**
```bash
# Default
code-hub serve

# Custom port
code-hub serve --port 3000

# Localhost only
code-hub serve --host 127.0.0.1 --port 8080
```

#### `code-hub reset`

Reset the database (destructive operation).

**Example:**
```bash
code-hub reset
```

### Web API Endpoints

The web server provides a RESTful API:

#### `GET /api/projects`

List all projects with pagination and filtering.

**Query Parameters:**
- `page` (int) - Page number (default: 1)
- `per_page` (int) - Items per page (default: 20, max: 100)
- `language` (string) - Filter by primary language
- `keyword` (string) - Filter by keyword

**Example Request:**
```bash
curl "http://localhost:8000/api/projects?language=python&page=1&per_page=10"
```

**Example Response:**
```json
{
  "projects": [
    {
      "name": "my-flask-api",
      "path": "/home/user/Code/my-flask-api",
      "short_description": "A Flask API for managing user authentication",
      "primary_language": "python",
      "languages": ["python", "javascript"],
      "frameworks": ["flask", "react"],
      "keywords": ["api", "authentication", "jwt"],
      "file_count": 42,
      "lines_of_code": 3456,
      "is_git_repo": true,
      "github_name": "user/my-flask-api"
    }
  ],
  "total": 45,
  "page": 1,
  "per_page": 10,
  "pages": 5
}
```

#### `GET /api/projects/{name}`

Get details for a specific project.

**Example Request:**
```bash
curl "http://localhost:8000/api/projects/my-flask-api"
```

**Example Response:**
```json
{
  "name": "my-flask-api",
  "path": "/home/user/Code/my-flask-api",
  "short_description": "A Flask API for managing user authentication",
  "long_description": "A comprehensive Flask API that provides JWT-based authentication...",
  "primary_language": "python",
  "languages": ["python", "javascript"],
  "frameworks": ["flask", "react"],
  "keywords": ["api", "authentication", "jwt", "rest"],
  "modules": [
    {
      "name": "auth",
      "path": "src/auth.py",
      "description": "Handles JWT authentication and token management"
    }
  ],
  "file_count": 42,
  "lines_of_code": 3456,
  "size_bytes": 1234567,
  "is_git_repo": true,
  "git_remote_url": "https://github.com/user/my-flask-api",
  "github_name": "user/my-flask-api",
  "scanned_at": "2024-01-20T10:15:00",
  "generated_at": "2024-01-20T10:15:30",
  "indexed_at": "2024-01-20T10:20:00"
}
```

#### `GET /api/projects/{name}/readme`

Get the README content for a project.

**Example Request:**
```bash
curl "http://localhost:8000/api/projects/my-flask-api/readme"
```

**Example Response:**
```json
{
  "name": "my-flask-api",
  "readme": "# My Flask API\n\nA Flask API for managing user authentication...",
  "has_readme": true
}
```

#### `GET /api/search`

Search projects using full-text or semantic search.

**Query Parameters:**
- `q` (string, required) - Search query
- `mode` (string) - Search mode: `fts`, `semantic`, or `hybrid` (default: `hybrid`)
- `limit` (int) - Maximum results (default: 10, max: 100)

**Example Request:**
```bash
curl "http://localhost:8000/api/search?q=machine%20learning&mode=hybrid&limit=5"
```

**Example Response:**
```json
{
  "query": "machine learning",
  "mode": "hybrid",
  "results": [
    {
      "name": "ml-pipeline",
      "short_description": "A machine learning pipeline for training and deploying models",
      "primary_language": "python",
      "keywords": ["machine-learning", "pytorch", "mlops"],
      "score": 0.89
    }
  ],
  "total": 5
}
```

#### `GET /api/keywords`

List all keywords with project counts.

**Example Request:**
```bash
curl "http://localhost:8000/api/keywords"
```

**Example Response:**
```json
{
  "keywords": [
    {"name": "api", "count": 23},
    {"name": "cli", "count": 18},
    {"name": "web", "count": 15},
    {"name": "machine-learning", "count": 12}
  ]
}
```

#### `GET /api/languages`

List all programming languages with project counts.

**Example Request:**
```bash
curl "http://localhost:8000/api/languages"
```

**Example Response:**
```json
{
  "languages": [
    {"name": "python", "count": 45},
    {"name": "javascript", "count": 32},
    {"name": "rust", "count": 15},
    {"name": "go", "count": 12}
  ]
}
```

#### `GET /api/stats`

Get system statistics.

**Example Request:**
```bash
curl "http://localhost:8000/api/stats"
```

**Example Response:**
```json
{
  "total_projects": 127,
  "with_readme": 115,
  "with_metadata": 120,
  "with_usage": 98,
  "indexed": 127,
  "total_lines_of_code": 1234567,
  "total_size_bytes": 2300000000,
  "languages": {
    "python": 45,
    "javascript": 32,
    "rust": 15
  },
  "top_keywords": [
    {"name": "api", "count": 23},
    {"name": "cli", "count": 18}
  ]
}
```

### Python API

You can also use Code Hub programmatically:

#### Scanning Projects

```python
from code_hub.scanner import ProjectScanner

scanner = ProjectScanner()

# Discover all projects
projects = list(scanner.discover_projects())
print(f"Found {len(projects)} projects")

# Scan a specific project
from pathlib import Path
project = scanner.scan_project(Path("~/Code/my-project").expanduser())

print(f"Name: {project.name}")
print(f"Languages: {project.languages}")
print(f"LOC: {project.stats.lines_of_code}")
print(f"Is Git repo: {project.git.is_repo}")
```

#### Generating Documentation

```python
from code_hub.generator import DocumentationGenerator
from code_hub.scanner import ProjectScanner
from pathlib import Path

scanner = ProjectScanner()
generator = DocumentationGenerator()

# Scan and generate for a project
project = scanner.scan_project(Path("~/Code/my-project").expanduser())
result = generator.generate_for_project(project)

if result.readme_generated:
    print("README generated successfully")
if result.metadata_generated:
    print("METADATA generated successfully")
if result.usage_generated:
    print("USAGE generated successfully")

# Save to database
db_project = generator.save_to_database(project)
print(f"Saved to database with ID: {db_project.id}")
```

#### Database Queries

```python
from code_hub.models import Project, Keyword, Module

# Get all Python projects
python_projects = Project.select().where(
    Project.primary_language == 'python'
)

for project in python_projects:
    print(f"{project.name}: {project.short_description}")

# Find projects by keyword
keyword = Keyword.get(Keyword.name == 'machine-learning')
projects = Project.select().join(ProjectKeyword).where(
    ProjectKeyword.keyword == keyword
)

# Get project modules
project = Project.get(Project.name == 'my-project')
for module in project.modules:
    print(f"{module.name}: {module.description}")
```

#### Search

```python
from code_hub.indexer import get_indexer

indexer = get_indexer()

# Full-text search
results = indexer.search_fts("flask api", limit=10)
for project, score in results:
    print(f"{project.name} (score: {score})")

# Semantic search
results = indexer.search_semantic("tools for data analysis", limit=10)
for project, score in results:
    print(f"{project.name} (score: {score})")

# Hybrid search
results = indexer.search_hybrid("machine learning", limit=10)
for project, score in results:
    print(f"{project.name} (score: {score})")
```

## Configuration

### Environment Variables

Create a `.env` file in the project root or set these environment variables:

```bash
# Paths
CODE_BASE_PATH=~/Code                    # Where to scan for projects
DATA_DIR=~/.code_hub                     # Where to store database and indexes

# Directories to exclude from scanning
EXCLUDE_DIRS=node_modules,venv,.venv,__pycache__,.git,dist,build

# Claude CLI Configuration
CLAUDE_README_MODEL=sonnet               # Model for README generation
CLAUDE_METADATA_MODEL=haiku              # Model for METADATA generation
CLAUDE_USAGE_MODEL=sonnet                # Model for USAGE generation
CLAUDE_TIMEOUT=300                       # Timeout in seconds (5 minutes)
CLAUDE_MAX_RETRIES=3                     # Max retries for failed requests
CLAUDE_RATE_LIMIT=10                     # Max requests per minute

# Web Server
SERVER_HOST=0.0.0.0                      # Server host
SERVER_PORT=8000                         # Server port

# Processing
BATCH_SIZE=10                            # Batch size for processing
MAX_WORKERS=4                            # Max parallel workers

# Embedding Model (for vector search)
EMBEDDING_MODEL=all-MiniLM-L6-v2         # Sentence transformer model
```

### Configuration File

Alternatively, create a `config.py` file:

```python
from pathlib import Path
from code_hub.config import Settings

settings = Settings(
    code_base_path=Path.home() / "Code",
    data_dir=Path.home() / ".code_hub",
    claude_readme_model="sonnet",
    claude_metadata_model="haiku",
    claude_usage_model="sonnet",
    server_port=8000
)
```

### Model Aliases

Code Hub accepts these model aliases for Claude:
- `sonnet` - Latest Claude Sonnet model
- `opus` - Latest Claude Opus model
- `haiku` - Latest Claude Haiku model

Or use full model names like `claude-sonnet-4-20250514`.

## Common Patterns

### Complete Workflow

Typical workflow for maintaining Code Hub:

```bash
# 1. Scan for new or updated projects
code-hub scan

# 2. Generate missing documentation
code-hub generate

# 3. Rebuild search indexes
code-hub index

# 4. Start the web interface
code-hub serve
```

### Incremental Updates

Code Hub intelligently handles incremental updates:

```bash
# Scan only updates changed projects
code-hub scan

# Generate only creates missing docs
code-hub generate

# Index only processes unindexed projects
code-hub index
```

### Force Regeneration

When you need to regenerate documentation:

```bash
# Regenerate everything
code-hub generate --force

# Regenerate specific types
code-hub generate --force-readme      # Update all READMEs
code-hub generate --force-metadata    # Update all METADATA
code-hub generate --force-usage       # Update all USAGE docs
```

### Working with Specific Projects

```bash
# Generate docs for one project
code-hub generate --project my-flask-api

# Show project details
code-hub show my-flask-api

# Search for the project
code-hub search "my-flask-api"
```

### Filtering and Browsing

Using the web interface (http://localhost:8000):

1. **Browse by Language**: Click on a language in the dashboard
2. **Filter by Keyword**: Click on a keyword tag
3. **Search**: Use the search bar for full-text or semantic search
4. **View Details**: Click on a project card to see full details

### Custom Scanning

Scan specific directories:

```bash
# Scan only Python projects
code-hub scan --path ~/Code/python

# Scan multiple times with different paths
code-hub scan --path ~/Code/python
code-hub scan --path ~/Code/rust
code-hub scan --path ~/Code/javascript
```

### Using the Run Script

The `run.py` script provides a convenient way to manage Code Hub:

```bash
# Standard startup (index + serve)
python run.py

# Scan, index, and serve
python run.py --scan

# Only rebuild indexes
python run.py --index-only

# Serve without indexing
python run.py --no-index

# Rebuild all indexes from scratch
python run.py --rebuild

# Custom host and port
python run.py --host 127.0.0.1 --port 3000
```

## Examples

### Example 1: Initial Setup

```bash
# Clone or navigate to Code Hub
cd ~/Code/code_hub

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e .

# Verify Claude CLI is working
claude --version
# Output: claude version 1.2.3

# Scan your codebase
code-hub scan
# Output: Found 127 projects, Saved 127 projects

# Generate documentation (this will take a while)
code-hub generate
# Output: Processing 127 projects...
#         (Shows progress for each project)

# Build search indexes
code-hub index
# Output: Indexing complete
#         FTS index: 127 projects
#         Vector index: 127 projects

# Start the server
code-hub serve
# Output: Starting server at http://0.0.0.0:8000
```

### Example 2: Searching for Projects

```bash
# Find all machine learning projects
code-hub search --semantic "machine learning projects"

# Output:
# Found 5 projects:
#
# 1. ml-pipeline (python)
#    A machine learning pipeline for training and deploying models
#    Keywords: machine-learning, pytorch, mlops
#    Score: 0.89
#
# 2. tensorflow-experiments (python)
#    Experiments with TensorFlow and deep learning
#    Keywords: machine-learning, tensorflow, neural-networks
#    Score: 0.76

# Find projects that use Flask
code-hub search --fts "flask" -n 20

# Output:
# Found 12 projects:
#
# 1. my-flask-api (python)
#    A Flask API for managing user authentication
#    Keywords: api, authentication, jwt, rest
#    Score: 0.95
```

### Example 3: Using the Web Interface

Start the server:
```bash
code-hub serve
```

Then open http://localhost:8000 in your browser.

**Dashboard View:**
- Shows total projects, languages, and top keywords
- Click on a language to filter projects (e.g., "Python (45 projects)")
- Click on a keyword to see related projects (e.g., "api (23)")

**Search:**
1. Navigate to the Search page
2. Enter query: "web scraping tools"
3. Select search mode: "Semantic"
4. Click "Search"
5. Results show relevant projects with scores

**Project Details:**
1. Click on any project card
2. View README content
3. See metadata: languages, frameworks, keywords
4. Browse module list with descriptions
5. Click "View on GitHub" if available

### Example 4: API Integration

```python
import requests

# Search via API
response = requests.get(
    "http://localhost:8000/api/search",
    params={
        "q": "authentication",
        "mode": "hybrid",
        "limit": 5
    }
)
results = response.json()

for project in results["results"]:
    print(f"{project['name']}: {project['short_description']}")

# Get project details
response = requests.get(
    "http://localhost:8000/api/projects/my-flask-api"
)
project = response.json()

print(f"Languages: {', '.join(project['languages'])}")
print(f"Frameworks: {', '.join(project['frameworks'])}")
print(f"Lines of Code: {project['lines_of_code']:,}")

# List all Python projects
response = requests.get(
    "http://localhost:8000/api/projects",
    params={"language": "python", "per_page": 50}
)
data = response.json()

print(f"Total Python projects: {data['total']}")
for project in data["projects"]:
    print(f"  - {project['name']}")
```

### Example 5: Custom Documentation Generation

```python
from pathlib import Path
from code_hub.scanner import ProjectScanner
from code_hub.generator import DocumentationGenerator
from code_hub.claude_wrapper import ClaudeWrapper

# Initialize components
scanner = ProjectScanner()
claude = ClaudeWrapper()

# Use custom models
generator = DocumentationGenerator(
    claude=claude,
    force_readme=True,  # Regenerate READMEs
    force_metadata=False,
    force_usage=False
)

# Process a specific project
project_path = Path("~/Code/my-new-project").expanduser()
scanned = scanner.scan_project(project_path)

# Generate documentation
result = generator.generate_for_project(
    scanned,
    on_progress=lambda msg: print(f"Progress: {msg}")
)

# Check results
if result.readme_generated:
    print("✓ README generated")
if result.metadata_generated:
    print("✓ METADATA generated")
if result.usage_generated:
    print("✓ USAGE generated")

print(f"Completed in {result.duration:.1f}s")

# Save to database
db_project = generator.save_to_database(scanned)
print(f"Saved to database: {db_project.name}")
```

### Example 6: Batch Processing

```python
from code_hub.scanner import ProjectScanner
from code_hub.generator import generate_all

# Generate docs for all projects with progress
def on_progress(msg, current, total):
    percent = (current / total) * 100
    print(f"[{current}/{total}] ({percent:.1f}%) {msg}")

results = generate_all(
    force_readme=False,
    force_metadata=True,  # Regenerate all metadata
    force_usage=False,
    on_progress=on_progress
)

# Summarize results
total = len(results)
readme_count = sum(1 for r in results if r.readme_generated)
metadata_count = sum(1 for r in results if r.metadata_generated)
usage_count = sum(1 for r in results if r.usage_generated)

print(f"\nSummary:")
print(f"  Total projects: {total}")
print(f"  READMEs generated: {readme_count}")
print(f"  METADATA generated: {metadata_count}")
print(f"  USAGE generated: {usage_count}")
```

## Troubleshooting

### Claude CLI Not Found

**Error:**
```
Error: claude command not found
```

**Solution:**
Install Claude Code CLI from https://claude.com/claude-code and ensure it's in your PATH:
```bash
which claude
# Should output: /usr/local/bin/claude (or similar)
```

### Authentication Required

**Error:**
```
Error: Claude CLI not authenticated
```

**Solution:**
Run the authentication command:
```bash
claude auth login
```

### Timeout Errors

**Error:**
```
Error: Claude request timeout after 300s
```

**Solution:**
Increase the timeout for large projects:
```bash
# In .env file
CLAUDE_TIMEOUT=600  # 10 minutes

# Or via environment variable
export CLAUDE_TIMEOUT=600
code-hub generate
```

### Rate Limit Errors

**Error:**
```
Error: Rate limit exceeded
```

**Solution:**
Reduce the rate limit setting:
```bash
# In .env file
CLAUDE_RATE_LIMIT=5  # Reduce from 10 to 5 requests per minute
```

Or add delays between generation calls when processing many projects.

### Database Locked

**Error:**
```
Error: database is locked
```

**Solution:**
This usually happens when multiple processes access the database. Ensure only one Code Hub process is running:
```bash
# Kill any running Code Hub processes
pkill -f "code-hub"

# Restart your operation
code-hub serve
```

### Vector Store Errors

**Error:**
```
Error: ChromaDB collection not found
```

**Solution:**
Rebuild the vector index:
```bash
code-hub index --rebuild
```

### Empty Search Results

**Problem:**
Search returns no results even though projects exist.

**Solution:**
Ensure indexes are built:
```bash
# Check if projects are indexed
code-hub stats
# Look for "Indexed: 0" or low numbers

# Rebuild indexes
code-hub index --rebuild
```

### Missing Documentation

**Problem:**
Projects scanned but no documentation generated.

**Solution:**
Explicitly run the generate command:
```bash
# Check what needs generation
code-hub generate --dry-run

# Generate missing docs
code-hub generate
```

### Metadata Parse Errors

**Error:**
```
Error: Could not parse metadata JSON
```

**Solution:**
This usually means Claude returned malformed JSON. Try regenerating:
```bash
code-hub generate --project my-project --force-metadata
```

If it persists, check the project's METADATA.json file and fix manually, or delete it and regenerate.

### Port Already in Use

**Error:**
```
Error: Address already in use
```

**Solution:**
Either kill the process using the port or use a different port:
```bash
# Find process using port 8000
lsof -ti:8000 | xargs kill -9

# Or use a different port
code-hub serve --port 3000
```

### Memory Issues with Large Codebases

**Problem:**
Code Hub crashes or becomes slow with very large codebases.

**Solution:**
1. Reduce batch size:
   ```bash
   # In .env
   BATCH_SIZE=5
   MAX_WORKERS=2
   ```

2. Process projects in smaller chunks:
   ```bash
   code-hub scan --path ~/Code/project-group-1
   code-hub generate
   code-hub index

   code-hub scan --path ~/Code/project-group-2
   code-hub generate
   code-hub index
   ```

### Stale Data

**Problem:**
Web interface shows outdated information.

**Solution:**
Rescan and regenerate:
```bash
# Rescan all projects
code-hub scan

# Force regenerate changed projects
code-hub generate --force

# Rebuild indexes
code-hub index --rebuild
```

## FAQ

**Q: How often should I run `code-hub scan`?**

A: Run it whenever you add new projects to your `~/Code` directory or want to update statistics for existing projects. Weekly or monthly is typical.

**Q: Does Code Hub work with non-Git projects?**

A: Yes! Code Hub scans any directory structure. Git information is optional and only used if available.

**Q: Can I exclude certain directories from scanning?**

A: Yes, configure `EXCLUDE_DIRS` in your `.env` file:
```bash
EXCLUDE_DIRS=node_modules,venv,.venv,__pycache__,.git,dist,build,vendor
```

**Q: How much does documentation generation cost?**

A: Cost depends on your Claude API usage. A typical project (2,000 LOC) uses:
- README: ~1,000 tokens (Sonnet)
- METADATA: ~500 tokens (Haiku)
- USAGE: ~1,500 tokens (Sonnet)

For 100 projects, expect ~$2-5 in API costs.

**Q: Can I use Code Hub with private repositories?**

A: Yes! Code Hub works entirely locally. Your code never leaves your machine except when calling Claude, and you can review what's sent.

**Q: How do I update generated documentation?**

A: Use `--force` flags:
```bash
code-hub generate --force-readme    # Update all READMEs
code-hub generate --force-metadata  # Update all METADATA
code-hub generate --force-usage     # Update all USAGE docs
```

**Q: Can I customize the documentation prompts?**

A: Yes! Edit the prompt files in `code_hub/prompts/`:
- `readme.txt` - README generation prompt
- `metadata.txt` - METADATA generation prompt
- `usage.txt` - USAGE generation prompt

**Q: Does semantic search work offline?**

A: The embedding model runs locally, so semantic search works offline after initial model download.

**Q: How do I backup my Code Hub data?**

A: Backup these directories:
```bash
~/.code_hub/code_hub.db    # Database
~/.code_hub/chroma/        # Vector store
```

Or simply regenerate from your projects using `code-hub scan` and `code-hub generate`.

**Q: Can I deploy the web interface to a server?**

A: Yes! The FastAPI server can be deployed like any ASGI application:
```bash
# Using Gunicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker code_hub.server:app

# Using systemd service
sudo systemctl start code-hub
```

**Q: How accurate is the generated documentation?**

A: Claude generates high-quality documentation based on the actual code. However, always review generated content for accuracy, especially for complex or domain-specific projects.