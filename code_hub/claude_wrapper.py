"""Wrapper for Claude Code CLI in headless mode."""
import json
import logging
import subprocess
import time
import uuid
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Callable

from code_hub.config import settings

logger = logging.getLogger(__name__)

# Temp directory for Claude output files
TEMP_OUTPUT_DIR = Path("/tmp/code_hub/output")


@dataclass
class ClaudeResponse:
    """Response from Claude CLI."""
    success: bool
    content: str
    raw_output: str
    error: Optional[str] = None
    duration: float = 0.0


class ClaudeWrapper:
    """Wrapper for executing Claude CLI in headless mode."""

    def __init__(
        self,
        timeout: int = None,
        max_retries: int = None,
    ):
        self.timeout = timeout or settings.claude_timeout
        self.max_retries = max_retries or settings.claude_max_retries
        self._last_call_time = 0.0
        self._min_interval = 60.0 / settings.claude_rate_limit
        # Ensure temp directory exists
        TEMP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def _rate_limit(self):
        """Enforce rate limiting between calls."""
        elapsed = time.time() - self._last_call_time
        if elapsed < self._min_interval:
            wait_time = self._min_interval - elapsed
            logger.debug(f"Rate limiting: waiting {wait_time:.1f}s")
            time.sleep(wait_time)
        self._last_call_time = time.time()

    def _generate_temp_path(self, suffix: str = ".txt") -> Path:
        """Generate a unique temp file path."""
        unique_id = uuid.uuid4().hex[:12]
        return TEMP_OUTPUT_DIR / f"{unique_id}{suffix}"

    def run(
        self,
        prompt: str,
        working_dir: Optional[Path] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        allowed_tools: Optional[list] = None,
        add_dirs: Optional[list] = None,
        output_file: Optional[Path] = None,
        on_retry: Optional[Callable[[int, str], None]] = None
    ) -> ClaudeResponse:
        """
        Run Claude CLI with the given prompt.

        Args:
            prompt: The prompt to send to Claude
            working_dir: Working directory for the command
            model: Model to use (e.g., "sonnet", "haiku", "opus")
            system_prompt: Custom system prompt
            allowed_tools: List of allowed tools (e.g., ["Read", "Glob", "Grep", "Write"])
            add_dirs: Additional directories to allow access to
            output_file: If provided, expect Claude to write output here
            on_retry: Callback for retry attempts (retry_num, error_msg)

        Returns:
            ClaudeResponse with results
        """
        claude_bin = settings.get_claude_path()
        cmd = [claude_bin, "-p", prompt, "--output-format", "json"]

        if model:
            cmd.extend(["--model", model])

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        if allowed_tools:
            cmd.extend(["--allowed-tools", ",".join(allowed_tools)])

        # Always add temp output dir for writing
        dirs_to_add = list(add_dirs) if add_dirs else []
        dirs_to_add.append(str(TEMP_OUTPUT_DIR))
        for d in dirs_to_add:
            cmd.extend(["--add-dir", str(d)])

        last_error = None
        start_time = time.time()

        logger.info(f"Running Claude CLI: model={model}, cwd={working_dir}, timeout={self.timeout}s")
        logger.debug(f"Command: {' '.join(cmd[:6])}...")  # Log first part of command

        for attempt in range(self.max_retries):
            try:
                self._rate_limit()

                # Clean up output file if it exists from previous attempt
                if output_file and output_file.exists():
                    output_file.unlink()

                attempt_start = time.time()
                logger.debug(f"Attempt {attempt + 1}/{self.max_retries}: starting subprocess...")

                result = subprocess.run(
                    cmd,
                    cwd=working_dir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout
                )

                attempt_duration = time.time() - attempt_start
                duration = time.time() - start_time
                logger.debug(f"Subprocess completed in {attempt_duration:.1f}s, return code: {result.returncode}")

                # Check if we should read from output file
                if output_file and output_file.exists():
                    try:
                        content = output_file.read_text()
                        content_len = len(content)
                        # Clean up temp file
                        output_file.unlink()
                        logger.info(f"Success: read {content_len} chars from output file in {duration:.1f}s")
                        return ClaudeResponse(
                            success=True,
                            content=content,
                            raw_output=result.stdout,
                            duration=duration
                        )
                    except IOError as e:
                        last_error = f"Failed to read output file: {e}"
                        logger.warning(f"Failed to read output file: {e}")
                        continue

                # Fall back to parsing stdout
                if result.returncode == 0:
                    try:
                        output = json.loads(result.stdout)
                        content = self._extract_content(output)

                        # Check for API errors in the result
                        if output.get("is_error"):
                            last_error = content
                            logger.warning(f"API error in response: {content[:200]}")
                            continue

                        logger.info(f"Success: got {len(content)} chars from stdout in {duration:.1f}s")
                        return ClaudeResponse(
                            success=True,
                            content=content,
                            raw_output=result.stdout,
                            duration=duration
                        )
                    except json.JSONDecodeError:
                        logger.info(f"Success: got non-JSON response ({len(result.stdout)} chars) in {duration:.1f}s")
                        return ClaudeResponse(
                            success=True,
                            content=result.stdout,
                            raw_output=result.stdout,
                            duration=duration
                        )
                else:
                    last_error = result.stderr or f"Exit code: {result.returncode}"
                    logger.warning(f"Non-zero exit code {result.returncode}: {last_error[:200]}")

            except subprocess.TimeoutExpired:
                last_error = f"Timeout after {self.timeout}s"
                logger.error(f"Timeout after {self.timeout}s on attempt {attempt + 1}")
            except Exception as e:
                last_error = str(e)
                logger.error(f"Exception on attempt {attempt + 1}: {e}")

            # Retry with exponential backoff
            if attempt < self.max_retries - 1:
                backoff = 2 ** attempt
                logger.info(f"Retrying in {backoff}s (attempt {attempt + 2}/{self.max_retries})...")
                if on_retry:
                    on_retry(attempt + 1, last_error)
                time.sleep(backoff)

        total_duration = time.time() - start_time
        logger.error(f"All {self.max_retries} attempts failed after {total_duration:.1f}s: {last_error}")
        return ClaudeResponse(
            success=False,
            content="",
            raw_output="",
            error=last_error,
            duration=total_duration
        )

    def _extract_content(self, output: Any) -> str:
        """Extract text content from Claude JSON output."""
        if isinstance(output, dict):
            # Handle various response formats
            if "result" in output:
                result = output["result"]
                if isinstance(result, str):
                    return result
                return json.dumps(result, indent=2)
            if "content" in output:
                return output["content"]
            if "response" in output:
                return output["response"]
            # Return full JSON if structure unknown
            return json.dumps(output, indent=2)
        if isinstance(output, str):
            return output
        return str(output)

    def generate_readme(self, project_path: Path) -> ClaudeResponse:
        """Generate a README.md for a project."""
        from code_hub.prompts import get_readme_prompt

        logger.info(f"Generating README for {project_path.name}")
        output_file = self._generate_temp_path(".md")
        prompt = get_readme_prompt(output_path=str(output_file))

        response = self.run(
            prompt=prompt,
            working_dir=project_path,
            model=settings.claude_readme_model,
            allowed_tools=["Read", "Glob", "Grep", "Write"],
            system_prompt="You are a technical documentation expert. Generate clear, accurate README files based on actual project content. Write output to the specified file path.",
            output_file=output_file
        )
        if response.success:
            logger.info(f"README generated for {project_path.name} ({len(response.content)} chars, {response.duration:.1f}s)")
        else:
            logger.error(f"README generation failed for {project_path.name}: {response.error}")
        return response

    def generate_metadata(self, project_path: Path) -> ClaudeResponse:
        """Generate METADATA.json for a project."""
        from code_hub.prompts import get_metadata_prompt

        logger.info(f"Generating METADATA for {project_path.name}")
        output_file = self._generate_temp_path(".json")
        prompt = get_metadata_prompt(output_path=str(output_file))

        response = self.run(
            prompt=prompt,
            working_dir=project_path,
            model=settings.claude_metadata_model,
            allowed_tools=["Read", "Glob", "Grep", "Write"],
            system_prompt="You are a code analysis expert. Extract accurate, structured metadata from codebases. Write valid JSON output to the specified file path.",
            output_file=output_file
        )
        if response.success:
            logger.info(f"METADATA generated for {project_path.name} ({len(response.content)} chars, {response.duration:.1f}s)")
        else:
            logger.error(f"METADATA generation failed for {project_path.name}: {response.error}")
        return response

    def generate_usage(self, project_path: Path) -> ClaudeResponse:
        """Generate USAGE.md for a project."""
        from code_hub.prompts import get_usage_prompt

        logger.info(f"Generating USAGE for {project_path.name}")
        output_file = self._generate_temp_path(".md")
        prompt = get_usage_prompt(output_path=str(output_file))

        response = self.run(
            prompt=prompt,
            working_dir=project_path,
            model=settings.claude_usage_model,
            allowed_tools=["Read", "Glob", "Grep", "Write"],
            system_prompt="You are a technical documentation expert specializing in usage guides and API documentation. Write comprehensive, self-contained usage documentation to the specified file path.",
            output_file=output_file
        )
        if response.success:
            logger.info(f"USAGE generated for {project_path.name} ({len(response.content)} chars, {response.duration:.1f}s)")
        else:
            logger.error(f"USAGE generation failed for {project_path.name}: {response.error}")
        return response


def get_claude() -> ClaudeWrapper:
    """Get a configured Claude wrapper instance."""
    return ClaudeWrapper()
