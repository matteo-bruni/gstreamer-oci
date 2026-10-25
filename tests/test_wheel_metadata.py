import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'wheel_metadata', Path(__file__).resolve().parents[1] / 'build-utils/wheel-metadata.py')
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


class WheelMetadataTests(unittest.TestCase):
    def test_records_runtime_libraries_headers_and_actual_meson_compiler(self):
        compiler = {'id': 'gcc', 'version': '15.2.0', 'full_version': 'cc (Ubuntu) 15.2.0',
                    'exelist': ['ccache', 'cc']}
        data = {'intro-compilers.json': {'host': {'c': compiler}},
                'meson-info.json': {'meson_version': {'full': '1.12.1'}}}
        with patch.dict('sys.modules', {'cairo': SimpleNamespace(cairo_version_string=lambda: '1.18.4')}), \
                patch.object(metadata.ctypes, 'CDLL'), \
                patch.object(metadata.ctypes.c_uint, 'in_dll', side_effect=[SimpleNamespace(value=n) for n in (2, 88, 1)]), \
                patch.object(metadata.platform, 'libc_ver', return_value=('glibc', '2.43')), \
                patch.object(metadata.subprocess, 'check_output', side_effect=['2.88.0\n', '2.88.0\n', '1.18.4\n']) as command, \
                patch.object(Path, 'read_text', lambda path: json.dumps(data[path.name])):
            result = metadata.native_environment()
        self.assertEqual(result['native_dependencies']['glib'], {'build': '2.88.0', 'runtime': '2.88.1'})
        self.assertEqual(result['native_dependencies']['gobject'], {'build': '2.88.0'})
        self.assertEqual(result['native_dependencies']['cairo'], {'build': '1.18.4', 'runtime': '1.18.4'})
        self.assertEqual(result['native_dependencies']['glibc'], {'runtime': '2.43'})
        self.assertEqual(result['build_tools'], {'meson': '1.12.1', 'c_compiler': compiler})
        self.assertEqual([call.args[0] for call in command.call_args_list],
                         [['pkg-config', '--modversion', package] for package in ('glib-2.0', 'gobject-2.0', 'cairo')])


if __name__ == '__main__':
    unittest.main()
