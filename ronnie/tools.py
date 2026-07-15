"""Filesystem and shell tools for the Ronnie agent."""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Optional

from ronnie.config import (
    LIST_DIR_DEFAULT_DEPTH,
    GREP_MAX_RESULTS,
    VIEW_FILE_MAX_BYTES,
    COMMAND_TIMEOUT,
)

# Directories / files to always skip when walking the tree.
_IGNORED_DIRS = frozenset((
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".agents", "scratch", ".idea", ".vscode", ".mypy_cache",
    ".pytest_cache", ".tox", "dist", "build", ".next",
    "coverage", ".coverage", ".eggs",
))
_IGNORED_FILES = frozenset((".DS_Store",))


def _is_binary(path: str, chunk_size: int = 8192) -> bool:
    """Heuristic: file is binary if the first chunk contains a null byte."""
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(chunk_size)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

def list_dir(path: str = ".", depth: int | str = LIST_DIR_DEFAULT_DEPTH) -> str:
    """List files and directories up to *depth* levels deep.

    Directories are shown with a trailing ``/`` and include a child count.
    """
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: Directory '{path}' does not exist."
    if not os.path.isdir(abs_path):
        return f"Error: '{path}' is not a directory."

    max_depth = min(int(depth), 5) if depth else LIST_DIR_DEFAULT_DEPTH
    lines: list[str] = []

    for root, dirs, files in os.walk(abs_path):
        # Calculate current depth relative to the base path.
        rel_root = os.path.relpath(root, abs_path)
        current_depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if current_depth >= max_depth:
            dirs.clear()
            continue

        # Prune ignored directories.
        dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS)

        for d in dirs:
            dir_path = os.path.join(root, d)
            rel = os.path.relpath(dir_path, abs_path)
            try:
                child_count = len(os.listdir(dir_path))
            except PermissionError:
                child_count = "?"
            lines.append(f"{rel}/ ({child_count} items)")

        for f in sorted(files):
            if f in _IGNORED_FILES:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, abs_path)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            lines.append(f"{rel} ({size} bytes)")

    return "\n".join(lines) if lines else "Directory is empty."


# ---------------------------------------------------------------------------
# view_file
# ---------------------------------------------------------------------------

def view_file(
    path: str,
    start_line: int | str = 1,
    end_line: Optional[int | str] = None,
) -> str:
    """Read a text file, optionally a specific line range.

    Refuses to read binary files or files larger than VIEW_FILE_MAX_BYTES.
    """
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: File '{path}' does not exist."
    if not os.path.isfile(abs_path):
        return f"Error: '{path}' is not a regular file."

    # Size guard.
    try:
        fsize = os.path.getsize(abs_path)
    except OSError:
        fsize = 0
    if fsize > VIEW_FILE_MAX_BYTES:
        return (
            f"Error: File '{path}' is {fsize:,} bytes which exceeds the "
            f"{VIEW_FILE_MAX_BYTES:,}-byte limit. Use grep_search to find "
            "specific sections, or view a line range with start_line/end_line."
        )

    # Binary guard.
    if _is_binary(abs_path):
        return f"Error: '{path}' appears to be a binary file."

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)
        start = max(1, int(start_line))
        end = min(total, int(end_line)) if end_line else total

        if start > total:
            return f"Error: start_line ({start}) exceeds total lines ({total})."

        selected = lines[start - 1 : end]
        formatted = [f"{idx:4d}: {line}" for idx, line in enumerate(selected, start=start)]
        header = f"--- File: {path} (Lines {start}-{end} of {total}) ---\n"
        return header + "".join(formatted) + "\n--- End of File ---"
    except Exception as e:
        return f"Error reading file: {e}"


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

def write_file(path: str, content: str) -> str:
    """Create or overwrite a file.  Creates parent directories as needed."""
    abs_path = os.path.abspath(path)
    try:
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"Success: Wrote {line_count} lines to '{path}'."
    except Exception as e:
        return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

def _normalize_ws(text: str) -> str:
    """Collapse all runs of whitespace to single spaces for fuzzy matching."""
    return " ".join(text.split())


def edit_file(path: str, search: str, replace: str) -> str:
    """Replace the first exact occurrence of *search* with *replace* in *path*.

    Falls back to whitespace-normalized matching if the exact match fails.
    """
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: File '{path}' does not exist."

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()

        # --- Exact match (preferred) ---
        if search in content:
            count = content.count(search)
            if count > 1:
                return (
                    f"Error: The search block matches {count} times in '{path}'. "
                    "Please provide a more unique search string."
                )
            new_content = content.replace(search, replace, 1)
        else:
            # --- Whitespace-normalised fallback ---
            norm_search = _normalize_ws(search)
            # Walk through lines and find a contiguous block that matches when normalised.
            lines = content.splitlines(keepends=True)
            search_lines = search.splitlines()
            match_start = None
            match_end = None

            for i in range(len(lines)):
                # Try matching search_lines starting at line i.
                candidate = "".join(lines[i : i + len(search_lines)])
                if _normalize_ws(candidate) == norm_search:
                    if match_start is not None:
                        return (
                            f"Error: Whitespace-normalised match found multiple times. "
                            "Please provide a more unique search string."
                        )
                    match_start = i
                    match_end = i + len(search_lines)

            if match_start is None:
                return (
                    f"Error: The search block was not found in '{path}'. "
                    "Use view_file to check the actual file content and ensure "
                    "whitespace/formatting matches exactly."
                )

            new_content = (
                "".join(lines[:match_start])
                + replace
                + "".join(lines[match_end:])
            )

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Success: Modified '{path}'."
    except Exception as e:
        return f"Error editing file: {e}"


# ---------------------------------------------------------------------------
# grep_search
# ---------------------------------------------------------------------------

def grep_search(pattern: str, path: str = ".") -> str:
    """Regex search across text files under *path*.  Results capped at GREP_MAX_RESULTS."""
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: Path '{path}' does not exist."

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"

    results: list[str] = []
    truncated = False

    for root, dirs, files in os.walk(abs_path):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
        for fname in sorted(files):
            if fname in _IGNORED_FILES:
                continue
            full = os.path.join(root, fname)

            # Skip binary files.
            if _is_binary(full):
                continue

            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                    for idx, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel = os.path.relpath(full, abs_path)
                            results.append(f"{rel}:{idx}: {line.rstrip()}")
                            if len(results) >= GREP_MAX_RESULTS:
                                truncated = True
                                break
            except Exception:
                continue

            if truncated:
                break
        if truncated:
            break

    if not results:
        return "No matches found."

    output = "\n".join(results)
    if truncated:
        output += f"\n... (truncated at {GREP_MAX_RESULTS} results)"
    return output


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------

def run_command(cmd: str) -> str:
    """Execute a shell command with a timeout.  Returns stdout, stderr, and timing."""
    try:
        start = time.monotonic()
        res = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
        elapsed = time.monotonic() - start

        parts: list[str] = [f"Exit code: {res.returncode} ({elapsed:.1f}s)"]

        if res.stdout:
            stdout = res.stdout
            if len(stdout) > 15_000:
                stdout = stdout[:15_000] + "\n... (stdout truncated)"
            parts.append(f"Stdout:\n{stdout}")

        if res.stderr:
            stderr = res.stderr
            if len(stderr) > 5_000:
                stderr = stderr[:5_000] + "\n... (stderr truncated)"
            parts.append(f"Stderr:\n{stderr}")

        if not res.stdout and not res.stderr:
            parts.append("(no output)")

        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {COMMAND_TIMEOUT} seconds."
    except Exception as e:
        return f"Error running command: {e}"
