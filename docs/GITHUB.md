# GitHub workflow

Repository: https://github.com/vicdeo2911/DroneControl

## Publishing this workspace

From the project root, inspect the pending files before committing:

```powershell
git status --short
git diff --cached --stat
```

The repository excludes Python environments, training data, model binaries, local device settings, credentials, and generated logs. These remain on disk and must be provisioned separately on a new machine. Source files and technical documentation are included.

After reviewing staged files, create a commit and push to the configured remote. If the remote already contains commits, fetch and integrate its history before pushing; avoid force pushing over existing work.

Git may open an authentication prompt for GitHub. Authenticate through Git's credential helper; never put passwords or access tokens in source files or a remote URL.

## Future changes

```powershell
git status
git add README.md
git diff --cached
git commit -m "docs: clarify setup instructions"
git push
```

Replace `README.md` with the files you intend to commit. For feature work, create a branch with `git switch -c feature/your-change`, push it using `git push -u origin feature/your-change`, and open a pull request.

## Repository presentation

Suggested description: **Raspberry Pi visual aircraft tracking and experimental RC control with Hailo inference, a GP8413 DAC, and a YOLO training studio.**

Suggested topics: `raspberry-pi`, `computer-vision`, `hailo`, `yolo`, `python`, `flask`, `rc-aircraft`.

Add real interface screenshots or a demonstration video when available. Choose a project license deliberately, taking account of bundled code and third-party dependencies, before advertising the project as open source.
