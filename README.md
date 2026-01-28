# RHEL on QEMU (GitHub Actions Friendly)

This variant runs entirely on GitHub-hosted Ubuntu ARM64 runners using QEMU/KVM.

## What it does
- Downloads the RHEL 10 aarch64 ISO (via HTTP or SFTP)
- Generates a Kickstart file
- Remasters the ISO to auto-run the Kickstart
- Boots QEMU/KVM to perform an unattended install (powers off when done)
- Boots the installed VM and runs post-install steps (subscription, updates, podman, nginx, SELinux demo)

## Requirements
- GitHub Actions runner: `runs-on: ubuntu-24.04-arm` (supports KVM)
- Secrets: `RHEL_ISO_URL` (or SFTP creds) pointing to the RHEL 10 aarch64 DVD ISO

## Usage (locally on Ubuntu)
```bash
sudo apt-get update && sudo apt-get install -y qemu-kvm qemu-system-arm xorriso python3-venv
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export RHEL_ISO_URL="https://your-url/rhel-10.0-aarch64-dvd.iso"
python main.py
```

## GitHub Actions
See `.github/workflows/ubuntu-qemu.yml` for a ready-to-run workflow.
