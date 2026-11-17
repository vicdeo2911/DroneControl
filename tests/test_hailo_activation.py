import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch


class HailoActivationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        self.job='b'*32
        self.proof='a'*32
        self.folder=self.root/'.hef-inspection'/self.job
        self.folder.mkdir(parents=True)
        (self.folder/'model.hef').write_bytes(b'unit-test-model')
        self.digest=hashlib.sha256(b'unit-test-model').hexdigest()
        self.request=dict(sha256=self.digest,inspection_job=self.proof,confirmed=True)
        self.write_request()
        proof=self.root/'.hef-inspection'/self.proof
        proof.mkdir()
        (proof/'result.json').write_text(json.dumps([dict(model_sha256=self.digest,
            model_input_shape=[640,640,3],preparation='full')]))
        spec=importlib.util.spec_from_file_location('activation_test',Path(__file__).resolve().parents[1]/'activate_hailo.py')
        self.module=importlib.util.module_from_spec(spec)
        fcntl=SimpleNamespace(LOCK_EX=2,LOCK_NB=4,flock=MagicMock())
        inspector=SimpleNamespace(preflight=MagicMock(),read_status=MagicMock())
        with patch.dict(sys.modules,{'fcntl':fcntl,'inspect_hailo':inspector}):
            spec.loader.exec_module(self.module)
        self.module.ROOT=self.root
        self.module.OVERRIDE=self.root/'systemd'/'50-plane-target.conf'
        self.module.OVERRIDE.parent.mkdir()
        self.previous=b'[Service]\nExecStart=old-settings\n'
        self.module.OVERRIDE.write_bytes(self.previous)
        self.before=dict(ready=True,model='old.hef',model_input_shape=[640,640,3])

    def tearDown(self):
        self.directory.cleanup()

    def write_request(self):
        (self.folder/'request.json').write_text(json.dumps(self.request))

    def patches(self):
        return (patch.object(self.module,'preflight'),
            patch.object(self.module,'read_status',return_value=self.before),
            patch.object(self.module,'current_arguments',return_value=['--fps','50','--threshold','0.55',
                '--quality','72','--model=old.hef','--labels','old-labels.txt','--detection-mode','adaptive']))

    def test_activation_preserves_camera_arguments_and_selects_full_frame(self):
        a,b,c=self.patches()
        expected='studio-'+self.digest+'.hef'
        with a,b,c,patch.object(self.module,'restart') as restart, \
             patch.object(self.module,'wait_camera',return_value=dict(model=expected)):
            result=self.module.activate(self.job)
        config=self.module.OVERRIDE.read_text()
        self.assertIn('"--fps" "50"',config)
        self.assertIn('"--threshold" "0.55"',config)
        self.assertIn('"--quality" "72"',config)
        self.assertIn('"--detection-mode" "full"',config)
        self.assertNotIn('old.hef',config)
        self.assertTrue(result['activated'])
        self.assertEqual(result['model'],expected)
        self.assertEqual((self.folder/'override.before').read_bytes(),self.previous)
        restart.assert_called_once()

    def test_failed_startup_restores_exact_previous_override(self):
        a,b,c=self.patches()
        with a,b,c,patch.object(self.module,'restart') as restart, \
             patch.object(self.module,'wait_camera',side_effect=[RuntimeError('not ready'),self.before]):
            with self.assertRaisesRegex(ValueError,'previous detector was restored'):
                self.module.activate(self.job)
        self.assertEqual(self.module.OVERRIDE.read_bytes(),self.previous)
        self.assertEqual(restart.call_count,2)

    def test_failure_without_previous_override_removes_new_override(self):
        self.module.OVERRIDE.unlink()
        a,b,c=self.patches()
        with a,b,c,patch.object(self.module,'restart'), \
             patch.object(self.module,'wait_camera',side_effect=[RuntimeError('not ready'),self.before]):
            with self.assertRaises(ValueError):self.module.activate(self.job)
        self.assertFalse(self.module.OVERRIDE.exists())

    def test_changed_file_or_unconfirmed_request_is_rejected_before_preflight(self):
        for cause in ('file','confirmation'):
            if cause=='file':(self.folder/'model.hef').write_bytes(b'changed')
            else:self.request['confirmed']=False;self.write_request()
            with patch.object(self.module,'preflight') as preflight:
                with self.assertRaises(ValueError):self.module.activate(self.job)
                preflight.assert_not_called()
            self.assertEqual(self.module.OVERRIDE.read_bytes(),self.previous)

    def test_guard_rejects_active_controls_before_writing_configuration(self):
        with patch.object(self.module,'preflight',side_effect=ValueError('Stop controls')):
            with self.assertRaisesRegex(ValueError,'Stop controls'):self.module.activate(self.job)
        self.assertEqual(self.module.OVERRIDE.read_bytes(),self.previous)

    def test_systemd_arguments_escape_literals_and_replace_both_flag_forms(self):
        self.assertEqual(self.module.systemd_quote('a $b %c "d"'), '"a $$b %%c \\"d\\""')
        self.assertEqual(self.module.replace_option(['--model=old','--fps','50','--model','older'],
            '--model','new'),['--fps','50','--model','new'])
