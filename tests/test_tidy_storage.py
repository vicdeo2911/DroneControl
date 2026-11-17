import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pi_vision.maintenance.tidy_storage import apply_local, apply_pi, local_plan, pi_plan, relocate


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def file(self, relative, content=b'model'):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_local_normalizes_and_archives_duplicate_without_losing_bytes(self):
        source = self.file('runs/plane-target/run/resolution-candidates/1920/compiled-1920/plane-target-h8.hef')
        (source.parent/'model-input.json').write_text(json.dumps({'input_size': 1920}))
        duplicate = self.file('runs/plane-target/run/resolution-candidates/1920/hailo-deployment/compiled/plane-target-h8.hef')
        labels = self.file('data/plane-target/annotations.sqlite3', b'unchanged')
        plan = local_plan(self.root)
        apply_local(self.root, plan, 'test')
        canonical = self.root/'runs/plane-target/run/compiled-1920/plane-target-h8.hef'
        self.assertEqual(canonical.read_bytes(), b'model')
        self.assertEqual((canonical.parent/'model-input.json').read_text(), '{"input_size": 1920}')
        self.assertFalse(source.exists())
        self.assertFalse(duplicate.exists())
        self.assertEqual(len(list((self.root/'maintenance/archive').rglob('*.hef'))), 1)
        self.assertEqual(labels.read_bytes(), b'unchanged')
        self.assertEqual(local_plan(self.root), {'moves': [], 'duplicates': []})

    def test_pi_plan_selects_only_known_historical_paths(self):
        for path in ('.autonomous-stage-20261110/app.py', '.native-crop-backup-20261110/app.py',
                     '.hef-inspection/job/result.json', 'remote-power.json', 'data/clip.avi'):
            self.file(path)
        plan = pi_plan(self.root)
        self.assertEqual(plan['archives'], ['.autonomous-stage-20261110', '.native-crop-backup-20261110'])

    @unittest.skipIf(__import__('sys').platform == 'win32', 'Pi symlinks require Linux; verified on Pi separately')
    def test_pi_deduplicates_and_preserves_old_paths_and_active_model(self):
        self.check_pi_apply()

    def check_pi_apply(self):
        sha = hashlib.sha256(b'model').hexdigest()
        canonical = self.file('models/studio-'+sha+'.hef')
        legacy = self.file('models/plane-target-old-h8.hef')
        self.file('models/plane-labels.txt', b'plane\ntarget\n')
        active = self.file('remote-power.json', b'zero-state')
        self.file('.autonomous-backup-20261110/app.py', b'old-code')
        plan = pi_plan(self.root)
        index = apply_pi(self.root, plan, 'test', [], sha)
        self.assertEqual(legacy.read_bytes(), canonical.read_bytes())
        self.assertTrue(legacy.is_symlink())
        self.assertEqual(len(index['models']), 1)
        self.assertTrue(index['models'][0]['active_at_audit'])
        self.assertEqual(active.read_bytes(), b'zero-state')
        self.assertEqual((self.root/'maintenance/archive/test/.autonomous-backup-20261110/app.py').read_bytes(), b'old-code')
        apply_pi(self.root, pi_plan(self.root), 'again', [], sha)
        self.assertEqual(legacy.read_bytes(), b'model')

    def test_relocate_rejects_escape_and_existing_destination(self):
        source = self.file('model.hef')
        dest = self.file('taken.hef', b'other')
        for target in (dest, self.root.parent/'outside'):
            with self.assertRaises(ValueError):
                relocate(self.root, source, target)
        self.assertEqual(source.read_bytes(), b'model')
