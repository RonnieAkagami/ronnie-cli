import os
from rich.console import Console
from rich.theme import Theme

MODEL_NAME = "ornith:9b"

# Custom Theme
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "magenta",
    "danger": "bold red",
    "success": "bold green",
    "banner": "bold bright_magenta",
})
console = Console(theme=custom_theme)

# Enable CLI history
history_file = os.path.expanduser("~/.ronnie_history")
