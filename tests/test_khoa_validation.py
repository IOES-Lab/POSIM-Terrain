import tempfile
import unittest
from pathlib import Path

from netCDF4 import Dataset

from posim_terrain.bathymetry import validate_khoa_file


class KhoaValidation(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "khoa.nc"

    def write_fixture(self, names=("Lat", "Lon", "MSL"), count=32):
        with Dataset(self.path, "w") as data:
            data.createDimension("points", count or None)
            for name in names:
                variable = data.createVariable(name, "f4", ("points",))
                if count:
                    variable[:] = list(range(count))

    def test_complete_file_is_accepted(self):
        self.write_fixture()
        validate_khoa_file(self.path)

    def test_missing_required_variable_is_rejected(self):
        self.write_fixture(names=("Lat", "Lon"))
        with self.assertRaisesRegex(ValueError, "unsupported_khoa_release_schema"):
            validate_khoa_file(self.path)

    def test_empty_variables_are_rejected(self):
        self.write_fixture(count=0)
        with self.assertRaisesRegex(ValueError, "empty_khoa_variable"):
            validate_khoa_file(self.path)

    def test_truncated_hdf5_is_rejected(self):
        self.write_fixture()
        with self.path.open("r+b") as stream:
            stream.truncate(self.path.stat().st_size // 2)
        with self.assertRaises(OSError):
            validate_khoa_file(self.path)
