import os
import re
import subprocess

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
