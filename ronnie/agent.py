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
    BELL_ENABLED,
)
from ronnie.tools import list_dir, view_file, write_file, edit_file, grep_search, run_command


# ---------------------------------------------------------------------------
# XML tool-call parser (resilient to common model mistakes)
# ---------------------------------------------------------------------------

def parse_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extract ``<tool_call name="…">…</tool_call>`` blocks from *text*.

    Resilient to common model quirks:
    - Single or double quotes on name attribute
    - Missing quotes: ``name=view_file``
    - Extra whitespace inside angle brackets
    - Unclosed ``</tool_call>`` tags (tries to recover)

    Uses greedy matching for ``content``, ``search``, and ``replace`` tags to
    handle code containing angle brackets (``x < 5``, ``</div>``, etc.).
    """
    # --- Phase 1: Fix up common malformations before parsing ---
    cleaned = text

    # Fix unclosed tool_call tags — if there's an opening <tool_call but no
    # closing </tool_call>, append one at the end.
    open_count = len(re.findall(r"<\s*tool_call[\s>]", cleaned))
    close_count = len(re.findall(r"</\s*tool_call\s*>", cleaned))
    if open_count > close_count:
        cleaned += "\n</tool_call>" * (open_count - close_count)

    # --- Phase 2: Extract tool calls with flexible pattern ---
    # Accept: name="x", name='x', name=x
    tool_call_pattern = re.compile(
        r"<\s*tool_call\s+name\s*=\s*[\"']?([^\"'>\s]+)[\"']?\s*>(.*?)<\s*/\s*tool_call\s*>",
        re.DOTALL,
    )
    calls: list[dict[str, Any]] = []

    for match in tool_call_pattern.finditer(cleaned):
        tool_name = match.group(1).strip()
        body = match.group(2)
        args: dict[str, str] = {}

        # Greedy match for large-body tags (content/search/replace).
        for tag in ("content", "search", "replace"):
            tag_re = re.compile(rf"<\s*{tag}\s*>(.*)<\s*/\s*{tag}\s*>", re.DOTALL)
            m = tag_re.search(body)
            if m:
                args[tag] = m.group(1)

        # Non-greedy match for short parameter tags.
        simple_re = re.compile(r"<\s*([a-zA-Z0-9_]+)\s*>(.*?)<\s*/\s*\1\s*>", re.DOTALL)
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
    _print_tool_label(name, args)

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


# Tool name → (icon, arg_key for display)
_TOOL_ICONS: dict[str, tuple[str, str | None]] = {
    "list_dir":     ("📂", "path"),
    "view_file":    ("👁 ", "path"),
    "write_file":   ("📝", "path"),
    "edit_file":    ("✏️ ", "path"),
    "grep_search":  ("🔍", "pattern"),
    "run_command":  ("▶ ", "cmd"),
}


def _print_tool_label(name: str, args: Dict[str, Any]) -> None:
    """Print a compact, icon-prefixed tool label with the key argument."""
    icon, arg_key = _TOOL_ICONS.get(name, ("⚙️ ", None))
    detail = ""
    if arg_key and arg_key in args:
        val = args[arg_key]
        # Truncate long values (e.g. file content, long commands).
        if len(val) > 60:
            val = val[:57] + "..."
        detail = f" [bold]{val}[/bold]"
    console.print(f"  {icon} [info]{name}[/info]{detail}")

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
# Context management (enhanced — smarter pruning)
# ---------------------------------------------------------------------------

def _prune_context(messages: List[Dict[str, str]]) -> None:
    """Trim older tool-exchange pairs when the conversation grows too long.

    Strategy:
    - Never touch index 0 (system prompt) or the last 6 exchanges (12 messages).
    - For older messages, compress tool responses but keep file-read results
      longer than command outputs (they're more valuable for code generation).
    - Smart truncation for view_file results: keep first 50 + last 20 lines.
    """
    if len(messages) <= CONTEXT_MAX_MESSAGES:
        return

    # Keep system prompt + the most recent 12 messages (6 exchanges).
    keep_recent = 12
    cutoff = len(messages) - keep_recent

    for i in range(1, cutoff):
        msg = messages[i]
        content = msg.get("content", "")

        if msg["role"] == "user" and "<tool_response" in content:
            if len(content) <= 600:
                continue  # Small responses: keep as-is.

            # Check if this contains file-read results (more valuable — keep longer).
            has_file_content = "--- File:" in content and "--- End of File ---" in content

            if has_file_content and len(content) <= 3000:
                # Keep file reads up to 3KB intact.
                continue
            elif has_file_content:
                # Smart truncation: keep first 50 lines + last 20 lines of file content.
                messages[i] = {"role": "user", "content": _smart_truncate_file_response(content)}
            else:
                # Command/tool outputs: extract just the status summary.
                statuses = re.findall(
                    r'<tool_response name="([^"]+)">\s*<status>(\w+)</status>', content
                )
                if statuses:
                    summary = "; ".join(f"{n}: {s}" for n, s in statuses)
                    messages[i] = {"role": "user", "content": f"[Earlier tool results: {summary}]"}

        elif msg["role"] == "assistant" and len(content) > 1500:
            messages[i] = {
                "role": "assistant",
                "content": content[:400] + "\n... (earlier response trimmed)",
            }


def _smart_truncate_file_response(content: str) -> str:
    """Truncate file content keeping first 50 + last 20 lines."""
    lines = content.splitlines()
    if len(lines) <= 80:
        return content

    head = lines[:50]
    tail = lines[-20:]
    skipped = len(lines) - 70
    return "\n".join(head) + f"\n... ({skipped} lines omitted) ...\n" + "\n".join(tail)


# ---------------------------------------------------------------------------
# Session token tracking
# ---------------------------------------------------------------------------

class _SessionTokens:
    """Cumulative token counter for the entire session."""
    def __init__(self) -> None:
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    def reset(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0


def _fmt_tokens(n: int) -> str:
    """Format token count: 800 → '800', 1200 → '1.2K', 15000 → '15K'."""
    if n < 1000:
        return str(n)
    elif n < 10_000:
        return f"{n / 1000:.1f}K"
    else:
        return f"{n // 1000}K"


# Global session counter — persists across turns within one `ronnie` session.
session_tokens = _SessionTokens()


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
        loop_start = time.monotonic()
        interrupted = False

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
                        time.sleep(0.5)

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
                last_chunk = None
                first_token = True

                try:
                    for chunk in stream_iter:
                        last_chunk = chunk
                        content = chunk.get("message", {}).get("content", "") or ""
                        if not content:
                            continue

                        if thinking:
                            thinking = False
                            t.join(timeout=1.0)

                        # Response header on first content token.
                        if first_token:
                            first_token = False
                            live.update(Text("─── Ronnie ───", style="dim"))
                            console.print()

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
                except KeyboardInterrupt:
                    # Graceful Ctrl+C during streaming.
                    thinking = False
                    t.join(timeout=1.0)
                    interrupted = True

                if not interrupted and thinking:
                    thinking = False
                    t.join(timeout=1.0)

            # Handle interruption cleanly.
            if interrupted:
                console.print("\n[warning]  ⏹  Generation interrupted.[/warning]")
                # Don't add partial response to history — return to prompt.
                return False

            # If we broke out of the Live context because of a tool call,
            # consume the rest of the stream silently.
            if in_tool_call:
                console.print("[info]  ⚙️  Formulating tool call(s)...[/info]")
                try:
                    for chunk in stream_iter:
                        last_chunk = chunk
                        content = chunk.get("message", {}).get("content", "") or ""
                        response_text += content
                except KeyboardInterrupt:
                    console.print("\n[warning]  ⏹  Generation interrupted.[/warning]")
                    return False

        except KeyboardInterrupt:
            console.print("\n[warning]  ⏹  Generation interrupted.[/warning]")
            return False
        except Exception as e:
            console.print(f"\n[danger]Ollama error:[/danger] {e}")
            return False

        # --- Extract real token counts from Ollama's final chunk ---
        elapsed = time.monotonic() - loop_start
        turn_prompt = 0
        turn_completion = 0

        if last_chunk is not None:
            turn_prompt = getattr(last_chunk, "prompt_eval_count", 0) or 0
            turn_completion = getattr(last_chunk, "eval_count", 0) or 0
            session_tokens.add(turn_prompt, turn_completion)

        # Display: per-turn stats + cumulative session total.
        turn_total = turn_prompt + turn_completion
        if turn_total > 0:
            tps = turn_completion / elapsed if elapsed > 0 else 0
            console.print(
                f"[dim]  {_fmt_tokens(turn_prompt)} in · "
                f"{_fmt_tokens(turn_completion)} out · "
                f"{tps:.0f} tok/s · "
                f"{elapsed:.1f}s "
                f"(session: {_fmt_tokens(session_tokens.total)} tokens)[/dim]"
            )

        # --- Record assistant message ---
        messages.append({"role": "assistant", "content": response_text})

        # --- Parse tool calls ---
        calls = parse_tool_calls(response_text)
        if not calls:
            break  # Model is done — no tools invoked.

        # --- Execute tool calls ---
        tool_responses: list[str] = []
        total = len(calls)
        files_modified: list[str] = []
        files_created: list[str] = []

        for idx, call in enumerate(calls, start=1):
            step_label = f"[dim]  [Step {iteration}/{MAX_ITERATIONS}] Tool {idx}/{total}[/dim]"
            console.print(step_label)

            resp, should_cancel, always_approve = execute_tool(
                call["name"], call["args"], always_approve
            )

            if should_cancel:
                console.print("[warning]Task cancelled by user.[/warning]")
                messages.pop()  # Remove assistant msg that triggered cancellation.
                return False

            # Track file changes for summary.
            if call["name"] in ("write_file", "edit_file") and "Success" in resp:
                fpath = call["args"].get("path", "?")
                if call["name"] == "write_file" and not os.path.exists(os.path.abspath(fpath)):
                    files_created.append(fpath)
                else:
                    files_modified.append(fpath)

            tool_responses.append(resp)

        # --- File change summary ---
        change_parts: list[str] = []
        if files_created:
            change_parts.append(f"Created: {', '.join(files_created)}")
        if files_modified:
            change_parts.append(f"Modified: {', '.join(files_modified)}")
        if change_parts:
            console.print(f"[success]  📁 {' · '.join(change_parts)}[/success]")

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

    # Bell notification when the agent finishes.
    if BELL_ENABLED:
        print("\a", end="", flush=True)

    return True
