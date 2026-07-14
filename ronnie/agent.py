import os
import re
import difflib
import time
import random
import threading
from typing import List, Dict, Any
import ollama

from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.prompt import Prompt
from rich.markdown import Markdown
from rich.spinner import Spinner

from ronnie.config import console, MODEL_NAME
from ronnie.tools import list_dir, view_file, write_file, edit_file, grep_search, run_command

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
        response_text = ""
        
        try:
            # Dynamic thinking synonyms
            THINKING_SYNONYMS = [
                "Pondering",
                "Analyzing",
                "Synthesizing",
                "Reflecting",
                "Deliberating",
                "Contemplating",
                "Musing",
                "Evaluating",
                "Deciphering",
                "Formulating",
                "Processing",
                "Cogitating"
            ]
            current_synonym = random.choice(THINKING_SYNONYMS)
            thinking = True
            
            initial_spinner = Spinner("dots", text=Text(f" {current_synonym}...", style="bold bright_magenta"))
            
            console.print()
            with Live(initial_spinner, refresh_per_second=10, console=console) as live:
                # Background thread to cycle synonyms every 5 seconds
                def update_spinner():
                    nonlocal current_synonym
                    start_time = time.time()
                    while thinking:
                        elapsed = time.time() - start_time
                        if elapsed >= 5.0:
                            remaining = [s for s in THINKING_SYNONYMS if s != current_synonym]
                            current_synonym = random.choice(remaining) if remaining else current_synonym
                            start_time = time.time()
                        
                        live.update(Spinner("dots", text=Text(f" {current_synonym}...", style="bold bright_magenta")))
                        time.sleep(0.1)

                t = threading.Thread(target=update_spinner, daemon=True)
                t.start()
                
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
                    stream_iterator = iter(stream)
                except Exception as e:
                    thinking = False
                    t.join(timeout=1.0)
                    raise e
                
                # Print chunks as they stream, hiding raw XML tool calls for speed & clean terminal UI
                in_tool_call = False
                
                for chunk in stream_iterator:
                    content = chunk.get('message', {}).get('content', '') or ''
                    if not content:
                        continue
                    
                    if thinking:
                        thinking = False
                        t.join(timeout=1.0)
                        
                    response_text += content
                    
                    tool_call_start = response_text.find("<tool_call")
                    if tool_call_start != -1:
                        in_tool_call = True
                        non_tool_text = response_text[:tool_call_start].strip()
                        live.update(Markdown(non_tool_text))
                        break
                    else:
                        live.update(Markdown(response_text.strip()))
                        
                if thinking:
                    thinking = False
                    t.join(timeout=1.0)
            
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
