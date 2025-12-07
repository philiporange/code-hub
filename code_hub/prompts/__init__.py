"""Prompts for Claude Code documentation generation."""
from pathlib import Path
from typing import Optional

PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str, output_path: Optional[str] = None) -> str:
    """Load a prompt from the prompts directory.

    Args:
        name: Name of the prompt file (without .txt extension)
        output_path: If provided, append instruction to write to this path
    """
    prompt_file = PROMPTS_DIR / f"{name}.txt"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt not found: {name}")

    prompt = prompt_file.read_text()

    if output_path:
        prompt += f"\n\nIMPORTANT: Write your output to this file: {output_path}\nUse the Write tool to create the file. Do not output the content in your response - only write it to the file."

    return prompt


# Convenience functions
def get_readme_prompt(output_path: Optional[str] = None) -> str:
    return load_prompt("readme", output_path)


def get_metadata_prompt(output_path: Optional[str] = None) -> str:
    return load_prompt("metadata", output_path)


def get_usage_prompt(output_path: Optional[str] = None) -> str:
    return load_prompt("usage", output_path)
