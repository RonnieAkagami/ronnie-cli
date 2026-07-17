import os
import platform
from rich.console import Console
from rich.theme import Theme

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
MODEL_NAME: str = os.environ.get("RONNIE_MODEL", "ornith:9b")

# ---------------------------------------------------------------------------
# Agent loop limits
# ---------------------------------------------------------------------------
MAX_ITERATIONS: int = int(os.environ.get("RONNIE_MAX_ITERATIONS", "25"))
# When the conversation exceeds this many messages, older tool exchanges are
# summarised to stay within the model's context window.
CONTEXT_MAX_MESSAGES: int = int(os.environ.get("RONNIE_CONTEXT_MAX_MESSAGES", "40"))
# Maximum characters kept from a single tool response before truncation.
MAX_TOOL_OUTPUT_CHARS: int = 12_000

# ---------------------------------------------------------------------------
# Tool defaults
# ---------------------------------------------------------------------------
LIST_DIR_DEFAULT_DEPTH: int = 2
GREP_MAX_RESULTS: int = 50
VIEW_FILE_MAX_BYTES: int = 100_000  # ~100 KB
COMMAND_TIMEOUT: int = 300  # seconds

# ---------------------------------------------------------------------------
# Environment info (injected into the system prompt at runtime)
# ---------------------------------------------------------------------------
OS_NAME: str = platform.system()  # "Darwin", "Linux", "Windows"

# ---------------------------------------------------------------------------
# UX preferences
# ---------------------------------------------------------------------------
# Ring terminal bell when the agent finishes a task (set RONNIE_BELL=0 to mute).
BELL_ENABLED: bool = os.environ.get("RONNIE_BELL", "1") != "0"

# ---------------------------------------------------------------------------
# Rich theme & console
# ---------------------------------------------------------------------------
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "magenta",
    "danger": "bold red",
    "success": "bold green",
    "banner": "bold bright_magenta",
})
console = Console(theme=custom_theme)

# ---------------------------------------------------------------------------
# CLI history
# ---------------------------------------------------------------------------
history_file: str = os.path.expanduser("~/.ronnie_history")
