"""Ronnie CLI — entry point, REPL, auto-update, and banner."""

from __future__ import annotations

import os
import sys
import time
import readline
import atexit
import threading
from typing import List, Dict

import ollama
from rich.panel import Panel
from rich.prompt import Prompt

from ronnie.config import console, MODEL_NAME, history_file
from ronnie.prompts import build_system_prompt
from ronnie.agent import run_agentic_loop, session_tokens, _fmt_tokens
from ronnie.tools import undo_last, undo_stack_depth, get_session_changes, clear_session_changes

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

    zip_url = f"https://github.com/RonnieAkagami/ronnie-cli/archive/{remote_sha}.zip"
    options_list = [
        ["install", "--upgrade", "--no-cache-dir", zip_url],
        ["install", "--upgrade", "--no-cache-dir", zip_url, "--break-system-packages"],
        ["install", "--upgrade", "--no-cache-dir", zip_url, "--user"],
        ["install", "--upgrade", "--no-cache-dir", zip_url, "--user", "--break-system-packages"],
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
# Multiline input helper
# ---------------------------------------------------------------------------

def _read_multiline(prompt_str: str) -> str:
    """Read user input with support for multiline mode.

    - Type ``\"\"\"`` to enter multiline mode; type ``\"\"\"`` again to submit.
    - End a line with ``\\`` to continue on the next line.
    """
    first_line = Prompt.ask(prompt_str)
    first_line_stripped = first_line.strip()

    # --- Triple-quote multiline mode ---
    if first_line_stripped == '"""':
        lines: list[str] = []
        console.print("[dim]  (multiline mode — type \"\"\" on its own line to submit)[/dim]")
        while True:
            try:
                line = Prompt.ask("[dim]  ...[/dim]")
            except (KeyboardInterrupt, EOFError):
                break
            if line.strip() == '"""':
                break
            lines.append(line)
        return "\n".join(lines)

    # --- Backslash continuation ---
    if first_line_stripped.endswith("\\"):
        lines = [first_line_stripped[:-1]]
        while True:
            try:
                line = Prompt.ask("[dim]  ...[/dim]")
            except (KeyboardInterrupt, EOFError):
                break
            stripped = line.rstrip()
            if stripped.endswith("\\"):
                lines.append(stripped[:-1])
            else:
                lines.append(stripped)
                break
        return " ".join(lines)

    return first_line


# ---------------------------------------------------------------------------
# Session user message history (for /history)
# ---------------------------------------------------------------------------

_user_messages: list[str] = []


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
[bold cyan]Available Commands[/bold cyan]
  [bold yellow]/clear[/bold yellow]     Clear conversation history
  [bold yellow]/undo[/bold yellow]      Undo the last file modification
  [bold yellow]/diff[/bold yellow]      Show all file changes this session
  [bold yellow]/history[/bold yellow]   Show conversation history
  [bold yellow]/tokens[/bold yellow]    Show session token usage
  [bold yellow]/help[/bold yellow]      Show this help message
  [bold yellow]/model[/bold yellow]     Show the current model name
  [bold yellow]/exit[/bold yellow]      Exit Ronnie (also: /quit)

[bold cyan]Input Modes[/bold cyan]
  [dim]Type[/dim] [bold yellow]\"\"\"[/bold yellow] [dim]to enter multiline mode (close with another[/dim] [bold yellow]\"\"\"[/bold yellow][dim])[/dim]
  [dim]End a line with[/dim] [bold yellow]\\\\[/bold yellow] [dim]to continue on the next line[/dim]
"""


def _handle_slash_command(
    cmd: str, messages: List[Dict[str, str]], system_prompt: str
) -> bool:
    """Handle a slash command.  Returns True if the input was consumed."""
    lower = cmd.lower()

    if lower in ("/exit", "/quit", "exit", "quit"):
        # Show session summary on exit.
        if session_tokens.total > 0:
            console.print(
                f"[dim]Session: {_fmt_tokens(session_tokens.total)} tokens "
                f"({_fmt_tokens(session_tokens.prompt_tokens)} in, "
                f"{_fmt_tokens(session_tokens.completion_tokens)} out)[/dim]"
            )
        console.print("[info]Goodbye![/info]")
        raise SystemExit(0)

    if lower == "/clear":
        messages.clear()
        messages.append({"role": "system", "content": system_prompt})
        session_tokens.reset()
        _user_messages.clear()
        clear_session_changes()
        console.print("[success]Conversation cleared. Tokens & history reset.[/success]")
        return True

    if lower == "/help":
        console.print(_HELP_TEXT)
        return True

    if lower == "/model":
        console.print(f"[info]Current model:[/info] [bold]{MODEL_NAME}[/bold]")
        return True

    if lower == "/undo":
        depth = undo_stack_depth()
        if depth == 0:
            console.print("[warning]Nothing to undo.[/warning]")
        else:
            result = undo_last()
            console.print(f"[success]{result}[/success]")
            remaining = undo_stack_depth()
            if remaining > 0:
                console.print(f"[dim]  ({remaining} more undo{'s' if remaining != 1 else ''} available)[/dim]")
        return True

    if lower == "/tokens":
        if session_tokens.total == 0:
            console.print("[dim]No tokens used yet in this session.[/dim]")
        else:
            console.print(
                f"[bold cyan]Session Token Usage[/bold cyan]\n"
                f"  Prompt (input):     [bold]{_fmt_tokens(session_tokens.prompt_tokens)}[/bold]\n"
                f"  Completion (output): [bold]{_fmt_tokens(session_tokens.completion_tokens)}[/bold]\n"
                f"  Total:              [bold]{_fmt_tokens(session_tokens.total)}[/bold]"
            )
        return True

    if lower == "/diff":
        changes = get_session_changes()
        if not changes:
            console.print("[dim]No file changes in this session.[/dim]")
        else:
            # Group by file path, count operations.
            from collections import Counter
            file_ops: dict[str, list[str]] = {}
            for c in changes:
                file_ops.setdefault(c.path, []).append(c.operation)

            console.print(f"[bold cyan]Session Changes[/bold cyan] ({len(changes)} operation{'s' if len(changes) != 1 else ''}):")
            for path, ops in file_ops.items():
                created = ops.count("created")
                modified = ops.count("modified")
                if created:
                    icon = "📝"
                    detail = "Created"
                elif modified > 1:
                    icon = "✏️ "
                    detail = f"Modified ({modified} edits)"
                else:
                    icon = "✏️ "
                    detail = "Modified"
                console.print(f"  {icon} [bold]{path}[/bold] — {detail}")
        return True

    if lower == "/history":
        if not _user_messages:
            console.print("[dim]No messages yet in this session.[/dim]")
        else:
            console.print("[bold cyan]Conversation History[/bold cyan]")
            for i, msg in enumerate(_user_messages, start=1):
                # Truncate long messages for display.
                display = msg if len(msg) <= 80 else msg[:77] + "..."
                console.print(f"  [dim]{i}.[/dim] {display}")
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

Commands: [bold yellow]/help[/bold yellow] · [bold yellow]/clear[/bold yellow] · [bold yellow]/undo[/bold yellow] · [bold yellow]/diff[/bold yellow] · [bold yellow]/exit[/bold yellow]
"""


def main() -> None:
    _start = time.monotonic()

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

    # Banner + startup time.
    startup_ms = (time.monotonic() - _start) * 1000
    banner = _BANNER.format(model=MODEL_NAME)
    console.print(Panel(banner, border_style="bright_magenta"))
    console.print(f"[dim]  Ready in {startup_ms:.0f}ms · Type [bold yellow]\"\"\"[/bold yellow] for multiline input[/dim]")

    # Build system prompt with CWD + project context injected.
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
            user_input = _read_multiline(f"\n[bold cyan]ronnie[/bold cyan] [dim]{short_cwd}[/dim]")
            user_input = user_input.strip()
            if not user_input:
                continue

            # Slash commands.
            if _handle_slash_command(user_input, messages, system_prompt):
                continue

            # Track user message for /history.
            _user_messages.append(user_input)

            # Normal user message → agent.
            messages.append({"role": "user", "content": user_input})
            run_agentic_loop(messages, client)

        except KeyboardInterrupt:
            console.print("\n[warning]Interrupted. Type /exit to exit.[/warning]")
        except SystemExit:
            break
        except EOFError:
            console.print("\n[info]Goodbye![/info]")
            break
