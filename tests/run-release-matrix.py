"""Build the release-only matrix; keep local reports out of Git and release assets."""
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/verification'
MATRIX = [
    (ubuntu, profile, variant, python)
    for ubuntu, profile, variant, pythons in (
        ('26.04', 'base', 'non-free', ('3.14', '3.12')),
        ('24.04', 'base', 'non-free', ('3.14', '3.12')),
        ('26.04', 'minimal', 'non-free', ('3.14', '3.12')),
        ('24.04', 'minimal', 'non-free', ('3.14', '3.12')),
        ('26.04', 'full', 'non-free', ('3.14', '3.12')),
        ('24.04', 'full', 'non-free', ('3.14', '3.12')),
        ('26.04', 'minimal', 'free', ('3.14',)),
        ('26.04', 'base', 'free', ('3.14',)),
        ('26.04', 'full', 'free', ('3.14',)),
    ) for python in pythons
]


def main(matrix=MATRIX, results=None):
    OUT.mkdir(parents=True, exist_ok=True)
    if results is None:
        results = []
    env = os.environ | {'BUILD_PROGRESS': 'plain', 'XDG_RUNTIME_DIR': '/tmp',
                        'BUILD_CACHE_FROM': '', 'BUILD_CACHE_TO': ''}
    for ubuntu, profile, variant, python in matrix:
        key = f'{ubuntu}-{profile}-{variant}-py{python}'
        folder = OUT / key
        folder.mkdir(exist_ok=True)
        args = ['release', 'ubuntu', ubuntu, '1.28.8', profile, variant, 'runtime', python]
        image = subprocess.check_output(['bash', 'scripts/image-tag.sh', *args], cwd=ROOT, text=True).strip()
        row = dict(ubuntu=ubuntu, profile=profile, variant=variant, python=python, image=image)
        print(f'Building {key}', flush=True)
        try:
            start = time.monotonic()
            with (folder / 'build.log').open('w') as log:
                subprocess.run(['bash', 'scripts/build.sh', *args, '0.13.5'], cwd=ROOT, env=env,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            row['build_seconds'] = round(time.monotonic() - start, 2)
            with (folder / 'checks.log').open('w') as log:
                subprocess.run(['bash', 'scripts/verify-image.sh', image], cwd=ROOT, env=env,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            row['size_bytes'] = int(subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Size}}', image], text=True))
            for filename in ('native-seconds', 'python-seconds', 'native-manifest.sha256', 'meson-options.json', 'build-requirements.txt'):
                data = subprocess.check_output(['docker', 'run', '--rm', '--network=none', image, 'cat', '/opt/build-metadata/' + filename])
                (folder / filename).write_bytes(data)
            row['native_seconds'] = int((folder/'native-seconds').read_text())
            row['python_seconds'] = int((folder/'python-seconds').read_text())
            container = subprocess.check_output(['docker', 'create', image], text=True).strip()
            try:
                subprocess.run(['docker', 'cp', container + ':/opt/wheel/.', str(folder / 'wheel')], check=True)
            finally:
                subprocess.run(['docker', 'rm', '-v', container], check=True, stdout=subprocess.DEVNULL)
            with (folder / 'plugins.txt').open('w') as log:
                subprocess.run(['docker', 'run', '--rm', '--network=none', image, 'gst-inspect-1.0'], stdout=log, stderr=subprocess.STDOUT, check=True)
            row['status'] = 'passed'
        except subprocess.CalledProcessError:
            row['status'] = 'failed'
            raise
        finally:
            results.append(row)
            (OUT / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(f'Passed {key}: {row["build_seconds"]}s', flush=True)


if __name__ == '__main__':
    main()
