# Contributing

Use [Where to edit](WHERE_TO_EDIT.md) to find the owning feature. Keep its Python and web assets together, and use package imports.

From the project root:

```powershell
.venv-training/Scripts/python.exe -m unittest discover -s tests -p 'test_*.py'
.venv-training/Scripts/python.exe -m pi_vision.deployment.deploy plan
```

Some checks need optional dependencies. Browser scripts (`tests/check_*.py`) need running services; read each script before running it.

When changing Pi code, update `pi_vision/deployment/project_layout.py` for new dependencies or assets. Describe the change and how you checked it. Keep credentials, device settings and generated data out of commits.

No project license has been selected.
