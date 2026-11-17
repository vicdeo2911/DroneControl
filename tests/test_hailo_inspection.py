import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from hailo_inspection import compiled_models
from test_training import picture
from training_studio import create_app
from remote_control import Remote
from test_remote_control import FakeBus


class HailoInspectionTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def hef(self,relative,content=b'unit-test-hef',size=640):
        path=self.root/'runs'/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
        (path.parent/'model-input.json').write_text(json.dumps(dict(input_size=size)))
        return path

    def test_discovers_fixed_sizes_deduplicates_copies_and_excludes_active_run(self):
        self.hef('old/compiled/model.hef')
        self.hef('old/hailo-deployment/compiled/model.hef')
        self.hef('old/resolution-candidates/1920/compiled-1920/model.hef',b'large-hef',1920)
        self.hef('running/compiled/model.hef',b'partial-hef')
        records=compiled_models(self.root/'runs',active_run='running')
        self.assertEqual(len(records),2)
        self.assertEqual({r['input_size'] for r in records},{640,1920})
        self.assertFalse(any('hailo-deployment' in r['path'] for r in records))

    def test_prefers_standard_run_output_over_nested_legacy_copy(self):
        self.hef('old/compiled-1920/model.hef', b'large-hef', 1920)
        self.hef('old/resolution-candidates/1920/compiled-1920/model.hef', b'large-hef', 1920)
        records = compiled_models(self.root/'runs')
        self.assertEqual([r['path'] for r in records], ['old/compiled-1920/model.hef'])

    def app(self):
        app=create_app(self.root/'data',runs=self.root/'runs')
        app.testing=True
        ident,_=app.extensions['dataset'].add(picture(),'test-frame','val')
        return app,ident

    def test_api_runs_selected_hef_on_pi_and_uses_compiled_size(self):
        path=self.hef('old/compiled/model.hef')
        app,ident=self.app()
        expected=dict(frame_size=[160,90],model_input_shape=[640,640,3],detections=[],
                      tiles=1,elapsed_ms=16,inference_ms=14)
        with patch('training_studio.inspect_on_pi',return_value=expected) as inspect:
            response=app.test_client().post('/api/predict',json=dict(weights='old/compiled/model.hef',
                image_id=ident,imgsz=1920,preparation='full',confidence=.55))
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(response.json['backend'],'hailo')
        self.assertEqual(response.json['detector_input_size'],640)
        self.assertEqual(response.json['mode'],'observation_only')
        self.assertFalse(response.json['passage_confirmed'])
        self.assertEqual(inspect.call_args.args[0],path)
        self.assertEqual(inspect.call_args.args[2:],(.55,'full'))

    def test_invalid_artifact_and_confidence_never_contact_pi(self):
        self.hef('old/compiled/model.hef')
        app,ident=self.app()
        with patch('training_studio.inspect_on_pi') as inspect:
            for changes in (dict(weights='../private.hef'),dict(confidence=0),dict(preparation='bad')):
                options=dict(weights='old/compiled/model.hef',image_id=ident)
                response=app.test_client().post('/api/predict',json={**options,**changes})
                self.assertEqual(response.status_code,400,response.json)
            inspect.assert_not_called()

    def test_pi_refusal_is_a_visible_error(self):
        self.hef('old/compiled/model.hef')
        app,ident=self.app()
        with patch('training_studio.inspect_on_pi',side_effect=ValueError('Stop controls first.')):
            response=app.test_client().post('/api/predict',json=dict(weights='old/compiled/model.hef',image_id=ident))
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.json['error'],'Stop controls first.')

    def test_control_start_guard_rejects_before_any_dac_write(self):
        remote=Remote(FakeBus())
        before=list(remote.bus.writes)
        with patch('remote_control.operation_guard',side_effect=ValueError('Inspection busy')):
            with self.assertRaisesRegex(ValueError,'Inspection busy'):
                remote.enable()
        with patch('autonomous.operation_guard',side_effect=ValueError('Inspection busy')), \
             patch('autonomous.camera_status') as camera:
            with self.assertRaisesRegex(ValueError,'Inspection busy'):
                remote.autonomy.start_from_camera('behind')
            camera.assert_not_called()
        self.assertEqual(before,remote.bus.writes)

    def test_web_ssh_never_prompts_for_password(self):
        from pi_ssh import password_for
        with patch('pi_ssh.credential_store') as store,patch('pi_ssh.getpass.getpass') as prompt:
            store.return_value.get_password.return_value=None
            with self.assertRaisesRegex(RuntimeError,'Save the Pi SSH credential'):
                password_for('unit-test','user',prompt=False)
            prompt.assert_not_called()

    def prepare_tested_model(self,preparation='full'):
        path=self.hef('old/compiled/model.hef')
        app,ident=self.app()
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        result=dict(frame_size=[160,90],model_input_shape=[640,640,3],detections=[],
                    tiles=1,elapsed_ms=16,inference_ms=14,model_sha256=digest,
                    inspection_job='a'*32,preparation=preparation)
        client=app.test_client()
        with patch('training_studio.inspect_on_pi',return_value=result):
            response=client.post('/api/predict',json=dict(weights='old/compiled/model.hef',
                image_id=ident,preparation=preparation))
        self.assertEqual(response.status_code,200,response.json)
        return client,path,digest,response.json

    def test_activation_requires_successful_exact_file_test_and_confirmation(self):
        self.hef('old/compiled/model.hef')
        app,_=self.app()
        with patch('training_studio.activate_on_pi') as activate:
            response=app.test_client().post('/api/hailo/activate',json=dict(
                weights='old/compiled/model.hef',sha256='f'*64,confirmed=True))
            self.assertEqual(response.status_code,400)
            activate.assert_not_called()
        client,path,digest,result=self.prepare_tested_model()
        self.assertTrue(result['activation_ready'])
        with patch('training_studio.activate_on_pi') as activate:
            for confirmed in (False,1,'yes'):
                response=client.post('/api/hailo/activate',json=dict(
                    weights='old/compiled/model.hef',sha256=digest,confirmed=confirmed))
                self.assertEqual(response.status_code,400)
            activate.assert_not_called()

    def test_confirmed_activation_passes_server_owned_test_proof(self):
        client,path,digest,_=self.prepare_tested_model()
        result=dict(activated=True,model='new.hef',model_input_shape=[640,640,3],sha256=digest)
        with patch('training_studio.activate_on_pi',return_value=result) as activate:
            response=client.post('/api/hailo/activate',json=dict(
                weights='old/compiled/model.hef',sha256=digest,confirmed=True))
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(activate.call_args.args[0],path)
        self.assertEqual(activate.call_args.args[1]['inspection_job'],'a'*32)
        self.assertEqual(activate.call_args.args[1]['sha256'],digest)

    def test_changed_hef_and_native_only_test_do_not_allow_activation(self):
        client,path,digest,_=self.prepare_tested_model()
        path.write_bytes(b'replaced-compiled-artifact')
        with patch('training_studio.activate_on_pi') as activate:
            response=client.post('/api/hailo/activate',json=dict(
                weights='old/compiled/model.hef',sha256=digest,confirmed=True))
            self.assertEqual(response.status_code,400)
            activate.assert_not_called()
        client,_,digest,result=self.prepare_tested_model(preparation='adaptive')
        self.assertFalse(result['activation_ready'])
        with patch('training_studio.activate_on_pi') as activate:
            response=client.post('/api/hailo/activate',json=dict(
                weights='old/compiled/model.hef',sha256=digest,confirmed=True))
            self.assertEqual(response.status_code,400)
            activate.assert_not_called()


class PiInspectionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.root=Path(self.directory.name)
        self.job='a'*32
        folder=self.root/'.hef-inspection'/self.job
        folder.mkdir(parents=True)
        (folder/'request.json').write_text(json.dumps(dict(confidence=.55,preparation='full')))
        spec=importlib.util.spec_from_file_location('pi_inspection_test',Path(__file__).resolve().parents[1]/'inspect_hailo.py')
        self.module=importlib.util.module_from_spec(spec)
        fcntl=SimpleNamespace(LOCK_EX=2,LOCK_NB=4,flock=MagicMock())
        with patch.dict(sys.modules,{'fcntl':fcntl}):spec.loader.exec_module(self.module)
        self.module.ROOT=self.root

    def tearDown(self):
        self.directory.cleanup()

    def test_failed_inference_still_restarts_camera_and_never_changes_override(self):
        calls=[]
        def run(command,**kwargs):
            calls.append(command)
            if any('verify_hailo.py' in part for part in command):
                raise subprocess.CalledProcessError(1,command)
        with patch.object(self.module,'preflight'),patch.object(self.module.subprocess,'run',side_effect=run), \
             patch.object(self.module,'read_status',return_value={'ready':True}):
            with self.assertRaisesRegex(ValueError,'HEF inference failed'):self.module.inspect(self.job)
        self.assertEqual(calls[0],['systemctl','stop','pi-vision'])
        self.assertEqual(calls[-1],['systemctl','start','pi-vision'])
        self.assertFalse(any('daemon-reload' in command for command in calls))

    def test_active_controls_refuse_before_stopping_camera(self):
        with patch.object(self.module,'preflight',side_effect=ValueError('Stop controls')), \
             patch.object(self.module.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'Stop controls'):self.module.inspect(self.job)
            run.assert_not_called()

    def test_lock_contention_refuses_before_preflight(self):
        with patch.object(self.module.fcntl,'flock',side_effect=BlockingIOError), \
             patch.object(self.module,'preflight') as preflight:
            with self.assertRaisesRegex(ValueError,'Another camera operation'):self.module.inspect(self.job)
            preflight.assert_not_called()

    def test_old_controller_without_operation_guard_is_rejected(self):
        with patch.object(self.module,'read_status',side_effect=[{}, {}, {'current':{'active':False}}]):
            with self.assertRaisesRegex(ValueError,'Update and restart'):
                self.module.preflight()
