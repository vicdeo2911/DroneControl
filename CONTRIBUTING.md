# Contributing

Describe the problem, expected behavior, and affected service in an issue or pull request. Include reproduction steps and relevant logs with credentials removed.

## Local development

Use [README.md](README.md) for setup and [ARCHITECTURE.md](ARCHITECTURE.md) to locate the owning module. Keep root module paths stable: service units, deployment manifests, and tests import them directly.

On Windows, run focused offline checks from the project root:

```powershell
.venv-training/Scripts/python.exe -m unittest discover -s tests -p test_project_layout.py
.venv-training/Scripts/python.exe -m unittest discover -s tests -p test_state_store.py
.venv-training/Scripts/python.exe deploy.py plan
```

Run additional `test_*.py` modules relevant to the change. Some tests need optional dependencies or a configured environment. The `check_*.py` browser scripts and live hardware checks require running services; read them before execution.

## Pull requests

- Explain the resulting behavior and how you verified it.
- Add new Pi runtime dependencies and served assets to `project_layout.py` when needed.
- Keep calibration, credentials, recordings, datasets, generated models, and runtime settings out of commits.
- Keep source deployment and model activation separate. Test hardware changes with propulsion disconnected and verify stop behavior before operation.

No project license has been selected yet. Do not assume that third-party models, datasets, or dependencies share the same redistribution terms.
