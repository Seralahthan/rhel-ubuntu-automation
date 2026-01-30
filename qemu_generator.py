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
                # inst.debug enables debug logging
                kickstart_arg = f' inst.ks=hd:LABEL={self.iso_label}:/ks.cfg inst.text inst.sshd inst.sshpw=password inst.debug console={self.console},115200 plymouth.enable=0'
                for line in lines:
                    if line.strip().startswith('set timeout='):
                        line = 'set timeout=1'
                    if line.strip().startswith('set default='):
                        line = 'set default=0'
                    if 'menuentry' in line and 'install' in line.lower():
                        in_install = True
                    elif 'menuentry' in line and in_install and '}' in line:
                        in_install = False
                    if in_install and line.strip().startswith('linux'):
                        line = re.sub(r'hd:LABEL=[^ ]+', f'hd:LABEL={self.iso_label}', line)
                        line = line.replace('rd.live.check', '')
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
            # Add highmem=on and gic-version=3 for better compatibility with large RAM
            cmd = [
                "qemu-system-aarch64",
                "-machine", f"virt,accel={accel},highmem=on,gic-version=3",
                "-bios", "/usr/share/AAVMF/AAVMF_CODE.fd"
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
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        # Thread to read logs
        def log_streamer():
            with process.stdout:
                for line in iter(process.stdout.readline, ''):
                    print(line, end='')
        
        # We will poll the process. If it runs too long (e.g. 10 mins) without finishing
        # We can try to SSH in automatically to grab logs.
        start_time = time.time()
        ssh_checked = False
        
        while process.poll() is None:
            # Print output from stdout (using readline non-blocking usually requires select, 
            # effectively here we just let the parent process stdout flow to terminal naturally 
            # via the Popen above IF we didn't use PIPE.
            # But we used PIPE to potentially inspect.
            # Actually, standard efficient way to just let it run:
            pass 
            
            # Read line-by-line printing is better
            line = process.stdout.readline()
            if line:
                print(line, end='')
                
            # Check for hang (simple heuristic: 20 minutes passed)
            if not ssh_checked and (time.time() - start_time > 1200):
                 print("!!! DETECTED POTENTIAL HANG - ATTEMPTING DEBUG SNAPSHOT !!!")
                 self.debug_snapshot()
                 ssh_checked = True

        if process.returncode != 0:
            print(f"Installation failed with code {process.returncode}")
            sys.exit(process.returncode)

    def debug_snapshot(self):
        # Helper to ssh in and dump logs
        print("ATTEMPTING TO EXTRACT DEBUG LOGS VIA SSH...")
        try:
             # Use sshpass to handle the password for the 'root' user
             # We fetch storage.log and anaconda.log
             cmd = [
                 "sshpass", "-p", "password", 
                 "ssh", "-p", "2222", 
                 "-o", "StrictHostKeyChecking=no", 
                 "-o", "UserKnownHostsFile=/dev/null", 
                 "root@localhost", 
                 "echo '--- STORAGE LOG ---'; cat /tmp/storage.log; echo '--- ANACONDA LOG ---'; tail -n 100 /tmp/anaconda.log"
             ]
             print(" ".join(cmd))
             subprocess.check_call(cmd)
        except Exception as e:
             print(f"FAILED to extract logs: {e}")

    def start_vm(self):
        cmd = self.get_qemu_cmd(None)
        print(f"Starting VM ({self.arch}) for post-install configuration...")
        print(' '.join(cmd))
        return subprocess.Popen(cmd)
