"""Source payload completeness and safe deployment entrypoint checks."""
import ast
import contextlib
import io
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import pi_vision.deployment.deploy as deploy
from pi_vision.deployment.project_layout import TARGETS, source_path, validate_layout, validate_source_paths

ROOT = Path(__file__).resolve().parents[1]


class LayoutTests(unittest.TestCase):
    def test_payloads_include_local_import_dependencies(self):
        validate_layout(ROOT)
        modules = {path.relative_to(ROOT).with_suffix('').as_posix().replace('/', '.'): path.relative_to(ROOT).as_posix() for path in ROOT.glob('pi_vision/**/*.py')}
        for target, spec in TARGETS.items():
            for name in spec.files:
                if not name.endswith('.py'):
                    continue
                tree = ast.parse(source_path(ROOT, name).read_text(encoding='utf-8-sig'))
                for node in ast.walk(tree):
                    imports = [node.module] if isinstance(node, ast.ImportFrom) else (
                        [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
                    for module in imports:
                        if module in modules:
                            self.assertIn(modules[module], spec.files, (target, name, module))

    def test_controller_handler_assets_are_in_payload(self):
        tree = ast.parse((ROOT/'pi_vision/control/remote_control.py').read_text(encoding='utf-8'))
        assets = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                  and isinstance(node.value, str)
                  and node.value.endswith(('.html', '.js', '.css'))
                  and (ROOT/'pi_vision/control/templates'/node.value).is_file()}
        self.assertTrue(assets)
        for asset in assets:
            self.assertIn('pi_vision/control/templates/'+asset, TARGETS['controller'].files)

    def test_package_initializers_are_in_each_payload(self):
        for target, spec in TARGETS.items():
            for name in spec.files:
                if name.startswith('pi_vision/') and name.endswith('.py'):
                    for parent in Path(name).parents:
                        if parent == Path('.'):
                            continue
                        self.assertIn((parent/'__init__.py').as_posix(), spec.files, (target, name))

    def test_runtime_paths_and_escapes_rejected(self):
        for name in ('../app.py', '/etc/passwd', 'models/model.hef', 'logs/flights.sqlite3',
                     'remote-power.json', 'data/annotations.sqlite3', 'C:/app.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_source_paths([name])

    @patch('pi_vision.deployment.deploy.connect')
    def test_plan_needs_no_pi_or_credentials(self, connect):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deploy.main(['plan']), 0)
        connect.assert_not_called()

    @patch('pi_vision.deployment.deploy.deploy_sources')
    @patch('pi_vision.deployment.deploy.read_json')
    def test_apply_refuses_enabled_controls_and_active_recording(self, read, sources):
        for states in (({'enabled': True}, {'active': False}),
                       ({'enabled': False}, {'active': True}),
                       ({'enabled': False}, {'active': False}, {'current': {'active': True}})):
            read.side_effect = states
            with self.assertRaises(RuntimeError):
                deploy.deploy_target(MagicMock(), 'all', host='pi', user='user')
        sources.assert_not_called()

    @patch('pi_vision.deployment.deploy.deploy_sources', return_value=0)
    @patch('pi_vision.deployment.deploy.read_json', side_effect=[{'enabled': False}, {'active': False}, {'current': {'active': False}}])
    def test_apply_uses_selected_manifest(self, read, sources):
        client = MagicMock()
        self.assertEqual(deploy.deploy_target(client, 'all', host='pi', user='user'), 0)
        self.assertEqual(sources.call_args.args[1], TARGETS['all'].files)

    def test_audit_reads_only_and_reports_missing_and_changed(self):
        client = MagicMock()
        sftp = client.open_sftp.return_value.__enter__.return_value
        stream = sftp.open.return_value.__enter__.return_value
        stream.read.side_effect = [b'older bytes', b'']
        rows = deploy.source_audit(client, ['pi_vision/camera/app.py'])
        self.assertEqual(rows[0]['state'], 'different')
        sftp.open.side_effect = FileNotFoundError()
        self.assertEqual(deploy.source_audit(client, ['pi_vision/camera/app.py'])[0]['state'], 'missing')
        client.exec_command.assert_not_called()
        sftp.put.assert_not_called()

    def test_audit_compares_legacy_destination_with_relocated_source(self):
        client = MagicMock()
        sftp = client.open_sftp.return_value.__enter__.return_value
        stream = sftp.open.return_value.__enter__.return_value
        stream.read.side_effect = [source_path(ROOT, 'app.py').read_bytes(), b'']
        self.assertEqual(deploy.source_audit(client, ['app.py'])[0]['state'], 'same')
        sftp.open.assert_called_once_with('/home/user/pi-vision/app.py', 'rb')
