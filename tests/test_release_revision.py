import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    'release_revision', Path(__file__).resolve().parents[1] / 'scripts/check-release-revision.py')
revision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(revision)

CONFIGURATION = ('1.28.8', 'ubuntu', '24.04', 'base', 'non-free', 'runtime', 'release')


class ReleaseRevisionTests(unittest.TestCase):
    def setUp(self):
        self.metadata = {}

    def release(self, number=1, tag=None, configuration=CONFIGURATION, draft=False, prerelease=False):
        version, base, ubuntu, profile, variant, target, build_type = configuration
        tag = tag or f'custom-{len(self.metadata)}'
        assets = []
        for py in ('3.12', '3.14'):
            asset = {'name': f'py{py}-build-metadata.json', 'id': len(self.metadata)}
            self.metadata[asset['id']] = {
                'repository': 'example/repo', 'release_tag': tag, 'release_revision': number,
                'base_image': base, 'base_tag': ubuntu, 'build_profile': profile,
                'variant': variant, 'image_target': target, 'build_type': build_type,
                'python_version': py, 'build': {'gstreamer_version': version},
            }
            assets.append(asset)
        return {'tag_name': tag, 'draft': draft, 'prerelease': prerelease, 'assets': assets}

    def check(self, releases, requested):
        revision.check_revision('example/repo', releases, CONFIGURATION, requested,
                                lambda asset: self.metadata[asset['id']])

    def test_first_and_consecutive_releases_include_prereleases_and_custom_tags(self):
        self.check([], 1)
        first = self.release(tag='first-publication')
        self.check([first], 2)
        second = self.release(2, tag='preview', prerelease=True)
        self.check([second, first], 3)
        for requested in (1, 2, 4):
            with self.subTest(requested=requested), self.assertRaisesRegex(ValueError, 'allowed revision r3'):
                self.check([first, second], requested)

    def test_missing_initial_release_and_gaps_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'allowed revision r1'):
            self.check([], 2)
        with self.assertRaisesRegex(ValueError, 'history has gaps'):
            self.check([self.release(2)], 3)
        with self.assertRaisesRegex(ValueError, 'history has gaps'):
            self.check([self.release(), self.release(3)], 4)

    def test_draft_reserves_revision_without_advancing_sequence(self):
        first = self.release()
        draft = self.release(2, draft=True)
        with self.assertRaisesRegex(ValueError, 'reserved by a draft'):
            self.check([first, draft], 2)
        with self.assertRaisesRegex(ValueError, 'allowed revision r2'):
            self.check([first, draft], 3)
        with self.assertRaisesRegex(ValueError, 'reserved by a draft'):
            self.check([self.release(draft=True)], 1)

    def test_each_configuration_dimension_has_an_independent_sequence(self):
        for field, value in ((0, '1.28.9'), (2, '26.04'), (3, 'full'), (4, 'free'),
                             (5, 'dev'), (6, 'debug')):
            other = list(CONFIGURATION)
            other[field] = value
            with self.subTest(field=field):
                self.check([self.release(configuration=tuple(other))], 1)

    def test_python_versions_share_one_revision_and_must_agree(self):
        first = self.release()
        self.check([first], 2)
        self.metadata[first['assets'][1]['id']]['release_revision'] = 2
        with self.assertRaisesRegex(ValueError, 'Conflicting Python'):
            self.check([first], 2)

    def test_duplicate_revisions_and_invalid_metadata_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Multiple releases'):
            self.check([self.release(), self.release()], 2)
        with self.assertRaisesRegex(ValueError, 'same revision'):
            self.check([self.release(), self.release(draft=True)], 2)
        first = self.release()
        original = copy.deepcopy(self.metadata[first['assets'][0]['id']])
        for field, value in (('release_revision', True), ('repository', 'wrong/repo'),
                             ('variant', 'unknown'), ('release_tag', 'wrong-tag')):
            self.metadata[first['assets'][0]['id']] = original | {field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check([first], 2)

    def test_canonical_legacy_tags_with_or_without_metadata(self):
        tag = 'gst-python-binding-1.28.8-ubuntu-24.04-base-non-free'
        legacy = {'tag_name': tag, 'draft': False, 'assets': []}
        self.check([legacy], 2)
        self.check([legacy, legacy | {'tag_name': tag + '-r2'}], 3)
        legacy['assets'] = [{'name': 'py3.12-build-metadata.json', 'id': 0}]
        self.metadata[0] = {'gstreamer_version': '1.28.8', 'base_tag': '24.04', 'non_free': True}
        self.check([legacy], 2)
        self.metadata[0]['non_free'] = 'true'
        self.check([legacy], 2)
        self.metadata[0]['non_free'] = False
        with self.assertRaisesRegex(ValueError, 'Conflicting historical'):
            self.check([legacy], 2)

    def test_ambiguous_history_and_tag_metadata_conflicts_fail_closed(self):
        unknown = {'tag_name': 'unknown-old-release', 'draft': False, 'assets': []}
        with self.assertRaisesRegex(ValueError, 'Cannot identify'):
            self.check([unknown], 1)
        canonical = 'gst-python-binding-1.28.8-ubuntu-24.04-base-non-free'
        with self.assertRaisesRegex(ValueError, 'Tag and metadata disagree'):
            self.check([self.release(2, tag=canonical)], 1)
        first = self.release()
        del self.metadata[first['assets'][0]['id']]['release_revision']
        with self.assertRaisesRegex(ValueError, 'Incomplete modern'):
            self.check([first], 1)
        first = self.release()
        self.metadata[first['assets'][0]['id']]['build']['variant'] = 'free'
        with self.assertRaisesRegex(ValueError, 'Conflicting nested'):
            self.check([first], 2)


if __name__ == '__main__':
    unittest.main()
