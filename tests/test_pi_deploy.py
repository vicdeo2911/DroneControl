import unittest
from unittest.mock import MagicMock, patch

from pi_deploy import CAMERA_FILES, CONTROLLER_FILES, deploy_sources, source_commands


class DeploymentTests(unittest.TestCase):
    def deploy(self):
        client = MagicMock()
        return deploy_sources(client, CAMERA_FILES + CONTROLLER_FILES,
            ('pi-vision', 'pi-remote'), ('http://127.0.0.1:8000/healthz',),
            host='pi', user='user', label='test')

    @patch('pi_deploy.password_for', return_value='test-credential')
    @patch('pi_deploy.execute', side_effect=[0, 0, 0, 0])
    def test_source_deploy_preserves_runtime_files_and_model_override(self, execute, password):
        self.assertEqual(self.deploy(), 0)
        commands = '\n'.join(call.args[1] for call in execute.call_args_list)
        for name in ('remote-calibration.json', 'remote-settings.json', 'remote-power.json',
                     'autonomous-settings.json', 'logs/', 'data/', 'models/', 'systemd/system'):
            self.assertNotIn(name, commands)
        self.assertIn('state_store.py', commands)
        self.assertIn('python3 -m py_compile', commands)
        self.assertNotIn('/api/enable', commands)

    @patch('pi_deploy.password_for', return_value='test-credential')
    @patch('pi_deploy.execute', side_effect=[0, 0, 0, 1, 0])
    def test_failed_readiness_rolls_back_sources_and_restarts_services(self, execute, password):
        with self.assertRaisesRegex(RuntimeError, 'previous files restored'):
            self.deploy()
        restore = execute.call_args.args[1]
        self.assertIn('.absent', restore)
        self.assertIn('systemctl start pi-vision pi-remote', restore)
        self.assertIn('cp -p', restore)

    @patch('pi_deploy.password_for', return_value='test-credential')
    @patch('pi_deploy.execute', side_effect=[0, 0, OSError('connection error'), 0])
    def test_update_exception_also_attempts_rollback(self, execute, password):
        with self.assertRaisesRegex(RuntimeError, 'previous files restored'):
            self.deploy()
        self.assertIn('.absent', execute.call_args.args[1])

    @patch('pi_deploy.execute', return_value=1)
    def test_staging_failure_does_not_stop_services(self, execute):
        self.assertEqual(self.deploy(), 1)
        execute.assert_called_once()
        self.assertNotIn('systemctl', execute.call_args.args[1])

    def test_path_escape_is_rejected(self):
        for name in ('../remote-power.json', '/etc/passwd'):
            with self.assertRaises(ValueError): source_commands([name], 'stage', 'backup', ['pi-vision'])
