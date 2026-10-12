"""Code style and compliance tests.

Enforces CyBreach rule:
Zero em dashes (Unicode U+2014) or en dashes (Unicode U+2013) anywhere in code, comments, or documentation.
Only hyphens (-) or colons (:) are permitted.
"""

from pathlib import Path
import pytest


def test_zero_em_dashes_in_repository():
    """Verify no em dash (U+2014) or en dash (U+2013) exists in any source or test file."""
    root = Path(__file__).resolve().parent.parent

    # File extensions to scan
    extensions = {".py", ".md", ".json", ".yaml", ".yml", ".txt"}
    # Ignore git directory and cache
    ignored_parts = {".git", ".pytest_cache", "__pycache__", ".venv", "venv"}

    violations = []

    for file_path in root.rglob("*"):
        if file_path.is_file() and file_path.suffix in extensions:
            if any(part in ignored_parts for part in file_path.parts):
                continue

            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            # Check for em dash (U+2014) and en dash (U+2013)
            for line_no, line in enumerate(content.splitlines(), start=1):
                if "\u2014" in line:
                    violations.append(f"{file_path}:{line_no} contains em dash (U+2014): {line}")
                if "\u2013" in line:
                    violations.append(f"{file_path}:{line_no} contains en dash (U+2013): {line}")

    assert not violations, f"Found em dash or en dash violations:\n" + "\n".join(violations)
