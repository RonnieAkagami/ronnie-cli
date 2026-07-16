"""Auto-detect project type, dependencies, and structure for context injection."""

from __future__ import annotations

import os
import json
from typing import Optional


# ---------------------------------------------------------------------------
# Manifest detection rules
# ---------------------------------------------------------------------------

_PROJECT_MARKERS: list[tuple[str, str, str]] = [
    # (filename, project_type, dependency_key_or_parser_hint)
    ("package.json",     "Node.js / JavaScript",  "deps_from_package_json"),
    ("pyproject.toml",   "Python",                "deps_from_pyproject"),
    ("requirements.txt", "Python",                "deps_from_requirements"),
    ("Pipfile",          "Python (Pipenv)",        "deps_from_pipfile"),
    ("setup.py",         "Python",                None),
    ("Cargo.toml",       "Rust",                  "deps_from_cargo"),
    ("go.mod",           "Go",                    "deps_from_gomod"),
    ("Gemfile",          "Ruby",                  "deps_from_gemfile"),
    ("pom.xml",          "Java (Maven)",          None),
    ("build.gradle",     "Java/Kotlin (Gradle)",  None),
    ("composer.json",    "PHP (Composer)",        "deps_from_composer"),
    ("mix.exs",          "Elixir",               None),
    ("CMakeLists.txt",   "C/C++ (CMake)",         None),
    ("Makefile",         "Make-based project",    None),
]

_TOOLING_FILES: dict[str, str] = {
    ".venv":          "Python venv present — use `.venv/bin/python`",
    "venv":           "Python venv present — use `venv/bin/python`",
    "node_modules":   "node_modules installed",
    "Dockerfile":     "Dockerized project",
    "docker-compose.yml":  "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
    ".env":           ".env file present",
    "Makefile":       "Makefile available",
    "Taskfile.yml":   "Taskfile available",
    ".eslintrc.js":   "ESLint configured",
    ".eslintrc.json": "ESLint configured",
    "tsconfig.json":  "TypeScript project",
    "jest.config.js": "Jest testing configured",
    "pytest.ini":     "Pytest configured",
    "setup.cfg":      "Setup.cfg present",
    ".flake8":        "Flake8 configured",
    "tailwind.config.js":  "Tailwind CSS",
    "tailwind.config.ts":  "Tailwind CSS",
    "next.config.js": "Next.js project",
    "next.config.ts": "Next.js project",
    "vite.config.ts": "Vite project",
    "vite.config.js": "Vite project",
    "webpack.config.js": "Webpack configured",
}


# ---------------------------------------------------------------------------
# Dependency parsers — lightweight, no external libs
# ---------------------------------------------------------------------------

def _read_file_safe(path: str, max_bytes: int = 50_000) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_bytes)
    except Exception:
        return None


def _deps_from_package_json(path: str) -> list[str]:
    raw = _read_file_safe(path)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        deps = list(data.get("dependencies", {}).keys())
        deps += list(data.get("devDependencies", {}).keys())
        return deps[:20]
    except Exception:
        return []


def _deps_from_pyproject(path: str) -> list[str]:
    raw = _read_file_safe(path)
    if not raw:
        return []
    deps: list[str] = []
    in_deps = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies") and "=" in stripped:
            in_deps = True
            continue
        if in_deps:
            if stripped == "]":
                break
            # Extract package name from "package>=1.0.0"
            cleaned = stripped.strip('", ')
            if cleaned:
                name = cleaned.split(">=")[0].split("==")[0].split("<")[0].split(">")[0].split("[")[0].strip()
                if name and not name.startswith("#"):
                    deps.append(name)
    return deps[:20]


def _deps_from_requirements(path: str) -> list[str]:
    raw = _read_file_safe(path)
    if not raw:
        return []
    deps: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = line.split(">=")[0].split("==")[0].split("<")[0].split(">")[0].split("[")[0].strip()
        if name:
            deps.append(name)
    return deps[:20]


def _deps_from_cargo(path: str) -> list[str]:
    raw = _read_file_safe(path)
    if not raw:
        return []
    deps: list[str] = []
    in_deps = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped == "[dependencies]" or stripped == "[dev-dependencies]":
            in_deps = True
            continue
        if in_deps:
            if stripped.startswith("["):
                in_deps = False
                continue
            if "=" in stripped:
                name = stripped.split("=")[0].strip()
                if name:
                    deps.append(name)
    return deps[:20]


def _deps_from_gomod(path: str) -> list[str]:
    raw = _read_file_safe(path)
    if not raw:
        return []
    deps: list[str] = []
    in_require = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("require"):
            in_require = True
            continue
        if in_require:
            if stripped == ")":
                in_require = False
                continue
            parts = stripped.split()
            if parts:
                deps.append(parts[0].split("/")[-1])  # short name
    return deps[:20]


