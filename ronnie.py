#!/usr/bin/env python3
import os
import sys
import re
import json
import readline
import subprocess
import difflib
import atexit
from typing import List, Dict, Any, Generator

try:
    import ollama
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.live import Live
    from rich.syntax import Syntax
    from rich.prompt import Prompt
    from rich.theme import Theme
    from rich.markdown import Markdown
except ImportError as e:
    print(f"Missing required dependency: {e}")
    print("Please run: pip install rich ollama")
    sys.exit(1)

# Configuration
MODEL_NAME = "ornith:9b"
SYSTEM_PROMPT = """You are Ronnie, a state-of-the-art autonomous software engineering agent.
You operate on the user's local workspace. You can read, write, edit files, run commands, list directory contents, and search code.

You must interact with the environment strictly by emitting one or more tool calls in your responses using the following XML format:
<tool_call name="tool_name">
  <parameter_name>parameter_value</parameter_name>
</tool_call>

Here is the toolset at your disposal:

1. list_dir: List files and directories in the workspace.
<tool_call name="list_dir">
  <path>relative_path_to_directory</path> <!-- optional, defaults to current directory '.' -->
</tool_call>

2. view_file: View the content of a file (supports line ranges).
<tool_call name="view_file">
  <path>relative_path_to_file</path>
  <start_line>1</start_line> <!-- optional, defaults to 1 -->
  <end_line>100</end_line> <!-- optional, defaults to end of file -->
</tool_call>

3. write_file: Create a new file or completely overwrite an existing file.
<tool_call name="write_file">
  <path>relative_path_to_file</path>
  <content>entire file contents here</content>
</tool_call>

4. edit_file: Edit a specific block of text in an existing file using find-and-replace.
<tool_call name="edit_file">
  <path>relative_path_to_file</path>
  <search>the exact text block to search for (must match exactly, including leading whitespace)</search>
  <replace>the replacement text block</replace>
</tool_call>

5. grep_search: Search recursively for a pattern in all files under a path.
<tool_call name="grep_search">
  <pattern>search_pattern</pattern>
  <path>relative_path_to_search</path> <!-- optional, defaults to current directory '.' -->
</tool_call>

6. run_command: Run a shell command in the local environment.
<tool_call name="run_command">
  <cmd>terminal_command</cmd>
</tool_call>

Rules of Engagement:

WORKFLOW:
- You are autonomous: first, inspect files and list directories to build understanding.
- Never write code from memory if you can check the existing file first.
- Always use specific, small `edit_file` calls rather than full `write_file` rewrites for large files.
- You can make multiple tool calls in a single turn if they are independent.
- After a tool call is outputted, STOP generating and wait for the tool response from the environment.
- **MANDATORY VERIFICATION:** After writing or modifying any script, you MUST execute a test run using the `run_command` tool to verify it works.
- **ACTIVE DEBUGGING & LOGGING:** Do not assume a task is successful simply because the code executes without syntax errors. Check the actual output (e.g., if a scraper returns 0 results or an empty list, it has failed!). Print exact errors, add logging statements, and inspect web page content if elements aren't matching.
- **VIRTUAL ENVIRONMENT AWARENESS:** Check if a local python virtual environment (like `.venv`) is present in the workspace. If found, run your Python scripts using the venv's Python path (e.g., `.venv/bin/python script.py`) to ensure all installed dependencies are imported correctly.
- **DEFENSIVE API CALLS:** Double-check library API method signatures (e.g., Selenium's `get_attribute` or BeautifulSoup). Never write generic `try-except` blocks that silently swallow exceptions without printing the error/traceback.

CODE QUALITY (CRITICAL — follow these strictly):
- **DRY (Don't Repeat Yourself):** NEVER duplicate logic across functions. If multiple code paths share the same pattern (e.g., "try decreasing quality in a loop, then try resizing"), write ONE generic function parameterized by format/options, not separate near-identical functions per variant. Duplicated functions are a critical failure.
- **Minimal Lines of Code:** Write the shortest correct solution. Remove all boilerplate, unnecessary comments, redundant variables, and wrapper functions that add no value. A 50-line solution is always preferred over a 200-line one if both are correct.
- **Algorithmic Efficiency:** Use efficient algorithms. For search/optimization problems (e.g., finding a quality level that fits a size), prefer binary search over linear scan. Avoid unnecessary repeated I/O, redundant conversions, or re-opening files already in memory.
- **Pythonic Code:** Use comprehensions, `pathlib`, f-strings, built-in functions, and standard library utilities. Avoid manual loops when a one-liner works. Use `argparse` only when genuinely needed; `sys.argv` is fine for simple scripts.
- **No Dead Code:** Never generate unused variables, unreachable branches, or commented-out code.
- **SELF-REVIEW BEFORE FINALIZING:** Before declaring a task complete, mentally review your generated code and ask: "Can this be shorter? Is there duplicated logic? Am I using an O(n) loop where O(log n) binary search works?" If yes, refactor BEFORE writing the file.

- When your task is verified and complete, summarize your changes and state that you are done.
"""

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
try:
    readline.read_history_file(history_file)
