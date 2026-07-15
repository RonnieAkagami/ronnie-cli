import os
import sys
import readline
import atexit
import ollama
from rich.panel import Panel
from rich.prompt import Prompt

from ronnie.config import console, MODEL_NAME, history_file
from ronnie.prompts import SYSTEM_PROMPT
from ronnie.agent import run_agentic_loop

# Enable CLI history
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

def check_and_update():
    # Avoid auto-updating when running in local development mode or when explicitly disabled
    is_dev = (
        os.environ.get("RONNIE_NO_AUTO_UPDATE") == "1"
        or os.environ.get("RONNIE_DEV") == "1"
        or os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "setup.py"))
    )
    if is_dev:
        return

    commit_file = os.path.expanduser("~/.ronnie_commit")
    
    # 1. Fetch remote commit SHA
    remote_sha = None
    url = "https://github.com/RonnieAkagami/ronnie-cli.git/info/refs?service=git-upload-pack"
    try:
        import urllib.request
        import re
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        with urllib.request.urlopen(req, timeout=1.2) as response:
            content = response.read().decode('utf-8', errors='ignore')
            match = re.search(r'([0-9a-fA-F]{40})\s+refs/heads/main', content)
            if match:
                remote_sha = match.group(1)
    except Exception:
        return

    if not remote_sha:
        return

    # 2. Check local commit SHA
    local_sha = None
    if os.path.exists(commit_file):
        try:
            with open(commit_file, "r") as f:
                local_sha = f.read().strip()
        except Exception:
            pass

    # 3. If remote commit is different, update
    if remote_sha != local_sha:
        console.print(f"[info][*] New update found! Updating Ronnie CLI to latest version ({remote_sha[:7]})...[/info]")
        
        import subprocess
        zip_url = "https://github.com/RonnieAkagami/ronnie-cli/archive/refs/heads/main.zip"
        
        success = False
        options_list = [
            ["install", "--upgrade", zip_url],
            ["install", "--upgrade", zip_url, "--break-system-packages"],
            ["install", "--upgrade", zip_url, "--user"],
            ["install", "--upgrade", zip_url, "--user", "--break-system-packages"]
        ]
        
        for opts in options_list:
            try:
                res = subprocess.run([sys.executable, "-m", "pip"] + opts, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
                
            # Try to update the executable itself if it's writable
            exe_path = sys.argv[0]
            if os.path.exists(exe_path) and os.access(exe_path, os.W_OK):
                try:
                    raw_url = "https://raw.githubusercontent.com/RonnieAkagami/ronnie-cli/main/ronnie.py"
                    with urllib.request.urlopen(raw_url, timeout=1.2) as resp:
                        new_code = resp.read()
                        if new_code:
                            with open(exe_path, 'wb') as f:
                                f.write(new_code)
                except Exception:
                    pass
                    
            console.print(f"[success][+] Ronnie CLI updated successfully. Restarting...[/success]\n")
            
            # Restart the process
            try:
                os.execvp(sys.argv[0], sys.argv)
            except Exception:
                try:
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                except Exception:
                    sys.exit(0)
        else:
            console.print("[warning][!] Failed to update Ronnie CLI automatically. Skipping update...[/warning]")

def main():
    check_and_update()
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
