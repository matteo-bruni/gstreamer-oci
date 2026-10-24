import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OrchestrationTests(unittest.TestCase):
    def test_build_selects_explicit_dockerfile_and_context_from_other_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for tool in ('git', 'docker'):
                mock = path / tool
                mock.write_text('#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
                mock.chmod(0o755)
            env = os.environ | {'PATH': str(path) + ':' + os.environ['PATH'],
                                'BUILD_CACHE_FROM': '', 'BUILD_CACHE_TO': ''}
            for ubuntu in ('24.04', '26.04'):
                with self.subTest(ubuntu=ubuntu):
                    result = subprocess.run(['bash', str(ROOT / 'scripts/build.sh'),
                        'release', 'ubuntu', ubuntu, '1.28.8', 'base', 'true', 'false', '3.14', '0.13.5'],
                        cwd=path, env=env, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    args = json.loads(result.stdout)
                    self.assertEqual(args[:2], ['buildx', 'build'])
                    self.assertEqual(args[args.index('--file') + 1], str(ROOT / f'docker/ubuntu-{ubuntu}.Dockerfile'))
                    self.assertEqual(args[-1], str(ROOT))

    def test_just_passes_arguments_without_execution(self):
        if shutil.which('just') is None:
            self.skipTest('just is not installed')
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'executed'
            args = ['release', 'ubuntu', '26.04', f'1.28.8$(touch {marker})', 'base', 'false', 'false', '3.14']
            result = subprocess.run(['just', 'image-tag', *args], cwd=ROOT,
                                    env=os.environ | {'XDG_RUNTIME_DIR': tmp}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def test_readable_choices_build_tags_and_example_match_legacy_booleans(self):
        if shutil.which('just') is None:
            self.skipTest('just is not installed')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for tool in ('git', 'docker'):
                mock = path / tool
                mock.write_text('#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
                mock.chmod(0o755)
            env = os.environ | {'PATH': str(path) + ':' + os.environ['PATH'],
                                'XDG_RUNTIME_DIR': tmp,
                                'BUILD_CACHE_FROM': '', 'BUILD_CACHE_TO': ''}
            for variant, legacy_variant in (('free', 'false'), ('non-free', 'true')):
                for image_target, legacy_target, target in (('runtime', 'false', 'final'), ('dev', 'true', 'gstreamer_builder')):
                    with self.subTest(variant=variant, image_target=image_target):
                        choices = ['ubuntu', '26.04', '1.28.8', 'base', variant, image_target, '3.14']
                        legacy = subprocess.check_output(['bash', 'scripts/image-tag.sh', 'release',
                            'ubuntu', '26.04', '1.28.8', 'base', legacy_variant, legacy_target, '3.14'], cwd=ROOT, text=True).strip()
                        tag = subprocess.check_output(['just', 'image-tag', 'release', *choices],
                            cwd=ROOT, env=env, text=True).strip()
                        self.assertEqual(tag, legacy)
                        for recipe, options in (('build-release', choices), ('build', ['release', *choices])):
                            result = subprocess.run(['just', recipe, *options, '0.13.5'],
                                cwd=ROOT, env=env, text=True, capture_output=True)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            args = json.loads(result.stdout)
                            self.assertIn('VARIANT=' + variant, args)
                            self.assertEqual(args[args.index('--target') + 1], target)
                            self.assertEqual(args[args.index('--tag') + 1], legacy)
                        result = subprocess.run(['just', 'run-example', *choices, 'release'],
                            cwd=ROOT, env=env, text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout)[-2:], [legacy, 'bash'])
            result = subprocess.run(['just', 'build-release'], cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads(result.stdout)
            self.assertIn('VARIANT=free', args)
            self.assertEqual(args[args.index('--target') + 1], 'final')

    def test_tags_and_rejected_input(self):
        args = ['release', 'ubuntu', '26.04', '1.28.8', 'full', 'true', 'false', '3.14']
        result = subprocess.run(['bash', str(ROOT / 'scripts/image-tag.sh'), *args], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'gstreamer:1.28.8-ubuntu.26.04-full-py-3.14-non-free')
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'executed'
            for index, value in ((3, f'1.28.8$(touch {marker})'), (3, 'main'), (4, 'typo'), (5, 'yes'), (5, 'runtime'), (6, 'free'), (7, '3.11'), (2, '25.10')):
                invalid = args.copy()
                invalid[index] = value
                result = subprocess.run(['bash', str(ROOT / 'scripts/image-tag.sh'), *invalid], capture_output=True)
                self.assertNotEqual(result.returncode, 0, value)
            self.assertFalse(marker.exists())

    def run_release(self, failure='', dry_run='false', cache_to='', profile='base'):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            shutil.copytree(ROOT / 'scripts', path / 'scripts')
            mock = path / 'bin'
            mock.mkdir()
            program = '''#!/usr/bin/env python3
import json, os, pathlib, sys
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['TEST_LOG'], 'a') as f: f.write(json.dumps([name,*args])+'\\n')
if name == 'git':
    if args[0]=='rev-parse': print('a'*40)
    elif args[0]=='ls-remote' and 'freedesktop' in ' '.join(args):
        if os.environ.get('TEST_FAILURE')=='upstream': sys.exit(2)
        print('b'*40+'\\trefs/tags/1.28.8')
    elif args[0]=='status' and os.environ.get('TEST_FAILURE')=='dirty': print(' M docker/ubuntu-24.04.Dockerfile')
    elif args[0]=='status' and os.environ.get('TEST_FAILURE')=='changed' and pathlib.Path(os.environ['TEST_IMAGE']).exists(): print(' M docker/ubuntu-24.04.Dockerfile')
    elif args[0]=='ls-remote' and os.environ.get('TEST_FAILURE')=='tag': print('a'*40+'\\trefs/tags/existing')
    elif args[0]=='show-ref': sys.exit(1)
elif name == 'gh':
    if args[:2]==['repo','view']: print('example/repo')
    elif args[:2]==['auth','token']: print('test-token')
    elif args[:2]==['api','user']: print('example')
    elif args[0]=='api' and '/commits/' in args[1]:
        if os.environ.get('TEST_FAILURE')=='commit': sys.exit(1)
        print('a'*40)
    elif args[:2]==['release','list'] and os.environ.get('TEST_FAILURE')=='release':
        print('gst-python-binding-1.28.8-ubuntu-26.04-base-non-free')
elif name == 'docker':
    failure=os.environ.get('TEST_FAILURE','')
    if args[0]=='login': sys.stdin.read()
    if args[:2]==['buildx','build'] and '3.14' in ' '.join(args) and failure=='build': sys.exit(3)
    if args[0]=='run' and '3.14' in ' '.join(args) and failure=='verify': sys.exit(4)
    if args[0]=='create':
        pathlib.Path(os.environ['TEST_IMAGE']).write_text(args[1]); print('test-container')
    elif args[0]=='cp':
        dest=pathlib.Path(args[2]); image=pathlib.Path(os.environ['TEST_IMAGE']).read_text()
        py='314' if '3.14' in image else '312'
        (dest/f'gst_python_binding-1.28.8-cp{py}-cp{py}-linux_x86_64.whl').write_text('binding')
        (dest/'typing_extensions-4.15.0-py3-none-any.whl').write_text('shared dependency')
        (dest/'build-environment.json').write_text(json.dumps({'python':py}))
'''
            for tool in ('git', 'gh', 'docker'):
                file = mock / tool
                file.write_text(program)
                file.chmod(0o755)
            env = os.environ | {'PATH': str(mock) + ':' + os.environ['PATH'],
                'TEST_LOG': str(path / 'commands.jsonl'), 'TEST_IMAGE': str(path / 'image'),
                'TEST_FAILURE': failure, 'GSTREAMER_VERSION': '1.28.8', 'PYTHON_VERSIONS': '3.12 3.14',
                'BASE_IMAGE': 'ubuntu', 'BASE_TAG': '26.04', 'GSTREAMER_BUILD_PROFILE': profile,
                'VARIANT': 'non-free', 'IMAGE_TARGET': 'runtime',
                'DRY_RUN': dry_run, 'GITHUB_ACTIONS': 'false'}
            for key in ('GITHUB_SHA', 'GITHUB_REPOSITORY', 'CUSTOM_RELEASE_TAG', 'CUSTOM_RELEASE_NAME', 'BUILD_CACHE_FROM', 'BUILD_CACHE_TO'):
                env.pop(key, None)
            env['BUILD_CACHE_TO'] = cache_to
            env['GITHUB_SHA'] = 'c' * 40
            result = subprocess.run(['bash', 'scripts/release.sh'], cwd=path, env=env, text=True, capture_output=True)
            commands = [json.loads(line) for line in (path/'commands.jsonl').read_text().splitlines()] if (path/'commands.jsonl').exists() else []
            return result, commands

    def test_only_base_can_be_published(self):
        for profile in ('minimal', 'full'):
            with self.subTest(profile=profile):
                result, commands = self.run_release(profile=profile)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Only the base profile', result.stderr)
                self.assertEqual(commands, [])
                result, commands = self.run_release(profile=profile, dry_run='true')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any(c[:2] == ['docker', 'push'] for c in commands))

    def test_profile_and_variant_meson_options_stop_on_setup_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            for profile in ('minimal', 'base', 'full'):
                for variant in ('free', 'non-free'):
                    with self.subTest(profile=profile, variant=variant):
                        env = os.environ | {'GSTREAMER_BUILD_PROFILE': profile, 'VARIANT': variant,
                            'GSTREAMER_BUILD_TYPE': 'release', 'BUILD_JOBS': '2'}
                        # Fail Meson setup before any build/install command; keep the log in tmp.
                        result = subprocess.run(['bash', '-c', r'''
cd() { :; }
meson() { printf '%s\n' "$@"; return 72; }
export -f cd meson
exec bash "$1"
''', 'test', str(ROOT / 'build-utils/build-native.sh')], cwd=tmp, env=env, text=True, capture_output=True)
                        self.assertEqual(result.returncode, 72, result.stderr)
                        args = result.stdout.splitlines()
                        self.assertIn('-Dlibav=' + ('disabled' if profile == 'minimal' else 'enabled'), args)
                        self.assertIn('-Drs=' + ('enabled' if profile == 'full' else 'disabled'), args)
                        self.assertIn('-Dgpl=' + ('enabled' if variant == 'non-free' else 'disabled'), args)
                        self.assertIn('-Db_lto=true', args)

    def test_second_build_failure_prevents_all_publication(self):
        result, commands = self.run_release('build')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ['docker', 'push'] for c in commands))
        self.assertFalse(any(c[:3] == ['gh', 'release', 'create'] for c in commands))

    def test_second_verification_failure_prevents_all_publication(self):
        result, commands = self.run_release('verify')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ['docker', 'push'] for c in commands))

    def test_publication_uses_source_commit_after_all_checks(self):
        result, commands = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        first_push = next(i for i,c in enumerate(commands) if c[:2] == ['docker', 'push'])
        last_check = max(i for i,c in enumerate(commands) if c[:2] == ['docker', 'run'])
        self.assertLess(last_check, first_push)
        release = next(c for c in commands if c[:3] == ['gh', 'release', 'create'])
        self.assertEqual(release[release.index('--target') + 1], 'a'*40)
        dependencies = [a for a in release if a.endswith('typing_extensions-4.15.0-py3-none-any.whl')]
        self.assertEqual(len(dependencies), 1)

    def test_dry_run_never_publishes(self):
        result, commands = self.run_release(dry_run='true', cache_to='type=registry,ref=example.invalid/cache')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[:2] == ['docker', 'push'] for c in commands))
        self.assertFalse(any(c[:3] == ['gh', 'release', 'create'] for c in commands))
        self.assertFalse(any('--cache-to' in c for c in commands))

    def test_unpushed_commit_prevents_build_and_publication(self):
        result, commands = self.run_release('commit')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:3] == ['docker', 'buildx', 'build'] for c in commands))
        self.assertFalse(any(c[:2] == ['docker', 'push'] for c in commands))

    def test_missing_upstream_tag_prevents_build(self):
        result, commands = self.run_release('upstream')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == 'docker' for c in commands))

    def test_dirty_sources_and_existing_tags_prevent_publication(self):
        for failure in ('dirty', 'release', 'tag'):
            with self.subTest(failure=failure):
                result, commands = self.run_release(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(c[0] == 'docker' for c in commands))

    def test_sources_changed_during_builds_prevent_publication(self):
        result, commands = self.run_release('changed')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ['docker', 'push'] for c in commands))
        self.assertFalse(any(c[:3] == ['gh', 'release', 'create'] for c in commands))


if __name__ == '__main__':
    unittest.main()
