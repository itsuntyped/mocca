# Mocca

A simple, standalone local AI chat app. Download any GGUF model and chat with
it - everything runs on your machine. No accounts, no cloud, no telemetry.

## Quick start

> Use **Python 3.11 or 3.12** (the inference engine ships prebuilt wheels for
> those).

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1   |   Arch/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scripts/run.py
```

Open <http://localhost:8000>, open **Models**, download a recommended model
(e.g. *Llama 3.2 3B Instruct*), set it as the **Active model**, and start
chatting.

## Data

Everything is stored under `data/` (git-ignored): `config.json`, `mocca.db`
(sessions), `models/` (downloads), and `logs/mocca.log`.

## Build a Windows app (optional)

Package Mocca into a double-clickable Windows app (no Python needed by the
recipient) with PyInstaller. From the project root, with the venv set up and
`llama-cpp-python` installed:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

This installs the build-only deps (`pyinstaller`, `pystray`, `pillow`) and
produces a one-folder app at `packaging\windows\dist\Mocca\Mocca.exe`.

## More

Asset credits are in [ATTRIBUTIONS.md](ATTRIBUTIONS.md).
