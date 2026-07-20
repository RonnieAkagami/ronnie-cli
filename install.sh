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

# 4. Install the package and dependencies (rich, ollama) globally or for the user
echo "[*] Installing Ronnie package and dependencies..."
if [ -f "ronnie.py" ]; then
    # Local install
    if python3 -m pip install --upgrade . --break-system-packages 2>/dev/null; then
        echo "[+] Package and dependencies installed globally."
    elif python3 -m pip install --upgrade . --user 2>/dev/null; then
        echo "[+] Package and dependencies installed in user space."
    else
        echo "[!] Warning: Failed to install package via pip. You may need to run 'pip install .' manually."
    fi
else
    # Remote install from Git archive (does not require git binary)
    SRC_ZIP="https://github.com/RonnieAkagami/ronnie-cli/archive/refs/heads/main.zip"
    if python3 -m pip install --upgrade --no-cache-dir "$SRC_ZIP" --break-system-packages 2>/dev/null; then
        echo "[+] Package and dependencies installed globally from GitHub."
    elif python3 -m pip install --upgrade --no-cache-dir "$SRC_ZIP" --user 2>/dev/null; then
        echo "[+] Package and dependencies installed in user space from GitHub."
    else
        echo "[!] Warning: Failed to install package via pip. You may need to run 'pip install' manually." #fail check
    fi
fi

# 5. Move script to global binary directory and make executable
echo "[*] Installing executable to $TARGET (may prompt for sudo password)..."
if sudo mv "$TEMP_FILE" "$TARGET" && sudo chmod +x "$TARGET"; then
    # 6. Save current commit hash to avoid redundant update on first run
    echo "[*] Saving current commit hash to ~/.ronnie_commit..."
    REMOTE_SHA=$(curl -s "https://github.com/RonnieAkagami/ronnie-cli.git/info/refs?service=git-upload-pack" | grep -oE '[0-9a-fA-F]{40}' | head -n 1)
    if [ ! -z "$REMOTE_SHA" ]; then
        echo "$REMOTE_SHA" > ~/.ronnie_commit
    fi

    echo "========================================="
    echo "[+] Installation Successful!"
    echo "========================================="
    echo "You can now run 'ronnie' from any directory in your terminal."
else
    echo "[-] Error: Failed to install executable to $TARGET." >&2
    rm -f "$TEMP_FILE"
    exit 1
fi
