#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Default to aarch64, but allow override env var (e.g. for x86 workflow)
ISO_NAME="${ISO_NAME:-rhel-10.0-aarch64-dvd.iso}"
ISO_PATH="./downloads/$ISO_NAME"

# Install both x86 and arm qemu packages to support both workflows
sudo apt-get install -y qemu-kvm qemu-system-x86 qemu-system-arm qemu-efi-aarch64 xorriso python3-venv curl sshpass

if [ ! -f "$ISO_PATH" ]; then
    echo "ISO not found at $ISO_PATH."
    # The python script (main.py) handles the complex SFTP/HTTP logic now.
    # We just ensure the folder exists.
    mkdir -p downloads
    echo "Delegating download to main.py automation..."
fi

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

python main.py
