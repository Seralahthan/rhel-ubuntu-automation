# RHEL on QEMU (GitHub Actions Friendly)

This variant runs entirely on GitHub-hosted Ubuntu runners using QEMU/KVM (no macOS host needed).

## What it does
- Downloads the RHEL 10 x86_64 ISO (URL provided via `RHEL_ISO_URL` secret)
- Generates a Kickstart file
- Remasters the ISO to auto-run the Kickstart
- Boots QEMU/KVM to perform an unattended install (powers off when done)
- Boots the installed VM and runs post-install steps (subscription, updates, podman, nginx, SELinux demo)

## Requirements
- GitHub Actions runner: `runs-on: ubuntu-latest` (supports KVM)
- Secrets: `RHEL_ISO_URL` pointing to the RHEL 10 x86_64 DVD ISO

## Usage (locally on Ubuntu)
```bash
sudo apt-get update && sudo apt-get install -y qemu-kvm qemu-system-x86 xorriso python3-venv
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export RHEL_ISO_URL="https://your-url/rhel-10.0-x86_64-dvd.iso"
python main.py
```

## GitHub Actions
See `.github/workflows/ubuntu-qemu.yml` for a ready-to-run workflow.
