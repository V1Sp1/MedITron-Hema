"""Cross-platform first setup and local Hema server launcher (Python 3.12)."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check existing environment without installing or starting')
    parser.add_argument('--install', action='store_true', help='Reinstall pinned dependencies')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        print('Python 3.12 is required for this handoff. See docs/06_LOCAL_SETUP.html.')
        return 1
    if sys.version_info < (3, 12, 15):
        print('Security notice: this Python is older than 3.12.15. Update your interpreter before any production deployment. Research mode only; see setup guide.')
    bundle = ROOT / 'experiments/baseline_v1/models/selected.joblib'
    if not bundle.is_file():
        print('Model weights missing. Extract the entire ZIP, not only the launch script.')
        return 1
    environment = ROOT / '.venv'
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    marker = environment / '.hema-setup.json'
    signature = {'root': str(ROOT), 'pyproject_sha256': hashlib.sha256((ROOT / 'pyproject.toml').read_bytes()).hexdigest(), 'constraints_sha256': hashlib.sha256((ROOT / 'requirements-runtime.txt').read_bytes()).hexdigest()}
    probe = "from backend.model_service import ModelService,DEFAULT_MODEL_BUNDLE; from backend.ferritin_service import FerritinService; s=ModelService(DEFAULT_MODEL_BUNDLE).status(); f=FerritinService().status(); print('baseline:',s['state'],'ferritin:',f['state']); raise SystemExit(0 if s['state']=='ready' and f['state']=='ready' else 1)"
    if args.check:
        if not python.is_file():
            print('No local environment yet. Run the launcher without --check to set up.')
            return 1
        return subprocess.call([str(python), '-c', probe], cwd=ROOT)
    previous = None
    if marker.is_file():
        try:
            previous = json.loads(marker.read_text('utf-8'))
        except (ValueError, OSError):
            pass
    if not python.is_file():
        print('Creating .venv with Python 3.12...')
        venv.create(environment, with_pip=True)
    if args.install or previous != signature:
        print('Installing pinned dependencies. Internet is required for this step...')
        subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], cwd=ROOT, check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-c', str(ROOT / 'requirements-runtime.txt'), '-e', '.[server,model]'], cwd=ROOT, check=True)
        marker.write_text(json.dumps(signature, indent=2), encoding='utf-8')
    if subprocess.call([str(python), '-c', probe], cwd=ROOT):
        print('Model is unavailable. Check weights and dependencies. See the setup guide.')
        return 1
    print('Starting Hema at http://127.0.0.1:8000/ . Keep this window open. Stop with Ctrl+C.')
    command = [str(python), '-m', 'backend']
    if sys.platform == 'win32':
        command += ['--ocr', 'off']
    child = subprocess.Popen(command, cwd=ROOT)
    def open_when_ready():
        # Poll only our local service, bounded to one minute.
        for _ in range(60):
            if child.poll() is not None:
                return
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=1) as response:
                    if response.status == 200:
                        webbrowser.open('http://127.0.0.1:8000/')
                        return
            except OSError:
                pass
            time.sleep(1)
    threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        return child.wait()
    except KeyboardInterrupt:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait()
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as error:
        print(f'Setup failed: {error}. See docs/06_LOCAL_SETUP.html.')
        raise SystemExit(1)
