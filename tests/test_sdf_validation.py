"""Validate generated worlds with the stock or packaged Gazebo command."""
from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from posim_terrain.terrain import SdfValidationTimeout, build


def source(lon, lat, out):
    return (np.full(lon.shape, -10.), np.array([21., 23.]), np.array([113., 115.]),
            np.full((2, 2), -10.), [dict(url='fixture', requested_url='fixture')], [b'fixture'])


class SdfValidation(unittest.TestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        self.out = Path(resources.enter_context(tempfile.TemporaryDirectory()))
        self.spec = dict(latitude=22., longitude=114., width_m=2000., height_m=2000.,
                         samples=33, source_policy='gebco')
        resources.enter_context(patch('posim_terrain.terrain.gebco_grid', side_effect=source))
        resources.enter_context(patch('posim_terrain.terrain.write_mesh'))
        resources.enter_context(patch('posim_terrain.seabed_visual.decorate'))
        self.run_validator = resources.enter_context(patch('posim_terrain.terrain.subprocess.run'))
        self.run_validator.return_value = subprocess.CompletedProcess([], 0, 'Valid.\n', '')

    def test_default_validator(self):
        meta = build(self.spec, self.out, engine_world=False)
        self.run_validator.assert_called_once_with(
            ['gz', 'sdf', '-k', str(self.out / 'world.sdf')],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(meta['sdf_validation'], 'gz sdf -k passed')
        self.assertEqual((self.out / 'sdf-validation.txt').read_text(), 'Valid.\n')

    def test_packaged_validator_rejects_invalid_world(self):
        executable = '/private/sdk/libexec/gz/sdformat15/gz-sdformat-sdf'
        self.run_validator.return_value = subprocess.CompletedProcess([], 1, '', 'Invalid.\n')
        with self.assertRaisesRegex(ValueError, '^generated_sdf_invalid$'):
            build(self.spec, self.out, engine_world=False, sdf_validator=(executable,))
        self.run_validator.assert_called_once_with(
            [executable, '-k', str(self.out / 'world.sdf')],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual((self.out / 'sdf-validation.txt').read_text(), 'Invalid.\n')

    def test_timeout_has_distinct_error(self):
        cmd = ['gz', 'sdf', '-k', str(self.out / 'world.sdf')]
        self.run_validator.side_effect = subprocess.TimeoutExpired(cmd, 20, b'partial', b'warning')
        with self.assertRaises(SdfValidationTimeout) as raised:
            build(self.spec, self.out, engine_world=False)
        error = raised.exception
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertEqual((error.cmd, error.timeout, error.stdout, error.stderr),
                         (cmd, 20, b'partial', b'warning'))
