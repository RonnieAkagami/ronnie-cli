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
