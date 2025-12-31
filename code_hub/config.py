"""Configuration management with Pydantic and dotenv."""
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


# Default data directory
DEFAULT_DATA_DIR = Path.home() / ".code_hub"


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Paths
    code_base_path: Path = Path.home() / "Code"
    data_dir: Path = DEFAULT_DATA_DIR
    exclude_dirs: List[str] = [
        "node_modules", "venv", ".venv", "__pycache__",
        ".git", "dist", "build", ".tox", "eggs", "*.egg-info",
        ".next", ".nuxt", "target", "vendor", ".cargo"
    ]

    # Database (derived from data_dir)
    @property
    def database_path(self) -> Path:
        return self.data_dir / "code_hub.db"

    # Vector Store (derived from data_dir)
    @property
    def chroma_path(self) -> Path:
        return self.data_dir / "chroma"

    # Prompts directory
    @property
    def prompts_dir(self) -> Path:
        return Path(__file__).parent / "prompts"

    # Templates directory
    @property
    def templates_dir(self) -> Path:
        return Path(__file__).parent / "templates"

    # Static directory
    @property
    def static_dir(self) -> Path:
        return Path(__file__).parent / "static"

    # Embedding model
    embedding_model: str = "all-MiniLM-L6-v2"

    # Claude CLI
    claude_path: str = ""  # Path to claude CLI, empty means search PATH
    claude_timeout: int = 300  # seconds - 5 minutes for complex projects
    claude_max_retries: int = 3
    claude_rate_limit: int = 10  # requests per minute
    claude_readme_model: str = "haiku"  # Haiku for speed
    claude_metadata_model: str = "haiku"  # Haiku for speed
    claude_usage_model: str = "sonnet"  # Sonnet for USAGE.md (more detailed)

    def get_claude_path(self) -> str:
        """Get the path to claude CLI, searching common locations if not configured."""
        if self.claude_path:
            return self.claude_path

        import shutil
        # First try PATH
        claude = shutil.which("claude")
        if claude:
            return claude

        # Search common nvm locations
        import os
        home = Path.home()
        nvm_dir = home / ".nvm" / "versions" / "node"
        if nvm_dir.exists():
            for node_version in sorted(nvm_dir.iterdir(), reverse=True):
                claude_bin = node_version / "bin" / "claude"
                if claude_bin.exists():
                    return str(claude_bin)

        # Search other common locations
        common_paths = [
            home / ".local" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
            Path("/usr/bin/claude"),
        ]
        for path in common_paths:
            if path.exists():
                return str(path)

        # Fallback to just "claude" and let it fail with a clear error
        return "claude"

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # Processing
    batch_size: int = 10
    max_workers: int = 4

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure data directories exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_path.mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = Settings()
