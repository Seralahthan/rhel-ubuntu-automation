import os
import sys
import time
import subprocess
from pathlib import Path
import yaml

from kickstart_generator import KickstartGenerator
from qemu_generator import QemuGenerator
from ssh_configurator import SSHConfigurator


def load_config(path="config.yaml"):
    cfg_path = Path(path)
    if not cfg_path.exists():
        print(f"Error: {path} not found.")
        sys.exit(1)
    with open(cfg_path, 'r') as f:
        config = yaml.safe_load(f)

    # Secure overrides from Environment Variables
    if os.environ.get("RHEL_SSH_USER"):
        config['ssh']['user'] = os.environ.get("RHEL_SSH_USER")
    if os.environ.get("RHEL_SSH_PASS"):
        config['ssh']['password'] = os.environ.get("RHEL_SSH_PASS")
    
    if os.environ.get("RHEL_SUB_USER"):
        if 'subscription' not in config: config['subscription'] = {}
        config['subscription']['username'] = os.environ.get("RHEL_SUB_USER")
    if os.environ.get("RHEL_SUB_PASS"):
        if 'subscription' not in config: config['subscription'] = {}
        config['subscription']['password'] = os.environ.get("RHEL_SUB_PASS")
        
    return config


def ensure_iso(config):
    iso_path = Path(config['os']['iso_path'])
    if iso_path.exists():
        print(f"ISO found at {iso_path}")
        return iso_path
    
    iso_path.parent.mkdir(parents=True, exist_ok=True)
    download_cfg = config['os'].get('download', {})
    
    if download_cfg.get('method') == 'sftp':
        print("Using SFTP for download...")
        import paramiko
        host = os.environ.get("RHEL_SFTP_HOST") or download_cfg['sftp'].get('host')
        if not host:
             print("Error: SFTP host must be set via RHEL_SFTP_HOST or config.yaml")
             sys.exit(1)

        port = int(download_cfg['sftp'].get('port', 22))
        remote_path = download_cfg['sftp']['remote_path']
        
        user = os.environ.get("RHEL_SFTP_USER")
        password = os.environ.get("RHEL_SFTP_PASS")
        
        if not user or not password:
             print("Error: RHEL_SFTP_USER and RHEL_SFTP_PASS must be set for SFTP download.")
             sys.exit(1)
             
        print(f"Connecting to SFTP {user}@{host}:{port}...")
        transport = paramiko.Transport((host, port))
        try:
            transport.connect(username=user, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            
            # Handle compressed download
            local_download_path = str(iso_path)
            is_gzipped = remote_path.endswith('.gz')
            if is_gzipped:
                local_download_path += ".gz"

            print(f"Downloading {remote_path} to {local_download_path} ...")
            
            # Progress callback: Print every ~500MB
            last_printed_chunk = [0]
            def progress(transferred, total):
                chunk_size = 500 * 1024 * 1024
                current_chunk = transferred // chunk_size
                if current_chunk > last_printed_chunk[0] or transferred == total:
                    last_printed_chunk[0] = current_chunk
                    print(f"--> Downloaded: {transferred // (1024*1024)}MB / {total // (1024*1024)}MB ({transferred/total*100:.1f}%)")

            sftp.get(remote_path, local_download_path, callback=progress)
            print("\nDownload complete.")
            sftp.close()

            if is_gzipped:
                print(f"Decompressing {local_download_path} to {iso_path}...")
                import gzip
                import shutil
                try:
                    with gzip.open(local_download_path, 'rb') as f_in:
                        with open(iso_path, 'wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    print("Decompression complete.")
                except Exception as e:
                    print(f"Failed to decompress: {e}")
                    if iso_path.exists():
                        iso_path.unlink()
                    sys.exit(1)
                finally:
                    if os.path.exists(local_download_path):
                        os.remove(local_download_path)
        finally:
            transport.close()
            
    else:
        # Fallback to HTTP URL env var
        url = os.environ.get("RHEL_ISO_URL") or download_cfg.get('url')
        if not url:
            print(f"ISO missing at {iso_path} and neither RHEL_ISO_URL nor SFTP config provided.")
            sys.exit(1)

        print(f"Downloading ISO from {url} ...")
        subprocess.check_call(["curl", "-L", "-o", str(iso_path), url])
        print("Download complete.")
        
    return iso_path


def main():
    print("--- RHEL on QEMU (GitHub Actions) ---")
    config = load_config()

    iso_path = ensure_iso(config)

    print("[1] Generate Kickstart")
    ks = KickstartGenerator(config)
    ks.generate_ks_cfg()

    qemu = QemuGenerator(config)

    print("[2] Create disk image")
    qemu.create_disk_image()

    print("[3] Remaster ISO with ks.cfg")
    remastered_iso = qemu.remaster_iso(iso_path, Path("ks.cfg"))

    print("[4] Unattended installation (wait for poweroff)...")
    qemu.run_install(remastered_iso)

    print("[5] Boot installed VM for post-install config")
    proc = qemu.start_vm()
    try:
        print("Waiting for SSH to become available...")
        time.sleep(20)
        configurator = SSHConfigurator(config)
        configurator.configure_system()
    finally:
        print("Stopping VM...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("All done. Artifacts: disk.qcow2, install-remastered.iso")


if __name__ == "__main__":
    main()
