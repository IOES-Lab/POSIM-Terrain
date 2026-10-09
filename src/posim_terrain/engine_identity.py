"""Identify the installed engine independently of the application's release."""
import json
import os
from pathlib import Path
import re

SOURCE_REVISION = '23bb608a9a89b9cda3472792316a81711f1ce73e'


def revision():
    # An installed old engine may run a newer app overlay. Never relabel its
    # generated terrain as if the newly checked-out engine were installed.
    value = os.environ.get('WWOS_POSIM_REVISION')
    prefix = os.environ.get('CONDA_PREFIX')
    if value is None and prefix:
        build = Path(prefix) / 'www-posim/BUILD.json'
        if build.is_file():
            value = json.loads(build.read_text())['posim_commit']
    value = SOURCE_REVISION if value is None else value
    if not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('invalid_installed_posim_revision')
    return value
