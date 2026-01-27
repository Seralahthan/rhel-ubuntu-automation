import subprocess
from pathlib import Path

class KickstartGenerator:
    def __init__(self, config):
        self.config = config
        self.generated_ks_path = Path("ks.cfg")
        self.template_path = Path("ks.cfg.template")
        self.seed_iso_path = Path("seed.iso")

    def generate_ks_cfg(self):
        with open(self.template_path, 'r') as f:
            template = f.read()
        user = self.config['ssh']['user']
        password = self.config['ssh']['password']

        content = template.replace("{{ root_password }}", password)

        if user == 'root':
            user_line_marker = "user --name={{ user_name }}"
            lines = content.splitlines()
            filtered_lines = [l for l in lines if user_line_marker not in l]
            content = "\n".join(filtered_lines)
        else:
            content = content.replace("{{ user_name }}", user)
            content = content.replace("{{ user_password }}", password)

        with open(self.generated_ks_path, 'w') as f:
            f.write(content)
        print(f"Generated Kickstart file at {self.generated_ks_path}")

    def create_seed_iso(self):
        # Optional: build a small ISO labeled OEMDRV with ks.cfg (used by some workflows)
        iso_root = Path("seed_content")
        iso_root.mkdir(exist_ok=True)
        Path(iso_root / "ks.cfg").write_text(self.generated_ks_path.read_text())

        if self.seed_iso_path.exists():
            self.seed_iso_path.unlink()

        cmd = [
            "xorriso", "-as", "mkisofs",
            "-V", "OEMDRV",
            "-o", str(self.seed_iso_path),
            str(iso_root)
        ]
        print(f"Creating seed ISO: {' '.join(cmd)}")
        subprocess.check_call(cmd)

if __name__ == "__main__":
    import yaml
    with open("config.yaml", "r") as f:
        conf = yaml.safe_load(f)
    gen = KickstartGenerator(conf)
    gen.generate_ks_cfg()
    gen.create_seed_iso()