except Exception:
    pass

try:
    readline.set_history_length(1000)
except Exception:
    pass

def save_history():
    try:
        readline.write_history_file(history_file)
    except Exception:
        pass

atexit.register(save_history)

# -----------------
# TOOL IMPLEMENTATIONS
# -----------------

def list_dir(path: str = ".") -> str:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: Directory '{path}' does not exist."
    if not os.path.isdir(abs_path):
        return f"Error: '{path}' is not a directory."
    
    lines = []
    for root, dirs, files in os.walk(abs_path):
        # Prune ignored directories
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '__pycache__', '.venv', '.agents', 'scratch')]
        for file in files:
            if file in ('.DS_Store',):
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, abs_path)
            size = os.path.getsize(full_path)
            lines.append(f"{rel_path} ({size} bytes)")
            
    return "\n".join(lines) if lines else "Directory is empty."

def view_file(path: str, start_line: int = 1, end_line: int = None) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: File '{path}' does not exist."
    
    try:
        with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        start = max(1, int(start_line))
        end = min(total_lines, int(end_line)) if end_line else total_lines
        
        if start > total_lines:
            return f"Error: start_line ({start}) exceeds total lines in file ({total_lines})."
            
        selected = lines[start-1:end]
        formatted = [f"{idx:4d}: {line}" for idx, line in enumerate(selected, start=start)]
        return f"--- File: {path} (Lines {start}-{end} of {total_lines}) ---\n" + "".join(formatted) + "\n--- End of File ---"
    except Exception as e:
        return f"Error reading file: {str(e)}"

def write_file(path: str, content: str) -> str:
    abs_path = os.path.abspath(path)
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Success: Wrote file '{path}' successfully."
    except Exception as e:
        return f"Error writing file: {str(e)}"

def edit_file(path: str, search: str, replace: str) -> str:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: File '{path}' does not exist."
        
    try:
        with open(abs_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        if search not in content:
            return f"Error: The search block was not found exactly in '{path}'. Please ensure whitespace and formatting match exactly."
            
        count = content.count(search)
        if count > 1:
            return f"Error: The search block matches {count} times in '{path}'. Make it more unique."
            
        new_content = content.replace(search, replace, 1)
        with open(abs_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return f"Success: Modified '{path}' successfully."
    except Exception as e:
        return f"Error editing file: {str(e)}"

def grep_search(pattern: str, path: str = ".") -> str:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return f"Error: Path '{path}' does not exist."
        
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error: Invalid regex pattern: {str(e)}"
        
    results = []
    for root, dirs, files in os.walk(abs_path):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '__pycache__', '.venv', '.agents', 'scratch')]
        for file in files:
            if file in ('.DS_Store',):
                continue
            full_path = os.path.join(root, file)
            try:
                # Basic text check by reading a chunk
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for idx, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel_path = os.path.relpath(full_path, abs_path)
                            results.append(f"{rel_path}:{idx}: {line.strip()}")
            except Exception:
                pass
                
    return "\n".join(results) if results else "No matches found."

