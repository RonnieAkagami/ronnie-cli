"""Core agentic loop — streams model output, parses tool calls, executes them."""

from __future__ import annotations

import os
import re
import difflib
import time
import random
import threading
from typing import List, Dict, Any, Tuple

import ollama

from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.prompt import Prompt
from rich.markdown import Markdown
from rich.spinner import Spinner

from ronnie.config import (
    console,
    MODEL_NAME,
    MAX_ITERATIONS,
    CONTEXT_MAX_MESSAGES,
    MAX_TOOL_OUTPUT_CHARS,
)
from ronnie.tools import list_dir, view_file, write_file, edit_file, grep_search, run_command


# ---------------------------------------------------------------------------
# XML tool-call parser
# ---------------------------------------------------------------------------

def parse_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extract ``<tool_call name="…">…</tool_call>`` blocks from *text*.

    Uses greedy matching for ``content``, ``search``, and ``replace`` tags to
    handle code containing angle brackets (``x < 5``, ``</div>``, etc.).
    """
    tool_call_pattern = re.compile(
        r"<tool_call\s+name=\"([^\"]+)\"\s*>(.*?)</tool_call>", re.DOTALL
    )
    calls: list[dict[str, Any]] = []

    for match in tool_call_pattern.finditer(text):
        tool_name = match.group(1)
        body = match.group(2)
        args: dict[str, str] = {}

        # Greedy match for large-body tags (content/search/replace).
        for tag in ("content", "search", "replace"):
            tag_re = re.compile(rf"<{tag}>(.*)</{tag}>", re.DOTALL)
            m = tag_re.search(body)
            if m:
                args[tag] = m.group(1)

        # Non-greedy match for short parameter tags.
        simple_re = re.compile(r"<([a-zA-Z0-9_]+)>(.*?)</\1>", re.DOTALL)
        for m in simple_re.finditer(body):
            pname = m.group(1)
            if pname not in args:  # don't overwrite greedy matches
                args[pname] = m.group(2).strip()

        calls.append({"name": tool_name, "args": args, "raw": match.group(0)})

    return calls


# ---------------------------------------------------------------------------
# Diff helper
# ---------------------------------------------------------------------------

def get_diff(old_text: str, new_text: str, filename: str) -> Text:
    """Generate a coloured unified diff."""
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    )
    result = Text()
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            result.append(line, style="green")
        elif line.startswith("-") and not line.startswith("---"):
            result.append(line, style="red")
        elif line.startswith("@@"):
            result.append(line, style="cyan")
        else:
            result.append(line, style="dim")
    return result


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def execute_tool(
    name: str,
    args: Dict[str, Any],
    always_approve: bool,
) -> Tuple[str, bool, bool]:
    """Execute a single tool call.

    Returns ``(response_xml, should_cancel, always_approve)``.
    The third element propagates the "always" approval choice back to the loop.
    """
    is_modifying = name in ("write_file", "edit_file", "run_command")

    # ------ Validate required args ------
    if name in ("write_file", "edit_file", "view_file") and not args.get("path"):
        return (
            _tool_resp(name, "error", 'Missing required "path" argument.'),
            False,
            always_approve,
        )
    if name == "run_command" and not args.get("cmd"):
        return (
            _tool_resp(name, "error", 'Missing required "cmd" argument.'),
            False,
            always_approve,
        )

    # ------ Confirmation for modifying tools ------
    if is_modifying and not always_approve:
        console.print()

        if name == "run_command":
            console.print(
                Panel(
                    f"[yellow]Command:[/yellow] {args.get('cmd')}",
                    title="Proposed command execution",
                    border_style="yellow",
                )
            )
        elif name == "write_file":
            _show_write_diff(args)
        elif name == "edit_file":
            _show_edit_diff(args)

        console.print(
            f"[warning]Tool Call:[/warning] [bold cyan]{name}[/bold cyan] requires permission."
        )
        choice = Prompt.ask(
            "Confirm execution? "
            "([bold green]y[/bold green]es / [bold red]n[/bold red]o / "
            "[bold yellow]a[/bold yellow]lways / [bold magenta]c[/bold magenta]ancel)",
            choices=["y", "n", "a", "c"],
            default="y",
        )

        if choice == "n":
            return (
                _tool_resp(name, "error", "Execution declined by user."),
                False,
                always_approve,
            )
        if choice == "c":
            return ("", True, always_approve)
        if choice == "a":
            always_approve = True

    # ------ Execute ------
    console.print(f"⚙️  [info]Executing {name}...[/info]")

    try:
        if name == "list_dir":
            result = list_dir(args.get("path", "."), depth=args.get("depth", 2))
        elif name == "view_file":
            result = view_file(
                args.get("path"),
                start_line=args.get("start_line", 1),
                end_line=args.get("end_line"),
            )
        elif name == "write_file":
            result = write_file(args.get("path"), args.get("content", ""))
        elif name == "edit_file":
            result = edit_file(
                args.get("path"), args.get("search", ""), args.get("replace", "")
            )
        elif name == "grep_search":
            result = grep_search(args.get("pattern"), args.get("path", "."))
        elif name == "run_command":
            result = run_command(args.get("cmd"))
        else:
            result = f"Error: Unknown tool '{name}'"

        status = "error" if result.startswith("Error") else "success"
    except Exception as e:
        result = f"Unexpected error: {e}"
        status = "error"

    # Truncate very large outputs to avoid blowing up context.
    if len(result) > MAX_TOOL_OUTPUT_CHARS:
        result = result[:MAX_TOOL_OUTPUT_CHARS] + "\n... (output truncated)"

    return (_tool_resp(name, status, result), False, always_approve)


def _tool_resp(name: str, status: str, message: str) -> str:
    return (
        f'<tool_response name="{name}">'
        f"<status>{status}</status>"
        f"<message>{message}</message>"
        f"</tool_response>"
    )


def _show_write_diff(args: Dict[str, Any]) -> None:
    path = args.get("path", "")
    content = args.get("content", "")
    abs_path = os.path.abspath(path)
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                old = f.read()
            console.print(
                Panel(get_diff(old, content, path), title=f"Overwriting {path}", border_style="yellow")
            )
        except Exception:
            console.print(Panel(f"Overwriting {path}", title="Proposed file write", border_style="yellow"))
    else:
        console.print(
            Panel(get_diff("", content, path), title=f"Creating {path}", border_style="green")
        )


def _show_edit_diff(args: Dict[str, Any]) -> None:
    path = args.get("path", "")
    search = args.get("search", "")
    replace = args.get("replace", "")
    abs_path = os.path.abspath(path)
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read()
            if search in content:
                new = content.replace(search, replace, 1)
                console.print(
                    Panel(get_diff(content, new, path), title=f"Editing {path}", border_style="yellow")
                )
            else:
                console.print(
                    Panel("[red]Search block not found exactly in file.[/red]", title="Diff error", border_style="red")
                )
        except Exception as e:
            console.print(Panel(f"Error: {e}", title="Diff error", border_style="red"))


# ---------------------------------------------------------------------------
# Context management
# ---------------------------------------------------------------------------

def _prune_context(messages: List[Dict[str, str]]) -> None:
    """Trim older tool-exchange pairs when the conversation grows too long.

    Keeps the system prompt (index 0), the last user message, and recent
    exchanges intact.  Older tool response messages are summarised.
    """
    if len(messages) <= CONTEXT_MAX_MESSAGES:
        return

    # Keep system prompt + the most recent 20 messages.  Summarise the rest.
    keep_recent = 20
    cutoff = len(messages) - keep_recent

    # Don't touch index 0 (system prompt).
    for i in range(1, cutoff):
        msg = messages[i]
        content = msg.get("content", "")
        # Compress long tool response messages.
        if msg["role"] == "user" and "<tool_response" in content and len(content) > 500:
            # Extract just the status lines.
            statuses = re.findall(r'<tool_response name="([^"]+)">\s*<status>(\w+)</status>', content)
            if statuses:
                summary = "; ".join(f"{n}: {s}" for n, s in statuses)
                messages[i] = {"role": "user", "content": f"[Earlier tool results: {summary}]"}
        elif msg["role"] == "assistant" and len(content) > 1000:
            # Trim very long assistant messages, keeping the first 300 chars.
            messages[i] = {
                "role": "assistant",
                "content": content[:300] + "\n... (earlier response trimmed)",
            }


# ---------------------------------------------------------------------------
# Spinner / thinking UI
# ---------------------------------------------------------------------------

_THINKING_WORDS = [
    "Pondering", "Analyzing", "Synthesizing", "Reflecting",
    "Deliberating", "Contemplating", "Evaluating", "Deciphering",
    "Formulating", "Processing", "Reasoning",
]


# ---------------------------------------------------------------------------
# Main agentic loop
# ---------------------------------------------------------------------------

def run_agentic_loop(messages: List[Dict[str, str]], client: ollama.Client) -> bool:
    """Run the agent loop until the model stops calling tools or we hit the cap.

    Returns ``True`` if completed normally, ``False`` if cancelled.
    """
    always_approve = False
    iteration = 0

    while iteration < MAX_ITERATIONS:
        iteration += 1
        response_text = ""

        try:
            # --- Spinner setup ---
            current_word = random.choice(_THINKING_WORDS)
            thinking = True

            spinner = Spinner("dots", text=Text(f" {current_word}...", style="bold bright_magenta"))
            console.print()

            with Live(spinner, refresh_per_second=8, console=console) as live:
                # Background thread cycles the word every 4s.
                def _cycle_spinner() -> None:
                    nonlocal current_word
                    last_change = time.monotonic()
                    while thinking:
                        if time.monotonic() - last_change >= 4.0:
                            remaining = [w for w in _THINKING_WORDS if w != current_word]
                            current_word = random.choice(remaining) if remaining else current_word
                            last_change = time.monotonic()
                        live.update(
                            Spinner("dots", text=Text(f" {current_word}...", style="bold bright_magenta"))
                        )
                        time.sleep(0.5)  # 0.5s vs original 0.1s — much less CPU

                t = threading.Thread(target=_cycle_spinner, daemon=True)
                t.start()

                try:
                    stream = client.chat(
                        model=MODEL_NAME,
                        messages=messages,
                        stream=True,
                        think=False,
                    )
                    stream_iter = iter(stream)
                except Exception as e:
                    thinking = False
                    t.join(timeout=1.0)
                    raise

                # --- Stream chunks, hiding raw XML tool calls ---
                in_tool_call = False

                for chunk in stream_iter:
                    content = chunk.get("message", {}).get("content", "") or ""
                    if not content:
                        continue

                    if thinking:
                        thinking = False
                        t.join(timeout=1.0)

                    response_text += content

                    # Check if we've entered a tool call block.
                    tool_start = response_text.find("<tool_call")
                    if tool_start != -1:
                        in_tool_call = True
                        prose = response_text[:tool_start].strip()
                        if prose:
                            live.update(Markdown(prose))
                        break
                    else:
                        live.update(Markdown(response_text.strip()))

                if thinking:
                    thinking = False
                    t.join(timeout=1.0)

            # If we broke out of the Live context because of a tool call,
            # consume the rest of the stream silently.
            if in_tool_call:
                console.print("[info]⚙️  Formulating tool call(s)...[/info]")
                for chunk in stream_iter:
                    content = chunk.get("message", {}).get("content", "") or ""
                    response_text += content

        except KeyboardInterrupt:
            raise  # Let the CLI handle it.
        except Exception as e:
            console.print(f"\n[danger]Ollama error:[/danger] {e}")
            return False

        # --- Record assistant message ---
        messages.append({"role": "assistant", "content": response_text})

        # --- Parse tool calls ---
        calls = parse_tool_calls(response_text)
        if not calls:
            break  # Model is done — no tools invoked.

        # --- Execute tool calls ---
        tool_responses: list[str] = []
        total = len(calls)

        for idx, call in enumerate(calls, start=1):
            if total > 1:
                console.print(f"[dim]  Tool {idx}/{total}[/dim]")

            resp, should_cancel, always_approve = execute_tool(
                call["name"], call["args"], always_approve
            )

            if should_cancel:
                console.print("[warning]Task cancelled by user.[/warning]")
                messages.pop()  # Remove assistant msg that triggered cancellation.
                return False

            tool_responses.append(resp)

        # Feed tool results back as a single user message.
        combined = "\n".join(tool_responses)
        messages.append({"role": "user", "content": combined})

        # Prune context if it's getting long.
        _prune_context(messages)

    else:
        console.print(
            f"[warning]⚠ Agent reached the iteration limit ({MAX_ITERATIONS}). "
            "Stopping to avoid an infinite loop.[/warning]"
        )

    return True
