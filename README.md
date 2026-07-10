# Ronnie CLI: Local Agentic Coding Partner

**Ronnie** is a state-of-the-art autonomous software engineering CLI tool (a replica of Claude Code) built on top of the local `ornith:9b` model running via Ollama. It operates directly in your terminal, executing commands, reading/writing files, performing regex grep searches, and conducting autonomous verification runs to ensure correctness.

---

## Features

- **Autonomous Agentic Loop:** Ronnie can plan, write, test, debug, and verify scripts autonomously.
- **Fast Stream Interception:** Instantly intercepts and hides raw XML tool call output while providing subtle progress cues, avoiding terminal clutter and maximizing performance.
- **Advanced Code Quality Engine:** Enforces DRY (Don't Repeat Yourself) code design, minimal lines of code, O(log n) binary search algorithms for optimization, and rigorous self-review gates.
- **Real-Time Integration:** Fully integrates with Python virtual environments (`.venv`) and standard Unix utilities.

---

## Installation

You can install Ronnie globally in your environment, within a virtual environment, or directly using a one-line `curl` installer.

### Option 1: Direct curl command (Recommended for global install)
Run this single command in your terminal. It will download the script, configure dependencies, and install `ronnie` to `/usr/local/bin`:
```bash
curl -fsSL https://raw.githubusercontent.com/RonnieAkagami/ronnie-cli/main/install.sh | bash
```

### Option 2: Local Installation Script (For Virtual Environments)
Clone this repository to your system, navigate to the directory, and run:
```bash
chmod +x setup.sh
./setup.sh
```

### Option 3: Manual pip installation
Run the following command in the directory containing `pyproject.toml`:
```bash
pip install .
```

Once installed, the `ronnie` command will be registered globally and accessible from **any working directory** in your terminal.

---

## Usage

Start the interactive coding shell:
```bash
ronnie
```

Or pass a direct instruction to run autonomously from end-to-end:
```bash
ronnie "Create a clean script to scrape the latest weather data from example.com"
```

### CLI Commands
- `/clear` — Clears the current conversation history.
- `/exit` or `/quit` — Exists the interactive CLI.
