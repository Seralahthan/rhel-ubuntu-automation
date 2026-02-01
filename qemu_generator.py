import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

class QemuGenerator:
    def __init__(self, config):
        self.config = config
        self.vm_name = config['vm']['name']
        self.arch = config['vm'].get('architecture', 'x86_64')
        self.disk_path = Path("disk.qcow2")
        
        if self.arch == 'aarch64':
            self.iso_label = "RHEL-10-AARCH64" 
            self.console = "ttyAMA0"
        else:
            self.iso_label = "RHEL-10-X86_64"
            self.console = "ttyS0"

    def find_qemu_img(self):
        qemu_img = shutil.which("qemu-img")
        if qemu_img:
            return qemu_img
        raise FileNotFoundError("qemu-img not found. Install qemu-utils / qemu-kvm.")

    def create_disk_image(self):
        qemu_img = self.find_qemu_img()
        cmd = [qemu_img, "create", "-f", "qcow2", str(self.disk_path), f"{self.config['vm']['disk_size_gb']}G"]
        print(f"Creating disk image: {' '.join(cmd)}")
        subprocess.check_call(cmd)

    def remaster_iso(self, iso_path: Path, ks_cfg_path: Path) -> Path:
        print(f"Remastering ISO {iso_path} with Kickstart...")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            iso_contents = temp_path / "iso_contents"
            iso_contents.mkdir()
            mount_point = temp_path / "mnt"
            mount_point.mkdir()

            # Mount ISO (loop)
            subprocess.check_call(["sudo", "mount", "-o", "loop", str(iso_path), str(mount_point)])
            try:
                # Use sudo to copy (bypass permission issues), then claim ownership
                subprocess.check_call(["sudo", "cp", "-a", f"{mount_point}/.", str(iso_contents)])
                subprocess.check_call(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(iso_contents)])
                subprocess.check_call(["chmod", "-R", "u+w", str(iso_contents)])
            finally:
                subprocess.check_call(["sudo", "umount", str(mount_point)])

            # Copy ks.cfg
            shutil.copy2(ks_cfg_path, iso_contents / "ks.cfg")

            # Patch grub.cfg to add kickstart and speed boot
            grub_cfg_path = iso_contents / "EFI" / "BOOT" / "grub.cfg"
            if grub_cfg_path.exists():
                content = grub_cfg_path.read_text()
                lines = content.splitlines()
                modified = []
                in_install = False
                # Remove console=tty0 and disable Plymouth
                # Use explicit LABEL for kickstart to avoid cdrom detection issues
                # inst.sshd allows debugging via ssh -p 2222 root@localhost during install
                # inst.sshpw sets a known password for the ssh session
                # inst.text enables text mode (more verbose, non-fatal warnings vs cmdline)
                kickstart_arg = f' inst.ks=hd:LABEL={self.iso_label}:/ks.cfg inst.text inst.sshd inst.sshpw=password inst.debug systemd.show_status=auto console={self.console},115200 plymouth.enable=0'
                for line in lines:
                    if line.strip().startswith('set timeout='):
                        line = 'set timeout=1'
                    if line.strip().startswith('set default='):
                        line = 'set default=0'
                    # Detect install menuentry - look for menuentry with "Install" in the title
                    if 'menuentry' in line and ('install' in line.lower() or 'Install' in line):
                        in_install = True
                    # Exit install menuentry when we hit closing brace at start of line or another menuentry
                    elif line.strip().startswith('}') and in_install:
                        in_install = False
                    elif 'menuentry' in line and in_install:
                        in_install = False
                    
                    if in_install and line.strip().startswith('linux'):
                        # Fix the stage2 label to match our new ISO label
                        line = re.sub(r'hd:LABEL=[^ ]+', f'hd:LABEL={self.iso_label}', line)
                        # Remove media check if present (remastered ISO usually fails checksum)
                        line = line.replace('rd.live.check', '')
                        # Add the kickstart parameter if not present
                        if 'inst.ks' not in line:
                            line += kickstart_arg
                    modified.append(line)
                grub_cfg_path.write_text('\n'.join(modified))
            else:
                print(f"Warning: grub.cfg not found at {grub_cfg_path}")

            remastered_iso = Path.cwd() / "install-remastered.iso"
            cmd_xorriso = [
                "xorriso", "-as", "mkisofs",
                "-r", "-J",
                "-V", self.iso_label,
                "-e", "images/efiboot.img",
                "-no-emul-boot",
                "-isohybrid-gpt-basdat",
                "-o", str(remastered_iso),
                str(iso_contents)
            ]
            print(f"Building ISO: {' '.join(cmd_xorriso)}")
            subprocess.check_call(cmd_xorriso)
            return remastered_iso

    def get_qemu_cmd(self, iso_path=None):
        has_kvm = Path("/dev/kvm").exists() and os.access("/dev/kvm", os.W_OK)
        accel = "kvm" if has_kvm else "tcg"
        cpu_type = "host" if has_kvm else "max"
        
        # Common arguments
        cmd = []
        if self.arch == 'aarch64':
            # Find AAVMF firmware - try common Ubuntu/Debian paths
            bios_paths = [
                "/usr/share/AAVMF/AAVMF_CODE.fd",
                "/usr/share/qemu-efi-aarch64/QEMU_EFI.fd",
                "/usr/share/edk2/aarch64/QEMU_EFI.fd"
            ]
            bios_path = None
            for path in bios_paths:
                if Path(path).exists():
                    bios_path = path
                    break
            
            if not bios_path:
                raise FileNotFoundError(
                    "ARM64 UEFI firmware not found. Install: sudo apt-get install qemu-efi-aarch64"
                )
            
            # Use simpler machine config without highmem to avoid potential issues
            cmd = [
                "qemu-system-aarch64",
                "-machine", f"virt,accel={accel}",
                "-bios", bios_path
            ]
        else:
             cmd = [
                "qemu-system-x86_64",
                "-machine", f"q35,accel={accel}"
            ]

        cmd.extend([
            "-m", str(self.config['vm']['memory_mb']),
            "-smp", str(self.config['vm']['cpu_cores']),
            "-cpu", cpu_type,
            "-drive", f"file={self.disk_path},if=virtio,format=qcow2",
            "-netdev", f"user,id=net0,hostfwd=tcp::{self.config['ssh']['port']}-:22",
            "-device", "virtio-net-pci,netdev=net0",
            "-device", "virtio-rng-pci",
            "-nographic"
        ])

        if iso_path:
            cmd.extend(["-cdrom", str(iso_path)])
            cmd.extend(["-boot", "d"])
        
        return cmd

    def run_install(self, remastered_iso: Path):
        cmd = self.get_qemu_cmd(remastered_iso)
        print(f"Starting unattended install ({self.arch})...")
        print(' '.join(cmd))
        
        # Start QEMU and stream output
        import sys
        import time
        import select
        
        # Use stdout=PIPE to capture logs, stderr to STDOUT to merge them
        try:
             # Run in binary mode to allow os.read to handle TUI output cleanly without blocking on newlines
             process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except Exception as e:
             print(f"Failed to start QEMU: {e}")
             sys.exit(1)

        start_time = time.time()
        ssh_checked = False
        last_status_time = start_time
        
        # Non-blocking read loop
        while process.poll() is None:
            # Check if data is available to read from stdout
            reads = [process.stdout.fileno()]
            ret = select.select(reads, [], [], 1.0) # 1 second timeout for select

            if process.stdout.fileno() in ret[0]:
                # Read raw bytes to avoid blocking on TUI partial lines
                output = os.read(process.stdout.fileno(), 1024)
                if output:
                    print(output.decode('utf-8', errors='replace'), end='', flush=True)
            
            # Print status every 5 minutes
            elapsed = time.time() - start_time
            if elapsed - (last_status_time - start_time) >= 300:
                 print(f"\n[STATUS] Installation running for {elapsed/60:.1f} minutes...\n", flush=True)
                 last_status_time = time.time()
            
            # Check for hang (simple heuristic: 30 minutes passed)
            # Increased from 20 to 30 minutes to account for slow ARM64 emulation
            if not ssh_checked and elapsed > 1800:
                 print(f"\n!!! DETECTED POTENTIAL HANG ({elapsed:.0f}s / 30min) - ATTEMPTING DEBUG SNAPSHOT !!!")
                 self.debug_snapshot()
                 ssh_checked = True
                 # Continue running - don't kill the process yet

        if process.returncode != 0:
            print(f"Installation failed with code {process.returncode}")
            sys.exit(process.returncode)

    def debug_snapshot(self):
        # Helper to ssh in and dump logs
        print("ATTEMPTING TO EXTRACT DEBUG LOGS VIA SSH...")
        try:
             # Use sshpass to handle the password for the 'root' user
             # We fetch storage.log, anaconda.log, program.log and check processes
             cmd = [
                 "sshpass", "-p", "password", 
                 "ssh", "-p", "2222", 
                 "-o", "StrictHostKeyChecking=no", 
                 "-o", "UserKnownHostsFile=/dev/null",
                 "-o", "ConnectTimeout=10",
                 "root@localhost", 
                 "echo '--- ANACONDA PROCESSES ---'; ps aux | grep anaconda || true; "
                 "echo '--- STORAGE LOG (last 50 lines) ---'; tail -n 50 /tmp/storage.log 2>/dev/null || echo 'storage.log not found'; "
                 "echo '--- ANACONDA LOG (last 100 lines) ---'; tail -n 100 /tmp/anaconda.log 2>/dev/null || echo 'anaconda.log not found'; "
                 "echo '--- PROGRAM LOG (last 50 lines) ---'; tail -n 50 /tmp/program.log 2>/dev/null || echo 'program.log not found'; "
                 "echo '--- PARTITIONS ---'; lsblk 2>/dev/null || true; "
                 "echo '--- ENTROPY ---'; cat /proc/sys/kernel/random/entropy_avail 2>/dev/null || true; "
                 "echo '--- MEMORY ---'; free -h 2>/dev/null || true"
             ]
             print(" ".join(cmd))
             subprocess.check_call(cmd)
        except Exception as e:
             print(f"FAILED to extract logs (this is expected if SSH isn't ready yet): {e}")

    def start_vm(self):
        cmd = self.get_qemu_cmd(None)
        print(f"Starting VM ({self.arch}) for post-install configuration...")
        print(' '.join(cmd))
        return subprocess.Popen(cmd)
