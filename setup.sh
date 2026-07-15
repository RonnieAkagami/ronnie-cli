#!/bin/bash
# Install Ronnie CLI
echo "[*] Installing Ronnie package..."
if [ -f ".venv/bin/pip" ]; then
    .venv/bin/pip install .
elif command -v pip3 &> /dev/null; then
    pip3 install .
else
    python3 -m pip install .
fi
chmod +x ronnie.py
# Write local commit hash to ~/.ronnie_commit if in git repo
if command -v git &> /dev/null && git rev-parse --is-inside-work-tree &> /dev/null; then
    git rev-parse HEAD > ~/.ronnie_commit 2>/dev/null
fi
echo "[*] Installation completed! You can now run 'ronnie' in any directory."