def _deps_from_gemfile(path: str) -> list[str]:
    raw = _read_file_safe(path)
    if not raw:
        return []
    deps: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("gem "):
            parts = stripped.split("'")
            if len(parts) >= 2:
                deps.append(parts[1])
            else:
                parts = stripped.split('"')
                if len(parts) >= 2:
                    deps.append(parts[1])
    return deps[:20]


def _deps_from_pipfile(path: str) -> list[str]:
    raw = _read_file_safe(path)
    if not raw:
        return []
    deps: list[str] = []
    in_packages = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped in ("[packages]", "[dev-packages]"):
            in_packages = True
            continue
        if in_packages:
            if stripped.startswith("["):
                in_packages = False
                continue
            if "=" in stripped:
                name = stripped.split("=")[0].strip()
                if name:
                    deps.append(name)
    return deps[:20]


def _deps_from_composer(path: str) -> list[str]:
    raw = _read_file_safe(path)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        deps = list(data.get("require", {}).keys())
        deps += list(data.get("require-dev", {}).keys())
        return [d for d in deps if d != "php"][:20]
    except Exception:
        return []


_PARSER_MAP = {
    "deps_from_package_json": _deps_from_package_json,
    "deps_from_pyproject":    _deps_from_pyproject,
    "deps_from_requirements": _deps_from_requirements,
    "deps_from_cargo":        _deps_from_cargo,
    "deps_from_gomod":        _deps_from_gomod,
    "deps_from_gemfile":      _deps_from_gemfile,
    "deps_from_pipfile":      _deps_from_pipfile,
    "deps_from_composer":     _deps_from_composer,
}


# ---------------------------------------------------------------------------
# File tree (shallow, depth=1)
# ---------------------------------------------------------------------------

_IGNORED_DIRS = frozenset((
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
    ".tox", ".next", ".eggs", "coverage", ".coverage",
))

def _shallow_tree(root: str) -> list[str]:
    """Return a compact depth-1 file tree of the project root."""
    entries: list[str] = []
    try:
        items = sorted(os.listdir(root))
    except Exception:
        return entries
    for item in items:
        if item.startswith(".") and item in (".DS_Store", ".git"):
            continue
        full = os.path.join(root, item)
        if os.path.isdir(full):
            if item in _IGNORED_DIRS:
                continue
            entries.append(f"  {item}/")
        else:
            entries.append(f"  {item}")
    return entries


# ---------------------------------------------------------------------------
# gitignore reader
# ---------------------------------------------------------------------------

def _read_gitignore(root: str) -> list[str]:
    """Return notable .gitignore patterns (skip boring defaults)."""
    path = os.path.join(root, ".gitignore")
    raw = _read_file_safe(path)
    if not raw:
        return []
    boring = {"*.pyc", "__pycache__/", ".DS_Store", "*.log", "node_modules/",
              ".venv/", "venv/", "dist/", "build/", "*.egg-info/", "*.egg-info",
              ".env", "coverage/", ".coverage", "*.o", "*.so"}
    patterns: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line in boring:
            continue
        patterns.append(line)
    return patterns[:10]


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan_project(root: str) -> str:
    """Scan the project at *root* and return a compact context string.

    This is injected into the system prompt so the model understands
    the project before writing any code.
    """
    root = os.path.abspath(root)
    sections: list[str] = []

    # --- Project type & dependencies ---
    project_type: Optional[str] = None
    all_deps: list[str] = []

    for filename, ptype, parser_key in _PROJECT_MARKERS:
        fpath = os.path.join(root, filename)
        if os.path.exists(fpath):
            if project_type is None:
                project_type = ptype
            if parser_key and parser_key in _PARSER_MAP:
                deps = _PARSER_MAP[parser_key](fpath)
                if deps:
                    all_deps.extend(deps)

    if project_type:
        sections.append(f"**Project type:** {project_type}")
    if all_deps:
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique: list[str] = []
        for d in all_deps:
            dl = d.lower()
            if dl not in seen:
                seen.add(dl)
                unique.append(d)
        sections.append(f"**Dependencies:** {', '.join(unique[:15])}")

    # --- Tooling signals ---
    tooling: list[str] = []
    for fname, desc in _TOOLING_FILES.items():
        if os.path.exists(os.path.join(root, fname)):
            tooling.append(desc)
    # Deduplicate descriptions.
    if tooling:
        sections.append("**Tooling:** " + "; ".join(sorted(set(tooling))))

    # --- File tree ---
    tree = _shallow_tree(root)
    if tree:
        sections.append("**File tree (root):**\n" + "\n".join(tree))

    # --- Notable gitignore patterns ---
    gi = _read_gitignore(root)
    if gi:
        sections.append("**Notable .gitignore:** " + ", ".join(gi))

    if not sections:
        return ""

    return "# Project Context\n" + "\n".join(sections)
