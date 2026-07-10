#!/bin/bash
# Ronnie CLI - Global Installation Script

# Target binary path
TARGET="/usr/local/bin/ronnie"

# Source URL where the single-file script is hosted
# Note: You can replace this with your actual repository URL when hosted on GitHub.
SRC_URL="https://raw.githubusercontent.com/RonnieAkagami/ronnie-cli/main/ronnie.py"

echo "[*] Installing Ronnie CLI globally..."

# 1. Check if python3 is installed
if ! command -v python3 &> /dev/null; then
    echo "[-] Error: python3 is not installed. Please install Python 3 first." >&2
    exit 1
fi

# 2. Check if curl is installed
if ! command -v curl &> /dev/null; then
    echo "[-] Error: curl is not installed. Please install curl first." >&2
    exit 1
fi

# 3. Get the source script
TEMP_FILE=$(mktemp)
if [ -f "ronnie.py" ]; then
    echo "[*] Found local ronnie.py, using local file..."
    cp ronnie.py "$TEMP_FILE"
else
    echo "[*] Downloading source from $SRC_URL..."
    if ! curl -fsSL "$SRC_URL" -o "$TEMP_FILE"; then
        echo "[-] Error: Failed to download source file from $SRC_URL" >&2
        rm -f "$TEMP_FILE"
        exit 1
    fi
fi

# 4. Install dependencies (rich, ollama) globally or for the user
echo "[*] Installing pip dependencies (rich, ollama)..."
if python3 -m pip install --upgrade rich ollama --break-system-packages 2>/dev/null; then
    echo "[+] Dependencies installed globally."
elif python3 -m pip install --upgrade rich ollama --user 2>/dev/null; then
    echo "[+] Dependencies installed in user space."
else
    echo "[!] Warning: Failed to install dependencies via pip. You may need to run 'pip install rich ollama' manually."
fi

# 5. Move script to global binary directory and make executable
echo "[*] Installing executable to $TARGET (may prompt for sudo password)..."
if sudo mv "$TEMP_FILE" "$TARGET" && sudo chmod +x "$TARGET"; then
    echo "========================================="
    echo "[+] Installation Successful!"
    echo "========================================="
    echo "You can now run 'ronnie' from any directory in your terminal."
else
    echo "[-] Error: Failed to install executable to $TARGET." >&2
    rm -f "$TEMP_FILE"
    exit 1
fi