def run_command(cmd: str) -> str:
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300
        )
        return f"Exit code: {res.returncode}\nOutput:\n{res.stdout}"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 300 seconds."
    except Exception as e:
        return f"Error running command: {str(e)}"

# -----------------
# PARSING & UTILS
# -----------------

def parse_tool_calls(text: str) -> List[Dict[str, Any]]:
    # Capture everything within <tool_call name="...">...</tool_call>
    tool_call_pattern = re.compile(r"<tool_call\s+name=\"([^\"]+)\"\s*>(.*?)</tool_call>", re.DOTALL)
    calls = []
    
    for match in tool_call_pattern.finditer(text):
        tool_name = match.group(1)
        body = match.group(2)
        
        args = {}
        # For content/search/replace tags, use GREEDY match to handle code with
        # angle brackets (e.g. `x < 5`, `</div>`, etc.) inside the tag body.
        # For other short params (path, cmd, pattern), use non-greedy.
        for tag_name in ('content', 'search', 'replace'):
            tag_pattern = re.compile(rf"<{tag_name}>(.*)</{tag_name}>", re.DOTALL)
            tag_match = tag_pattern.search(body)
            if tag_match:
                args[tag_name] = tag_match.group(1)
        
        # Parse remaining simple parameter tags with non-greedy match
        simple_pattern = re.compile(r"<([a-zA-Z0-9_]+)>(.*?)</\1>", re.DOTALL)
        for p_match in simple_pattern.finditer(body):
            param_name = p_match.group(1)
            if param_name in args:  # Already parsed with greedy match
                continue
            args[param_name] = p_match.group(2).strip()
            
        calls.append({
            "name": tool_name,
            "args": args,
            "raw": match.group(0)
        })
    return calls

def get_diff(old_text: str, new_text: str, filename: str) -> Text:
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3
    )
    diff_text = Text()
    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            diff_text.append(line, style="green")
        elif line.startswith('-') and not line.startswith('---'):
            diff_text.append(line, style="red")
        elif line.startswith('@@'):
            diff_text.append(line, style="cyan")
        else:
            diff_text.append(line, style="dim")
    return diff_text

# -----------------
# AGENT LOOP
# -----------------

