import json
from pathlib import Path
import shutil
import subprocess
import unittest

from experiment_engine import DEFAULT_CONFIG, generate_trials


class StudyConditionsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is needed to execute study UI logic')
    def test_all_conditions_use_experiment_values_without_editing_presets(self):
        root = Path(__file__).resolve().parents[1]
        fixture = {'config': DEFAULT_CONFIG, 'trials': generate_trials(DEFAULT_CONFIG, 0)}
        result = subprocess.run(
            ['node', 'tests/study_conditions.cjs'], cwd=root,
            input=json.dumps(fixture), text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
