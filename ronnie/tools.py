"""Filesystem and shell tools for the Ronnie agent."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
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
# File backup / undo system
# ---------------------------------------------------------------------------

@dataclass
class _FileSnapshot:
    """A snapshot of a file before modification."""
    path: str             # absolute path
    content: str | None   # None means the file didn't exist (was created)
    operation: str        # "write_file" or "edit_file"


_undo_stack: deque[_FileSnapshot] = deque(maxlen=20)


def _backup_file(abs_path: str, operation: str) -> None:
    """Save current file content (or None if new) to the undo stack."""
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            content = None
    else:
        content = None
    _undo_stack.append(_FileSnapshot(path=abs_path, content=content, operation=operation))


def undo_last() -> str:
    """Undo the last file modification. Returns a status message."""
    if not _undo_stack:
        return "Nothing to undo."

    snap = _undo_stack.pop()
    rel = os.path.relpath(snap.path)

    if snap.content is None:
        # File was created — delete it.
        try:
            os.remove(snap.path)
            return f"Undone: Deleted newly created file '{rel}'."
        except Exception as e:
            return f"Undo failed: Could not delete '{rel}': {e}"
    else:
        # File was modified — restore content.
        try:
            with open(snap.path, "w", encoding="utf-8") as f:
                f.write(snap.content)
            return f"Undone: Restored '{rel}' to its previous state."
        except Exception as e:
            return f"Undo failed: Could not restore '{rel}': {e}"


def undo_stack_depth() -> int:
    """Return the number of undoable operations."""
    return len(_undo_stack)


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
    On file-not-found, lists the actual directory contents to help self-correct.
    """
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        # Self-correcting: show what files ARE in the directory.
        parent = os.path.dirname(abs_path)
        hint = ""
        if os.path.isdir(parent):
            try:
                siblings = sorted(os.listdir(parent))[:20]
                if siblings:
                    hint = (
                        f"\nFiles in '{os.path.relpath(parent)}': "
                        + ", ".join(siblings)
                    )
            except Exception:
                pass
        return f"Error: File '{path}' does not exist.{hint}"

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
    """Create or overwrite a file.  Creates parent directories as needed.
    Saves a backup for /undo before writing.
    """
    abs_path = os.path.abspath(path)
    try:
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Backup before writing.
        _backup_file(abs_path, "write_file")

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"Success: Wrote {line_count} lines to '{path}'."
    except PermissionError:
        return (
            f"Error: Permission denied writing to '{path}'. "
            "Try using run_command with `chmod` or `sudo` to fix permissions."
        )
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
    On failure, includes actual file lines to help the model self-correct.
    Saves a backup for /undo before writing.
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
            # Backup before modifying.
            _backup_file(abs_path, "edit_file")
            new_content = content.replace(search, replace, 1)
        else:
            # --- Whitespace-normalised fallback ---
            norm_search = _normalize_ws(search)
            lines = content.splitlines(keepends=True)
            search_lines = search.splitlines()
            match_start = None
            match_end = None

            for i in range(len(lines)):
                candidate = "".join(lines[i : i + len(search_lines)])
                if _normalize_ws(candidate) == norm_search:
                    if match_start is not None:
                        return (
                            "Error: Whitespace-normalised match found multiple times. "
                            "Please provide a more unique search string."
                        )
                    match_start = i
                    match_end = i + len(search_lines)

            if match_start is None:
                # Self-correcting: show the actual file content around where the
                # search might have been, so the model can see what's really there.
                file_lines = content.splitlines()
                preview_lines = file_lines[:15]
                preview = "\n".join(f"  {i+1}: {l}" for i, l in enumerate(preview_lines))
                suffix = f"\n  ... ({len(file_lines)} total lines)" if len(file_lines) > 15 else ""
                return (
                    f"Error: The search block was not found in '{path}'.\n"
                    f"Actual file content (first 15 lines):\n{preview}{suffix}\n"
                    "Use view_file to read the full file before retrying."
                )

            # Backup before modifying.
            _backup_file(abs_path, "edit_file")
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
        return (
            f"No matches found for pattern '{pattern}'."
            " Try a simpler or broader pattern, or check the search path."
        )

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

            # Self-correcting hints for common errors.
            stderr_lower = stderr.lower()
            if "command not found" in stderr_lower or "not found" in stderr_lower:
                parts.append(
                    "Hint: The command was not found. "
                    "Check if the tool is installed, or try installing it first."
                )
            elif "permission denied" in stderr_lower:
                parts.append(
                    "Hint: Permission denied. Try prefixing the command with `sudo`."
                )

        if not res.stdout and not res.stderr:
            parts.append("(no output)")

        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {COMMAND_TIMEOUT} seconds."
    except Exception as e:
        return f"Error running command: {e}"