def execute_tool(name: str, args: Dict[str, Any], always_approve: bool) -> tuple[str, bool]:
    """
    Executes a tool call. Returns a tuple (response_text, should_cancel).
    """
    is_modifying = name in ('write_file', 'edit_file', 'run_command')
    
    # Validate required arguments
    if name in ('write_file', 'edit_file', 'view_file') and not args.get('path'):
        return f'<tool_response name="{name}"><status>error</status><message>Error: Missing required "path" argument. The XML parser could not extract it — the model may have generated malformed XML.</message></tool_response>', False
    if name == 'run_command' and not args.get('cmd'):
        return f'<tool_response name="{name}"><status>error</status><message>Error: Missing required "cmd" argument.</message></tool_response>', False
    
    # Prompt for confirmation if necessary
    if is_modifying and not always_approve:
        console.print()
        
        # Visual cues depending on tool
        if name == "run_command":
            console.print(Panel(f"[yellow]Command:[/yellow] {args.get('cmd')}", title="Proposed command execution", border_style="yellow"))
        elif name == "write_file":
            path = args.get('path')
            content = args.get('content', '')
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, 'r', encoding='utf-8') as f:
                        old_content = f.read()
                    diff_text = get_diff(old_content, content, path)
                    console.print(Panel(diff_text, title=f"Overwriting {path} (Diff)", border_style="yellow"))
                except Exception:
                    console.print(Panel(f"Overwriting {path}", title="Proposed file write", border_style="yellow"))
            else:
                diff_text = get_diff("", content, path)
                console.print(Panel(diff_text, title=f"Creating new file {path}", border_style="green"))
        elif name == "edit_file":
            path = args.get('path')
            search = args.get('search')
            replace = args.get('replace')
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path):
                try:
                    with open(abs_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if search in content:
                        new_content = content.replace(search, replace, 1)
                        diff_text = get_diff(content, new_content, path)
                        console.print(Panel(diff_text, title=f"Editing {path} (Diff)", border_style="yellow"))
                    else:
                        console.print(Panel(f"[red]Search block not found exactly in file.[/red]", title="Diff error", border_style="red"))
                except Exception as e:
                    console.print(Panel(f"Error preparing diff: {e}", title="Diff error", border_style="red"))
        
        console.print(f"[warning]Tool Call:[/warning] [bold cyan]{name}[/bold cyan] requires permission.")
        choice = Prompt.ask("Confirm execution? ([bold green]y[/bold green]es / [bold red]n[/bold red]o / [bold yellow]a[/bold yellow]lways / [bold magenta]c[/bold magenta]ancel)", choices=["y", "n", "a", "c"], default="y")
        
        if choice == "n":
            return f"<tool_response name=\"{name}\"><status>error</status><message>Execution declined by user.</message></tool_response>", False
        elif choice == "c":
            return "", True
        elif choice == "a":
            # Set parent loop variable to always approve from now on
            always_approve = True
            
    # Run the tool
    console.print(f"⚙️  [info]Executing {name}...[/info]")
    
    try:
        if name == "list_dir":
            result = list_dir(args.get("path", "."))
        elif name == "view_file":
            result = view_file(
                args.get("path"), 
                start_line=args.get("start_line", 1), 
                end_line=args.get("end_line")
            )
        elif name == "write_file":
            result = write_file(args.get("path"), args.get("content", ""))
        elif name == "edit_file":
            result = edit_file(args.get("path"), args.get("search", ""), args.get("replace", ""))
        elif name == "grep_search":
            result = grep_search(args.get("pattern"), args.get("path", "."))
        elif name == "run_command":
            result = run_command(args.get("cmd"))
        else:
            result = f"Error: Unknown tool name '{name}'"
            
        status = "success" if not result.startswith("Error") else "error"
    except Exception as e:
        result = f"Unexpected execution error: {str(e)}"
        status = "error"
        
    response = f"<tool_response name=\"{name}\"><status>{status}</status><message>{result}</message></tool_response>"
    return response, False

def run_agentic_loop(messages: List[Dict[str, str]], client: ollama.Client) -> bool:
    """
    Runs the agent loop until the agent completes its task or is cancelled.
    Returns True if completed, False if cancelled.
    """
    always_approve = False
    
    while True:
        console.print("\n🤖 [bold bright_magenta]Ronnie Thinking...[/bold bright_magenta]")
        response_text = ""
        
        try:
            # Stream the completion from Ollama
            # Disable thinking mode for speed — ornith:9b supports it but it causes
            # very long prefill delays for complex prompts
            stream = client.chat(
                model=MODEL_NAME,
                messages=messages,
                stream=True,
                think=False
            )
            
            # Print chunks as they stream, hiding raw XML tool calls for speed & clean terminal UI
            in_tool_call = False
            stream_iterator = iter(stream)
            
            with Live("", refresh_per_second=10, console=console) as live:
                for chunk in stream_iterator:
                    content = chunk.get('message', {}).get('content', '') or ''
                    if not content:
                        continue
                    response_text += content
                    
                    tool_call_start = response_text.find("<tool_call")
                    if tool_call_start != -1:
                        in_tool_call = True
                        non_tool_text = response_text[:tool_call_start].strip()
                        live.update(Markdown(non_tool_text))
                        break
                    else:
                        live.update(Markdown(response_text.strip()))
            
            if in_tool_call:
                console.print("[info]⚙️  Formulating tool call(s)...[/info]", end="")
                for chunk in stream_iterator:
                    content = chunk.get('message', {}).get('content', '') or ''
                    response_text += content
                    if len(response_text) % 80 == 0:
                        print(".", end="", flush=True)
                print()
            
        except Exception as e:
            console.print(f"\n[danger]Ollama error:[/danger] {e}")
            return False
            
        # Add assistant response to history
        messages.append({"role": "assistant", "content": response_text})
        
        # Parse for tool calls
        calls = parse_tool_calls(response_text)
        if not calls:
            # If no tools called, we assume the assistant is done or waiting for input
            break
            
        # Execute tool calls
        tool_responses = []
        for call in calls:
            name = call["name"]
            args = call["args"]
            
            resp, should_cancel = execute_tool(name, args, always_approve)
            if should_cancel:
                console.print("[warning]Task cancelled by user.[/warning]")
                # Remove assistant's message that provoked the cancellation so history stays clean
                messages.pop()
                return False
                
            # If user selected "always", propagate it
            if "always" in resp or (name in ('write_file', 'edit_file', 'run_command') and always_approve == False and resp and "Execution declined" not in resp):
                pass 
                
            tool_responses.append(resp)
            
        # Feed all responses back in a single user message
        combined_response = "\n".join(tool_responses)
        messages.append({"role": "user", "content": combined_response})
        
    return True

# -----------------
# MAIN INTERACTIVE SHELL
# -----------------

def main():
    # Setup client
    client = ollama.Client()
    try:
        client.list()
    except Exception as e:
        console.print(Panel(f"[red]Error connecting to Ollama: {e}[/red]\n\nPlease verify that Ollama is running and that the model [bold]{MODEL_NAME}[/bold] is loaded.", title="Connection Error"))
        sys.exit(1)
        
    # Print welcome banner
    console.print(Panel("""[banner]██████╗  ██████╗ ███╗   ██╗███╗   ██╗██╗███████╗
██╔══██╗██╔═══██╗████╗  ██║████╗  ██║██║██╔════╝
██████╔╝██║   ██║██╔██╗ ██║██╔██╗ ██║██║█████╗  
██╔══██╗██║   ██║██║╚██╗██║██║╚██╗██║██║██╔══╝  
██║  ██║╚██████╔╝██║ ╚████║██║ ╚████║██║███████╗
╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═══╝╚═╝╚══════╝[/banner]

[bold cyan]Ronnie CLI - Local Agentic Coding Partner[/bold cyan]
Powered by [bold magenta]ornith:9b[/bold magenta]

Commands:
  [bold yellow]/clear[/bold yellow]   Clear conversation history
  [bold yellow]/exit[/bold yellow]    Exit Ronnie
""", border_style="bright_magenta"))

    # Check for direct prompt execution
    direct_prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    
    # Session state
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    
    if direct_prompt:
        console.print(f"[bold]Direct Input:[/bold] {direct_prompt}")
        messages.append({"role": "user", "content": direct_prompt})
        run_agentic_loop(messages, client)
        sys.exit(0)
        
    # Shell loop
    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]ronnie[/bold cyan]")
            user_input = user_input.strip()
            
            if not user_input:
                continue
                
            if user_input.lower() in ('/exit', '/quit', 'exit', 'quit'):
                console.print("[info]Goodbye![/info]")
                break
                
            if user_input.lower() == '/clear':
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                console.print("[success]Conversation history cleared.[/success]")
                continue
                
            # Append prompt and run agentic loop
            messages.append({"role": "user", "content": user_input})
            run_agentic_loop(messages, client)
            
        except KeyboardInterrupt:
            console.print("\n[warning]KeyboardInterrupt. Type /exit to exit.[/warning]")
        except EOFError:
            console.print("\n[info]Goodbye![/info]")
            break

if __name__ == "__main__":
    main()
