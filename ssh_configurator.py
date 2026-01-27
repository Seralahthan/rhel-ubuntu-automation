import paramiko
import time
import sys

class SSHConfigurator:
    def __init__(self, config):
        self.config = config
        self.host = 'localhost'
        self.port = config['ssh']['port']
        self.user = config['ssh']['user']
        self.password = config['ssh']['password']
        self.ssh_client = None

    def connect(self, retries=30, interval=5):
        print(f"Attempting to connect to {self.user}@{self.host}:{self.port}...")
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        for i in range(retries):
            try:
                self.ssh_client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.user,
                    password=self.password,
                    timeout=10
                )
                print("SSH connection established.")
                return True
            except (paramiko.BadHostKeyException, paramiko.AuthenticationException,
                    paramiko.SSHException, ConnectionRefusedError) as e:
                print(f"Connection attempt {i+1}/{retries} failed: {e}. Retrying in {interval}s...")
                time.sleep(interval)

        print("Failed to establish SSH connection after multiple attempts.")
        return False

    def execute_command(self, command, description):
        if not self.ssh_client:
            print("SSH client not connected.")
            return False

        print(f"\n[Executing] {description}...")
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()

        if exit_status == 0:
            print(f"[Success] {description}")
            if output:
                print(output)
            return True
        else:
            print(f"[Error] {description} failed with status {exit_status}")
            print(f"Command: {command}")
            print(f"Error Output: {error}")
            return False

    def configure_system(self):
        if not self.connect():
            return

        sub_user = self.config.get('subscription', {}).get('username')
        sub_pass = self.config.get('subscription', {}).get('password')

        if sub_user and sub_pass and sub_user != "CHANGE_ME":
            cmd = f"subscription-manager register --username {sub_user} --password {sub_pass}"
            if not self.execute_command(cmd, "Registering Red Hat Subscription"):
                print("Warning: Subscription registration failed. Updates might fail.")
        else:
            print("Skipping subscription registration (credentials not provided or default).")

        self.execute_command("sudo dnf update -y", "Updating system packages")
        self.execute_command("sudo dnf install -y container-tools nginx", "Installing Podman and Nginx")
        self.execute_command("sudo systemctl enable --now nginx", "Enabling and starting Nginx")

        fw_cmds = [
            "sudo firewall-cmd --permanent --add-service=http",
            "sudo firewall-cmd --permanent --add-service=https",
            "sudo firewall-cmd --reload"
        ]
        for cmd in fw_cmds:
            self.execute_command(cmd, f"Configuring firewall: {cmd}")

        setup_page_cmd = (
            "cd /usr/share/nginx/html && "
            "sudo mv index.html index.html.bak 2>/dev/null || true && "
            "echo '<h1>Hello! Nginx is running on my RHEL VM.</h1>' | sudo tee index.html"
        )
        self.execute_command(setup_page_cmd, "Setting up custom Nginx landing page")

        verify_cmd = "curl -s http://localhost | grep -q 'Nginx is running on my RHEL VM'"
        if self.execute_command(verify_cmd, "Verifying Nginx Installation"):
            print("\n[Verification Passed] Nginx is serving the expected content.")
            self.execute_command("podman --version", "Checking Podman version")
        else:
            print("\n[Verification Failed] Nginx did not return the expected content.")

        self.ssh_client.close()
