# GitHub Push Plan

Do not push until explicitly requested by the user.

The local repository currently contains the Raspberry Pi setup and vision-nav
scaffold as uncommitted files. The GitHub repository may already contain older
planning documentation, so the first upload should avoid force-pushing over the
remote `main` branch.

## Safe Default Strategy

When the user says `commit and push`, use a feature branch:

```bash
git status --short --branch
git remote -v
./scripts/dev/handoff_audit.sh
git checkout -b codex/pi-vision-nav-setup
git add .gitignore README.md config data docker docs logs map_bundles pyproject.toml requirements scripts src systemd tests transfer
git commit -m "Add Raspberry Pi vision navigation setup"
git push -u origin codex/pi-vision-nav-setup
```

Then inspect or open a PR/merge through GitHub. This avoids overwriting whatever
already exists on `main`.

## If The User Explicitly Wants Main Updated Directly

Fetch and inspect the remote first:

```bash
git fetch origin main
git log --oneline --decorate --max-count=5 origin/main
```

If remote `main` has existing commits, prefer merging this branch into `main`
through GitHub instead of force-pushing.

## Preflight Gates

Before any commit:

```bash
./scripts/dev/handoff_audit.sh
```

Expected checks:

- Python files compile
- Shell scripts parse
- Core unit tests pass
- Required Pi/Mac scripts exist and are executable
- Required config, systemd, transfer, map bundle, and placeholder folders exist
- Expected Python CLI entrypoints exist
- Git `origin` remote exists
- No unrelated agent/chatbot scope appears in repository text

The OpenCV feature-matching test requires `cv2`; it is expected to run on the
Raspberry Pi venv or Docker runtime, not necessarily on the Mac Python.

After the push, the first Pi-side verification command should be:

```bash
cd Drone
./scripts/pi/first_run_checks.sh
```
