"""Ronnie CLI — entry point, REPL, auto-update, and banner."""

import os
import sys
import readline
import atexit
import threading
from typing import List, Dict

import ollama
from rich.panel import Panel
from rich.prompt import Prompt

from ronnie.config import console, MODEL_NAME, history_file
from ronnie.prompts import build_system_prompt
from ronnie.agent import run_agentic_loop

# ---------------------------------------------------------------------------
# CLI history
# ---------------------------------------------------------------------------

try:
    readline.read_history_file(history_file)
except Exception:
    pass

try:
    readline.set_history_length(1000)
except Exception:
    pass


def _save_history() -> None:
    try:
        readline.write_history_file(history_file)
    except Exception:
        pass


atexit.register(_save_history)


# ---------------------------------------------------------------------------
# Auto-update (runs in background thread so the CLI appears instantly)
# ---------------------------------------------------------------------------

def _check_and_update_bg() -> None:
    """Background auto-update check.  Prints a notice if an update is applied."""
    # Skip in dev mode.
    is_dev = (
        os.environ.get("RONNIE_NO_AUTO_UPDATE") == "1"
        or os.environ.get("RONNIE_DEV") == "1"
        or os.path.exists(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "setup.py")
        )
    )
    if is_dev:
        return

    commit_file = os.path.expanduser("~/.ronnie_commit")

    # 1. Fetch remote commit SHA.
    remote_sha = None
    url = "https://github.com/RonnieAkagami/ronnie-cli.git/info/refs?service=git-upload-pack"
    try:
        import urllib.request
        import re as _re

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            m = _re.search(r"([0-9a-fA-F]{40})\s+refs/heads/main", content)
            if m:
                remote_sha = m.group(1)
    except Exception:
        return

    if not remote_sha:
        return

    # 2. Compare with local SHA.
    local_sha = None
    if os.path.exists(commit_file):
        try:
            with open(commit_file) as f:
                local_sha = f.read().strip()
        except Exception:
            pass

    if remote_sha == local_sha:
        return

    # 3. Update.
    console.print(
        f"[info][*] New update found — updating to {remote_sha[:7]}...[/info]"
    )

    import subprocess

    zip_url = "https://github.com/RonnieAkagami/ronnie-cli/archive/refs/heads/main.zip"
    options_list = [
        ["install", "--upgrade", zip_url],
        ["install", "--upgrade", zip_url, "--break-system-packages"],
        ["install", "--upgrade", zip_url, "--user"],
        ["install", "--upgrade", zip_url, "--user", "--break-system-packages"],
    ]

    success = False
    for opts in options_list:
        try:
            res = subprocess.run(
                [sys.executable, "-m", "pip"] + opts,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if res.returncode == 0:
                success = True
                break
        except Exception:
            continue

    if success:
        try:
            with open(commit_file, "w") as f:
                f.write(remote_sha)
        except Exception:
            pass

        # Update the executable script if writable.
        exe_path = sys.argv[0]
        if os.path.exists(exe_path) and os.access(exe_path, os.W_OK):
            try:
                import urllib.request as _ur

                raw_url = "https://raw.githubusercontent.com/RonnieAkagami/ronnie-cli/main/ronnie.py"
                with _ur.urlopen(raw_url, timeout=2) as r:
                    new_code = r.read()
                    if new_code:
                        with open(exe_path, "wb") as f:
                            f.write(new_code)
            except Exception:
                pass

        console.print(
            "[success][+] Updated successfully. Restart `ronnie` to use the new version.[/success]"
        )
    else:
        console.print("[warning][!] Auto-update failed. Skipping.[/warning]")


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
[bold cyan]Available Commands[/bold cyan]
  [bold yellow]/clear[/bold yellow]   Clear conversation history
  [bold yellow]/help[/bold yellow]    Show this help message
  [bold yellow]/model[/bold yellow]   Show the current model name
  [bold yellow]/exit[/bold yellow]    Exit Ronnie (also: /quit)
"""


def _handle_slash_command(
    cmd: str, messages: List[Dict[str, str]], system_prompt: str
) -> bool:
    """Handle a slash command.  Returns True if the input was consumed."""
    lower = cmd.lower()

    if lower in ("/exit", "/quit", "exit", "quit"):
        console.print("[info]Goodbye![/info]")
        raise SystemExit(0)

    if lower == "/clear":
        messages.clear()
        messages.append({"role": "system", "content": system_prompt})
        console.print("[success]Conversation history cleared.[/success]")
        return True

    if lower == "/help":
        console.print(_HELP_TEXT)
        return True

    if lower == "/model":
        console.print(f"[info]Current model:[/info] [bold]{MODEL_NAME}[/bold]")
        return True

    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_BANNER = """\
[banner]██████╗  ██████╗ ███╗   ██╗███╗   ██╗██╗███████╗
██╔══██╗██╔═══██╗████╗  ██║████╗  ██║██║██╔════╝
██████╔╝██║   ██║██╔██╗ ██║██╔██╗ ██║██║█████╗  
██╔══██╗██║   ██║██║╚██╗██║██║╚██╗██║██║██╔══╝  
██║  ██║╚██████╔╝██║ ╚████║██║ ╚████║██║███████╗
╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═══╝╚═╝╚══════╝[/banner]
 
[bold cyan]Ronnie CLI — Local Agentic Coding Partner[/bold cyan]
Powered by [bold magenta]{model}[/bold magenta]

Commands: [bold yellow]/help[/bold yellow] · [bold yellow]/clear[/bold yellow] · [bold yellow]/exit[/bold yellow]
"""


def main() -> None:
    # Fire auto-update in background so the CLI appears instantly.
    update_thread = threading.Thread(target=_check_and_update_bg, daemon=True)
    update_thread.start()

    # Connect to Ollama.
    client = ollama.Client()
    try:
        client.list()
    except Exception as e:
        console.print(
            Panel(
                f"[red]Error connecting to Ollama: {e}[/red]\n\n"
                f"Make sure Ollama is running ([bold]ollama serve[/bold]) "
                f"and the model [bold]{MODEL_NAME}[/bold] is pulled "
                f"([bold]ollama pull {MODEL_NAME}[/bold]).",
                title="Connection Error",
            )
        )
        sys.exit(1)

    # Banner.
    console.print(Panel(_BANNER.format(model=MODEL_NAME), border_style="bright_magenta"))

    # Build system prompt with CWD injected.
    cwd = os.getcwd()
    system_prompt = build_system_prompt(cwd)
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    # Direct prompt mode (e.g. `ronnie "fix the bug"`)
    direct_prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    if direct_prompt:
        console.print(f"[bold]Direct Input:[/bold] {direct_prompt}")
        messages.append({"role": "user", "content": direct_prompt})
        run_agentic_loop(messages, client)
        sys.exit(0)

    # Interactive REPL.
    short_cwd = os.path.basename(cwd) or cwd
    while True:
        try:
            user_input = Prompt.ask(f"\n[bold cyan]ronnie[/bold cyan] [dim]{short_cwd}[/dim]")
            user_input = user_input.strip()
            if not user_input:
                continue

            # Slash commands.
            if _handle_slash_command(user_input, messages, system_prompt):
                continue

            # Normal user message → agent.
            messages.append({"role": "user", "content": user_input})
            run_agentic_loop(messages, client)

        except KeyboardInterrupt:
            console.print("\n[warning]KeyboardInterrupt. Type /exit to exit.[/warning]")
        except SystemExit:
            break
        except EOFError:
            console.print("\n[info]Goodbye![/info]")
            break
