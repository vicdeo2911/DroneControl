import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pi_vision.shared.state_store import save_json


class StateStoreTests(unittest.TestCase):
    def test_failed_replace_leaves_prior_settings_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            save_json(path, {'old': True})
            before = path.read_bytes()
            with patch('pi_vision.shared.state_store.os.replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError): save_json(path, {'new': True})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(json.loads(path.with_suffix('.tmp').read_text()), {'new': True})

    def test_preserves_existing_format_options(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            save_json(path, {'outputs_zero': True})
            self.assertEqual(path.read_text(), '{"outputs_zero": true}')
            save_json(path, {'version': 2}, indent=2, newline=True)
            self.assertEqual(path.read_text(), '{\n  "version": 2\n}\n')
            self.assertFalse(path.with_suffix('.tmp').exists())
