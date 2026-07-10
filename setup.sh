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
echo "[*] Installation completed! You can now run 'ronnie' in any directory."
