# Mocca

A simple, standalone local AI chat app. Download any GGUF model and chat with
it - everything runs on your machine. No accounts, no cloud, no telemetry. The
assistant can also search the web when you ask it something current (a single
toggle turns this off to stay fully offline).

![Mocca - the chat UI with the monochrome dark theme](https://i.imgur.com/fSRgBVR.png)

## Quick start

> Use **Python 3.11 or 3.12**. These have prebuilt wheels for the inference
> engine, including the optional GPU (CUDA) build. Newer Python (3.13/3.14) runs
> fine on CPU but has no prebuilt CUDA wheel yet.

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1   |   Arch/Linux: source .venv/bin/activate
python scripts/setup.py     # installs deps; auto-picks the GPU build if an NVIDIA GPU is found
python scripts/run.py
```

`scripts/setup.py` installs the dependencies and, if it detects an NVIDIA GPU,
installs the CUDA build of the engine automatically (otherwise the CPU build).
Force it either way with `--cuda` / `--cpu`. See [GPU acceleration](#gpu-acceleration-nvidia)
below. (Plain `pip install -r requirements.txt` still works for a CPU-only setup.)

Open <http://localhost:8000>, open **Models**, download a recommended model
(e.g. *Llama 3.2 3B Instruct*), set it as the **Active model**, and start
chatting.

## Tools

The assistant can use **tools** - a calculator, the current date/time, unit
conversion, reading text files you drop in `data/files/`, and **web search**
(plus fetching a URL). Capable models call them automatically; the calls run
behind the scenes and are not shown in the chat.

All the local tools are always available. **Web search** is the one capability
that reaches the internet: it is **on by default** so the assistant can answer
questions about current information, but a single **Enable web search** toggle
under **Settings -> Capability** turns it off to keep Mocca fully offline. Tool use
works best with larger, tool-capable models; smaller models still run, just less
reliably.

## GPU acceleration (NVIDIA)

Running models on an NVIDIA GPU is far faster than CPU. `python scripts/setup.py`
sets this up automatically when it detects a GPU, but the details:

- **Python 3.11 or 3.12** is required for the prebuilt CUDA wheel (there are no
  CUDA wheels for 3.13/3.14 yet). You need a current NVIDIA driver, but **no**
  system CUDA Toolkit - the runtime comes from pip wheels (`nvidia-*-cu12`),
  which the app adds to the DLL search path at startup.
- The CUDA build **defaults GPU layers to 99 on first run** (offloads the whole
  model), so it uses the GPU out of the box. You can change it in **Settings** -
  the value caps to the model's real layer count, and partial offload is only
  needed for models too large for your VRAM. (The CPU build defaults to 0.)
- Rough VRAM use for a 3B Q4 model is ~2.5 GB, so an 8 GB card has plenty of room
  - you can also raise **Context size** for longer chats.
- To force or re-run: `python scripts/setup.py --cuda` (or `--cpu`). Verify with
  `python -c "from src import engine; import llama_cpp; print(llama_cpp.llama_supports_gpu_offload())"`
  - it should print `True`.
- On a newer Python where no CUDA wheel exists, either use 3.11/3.12 or build
  `llama-cpp-python` from source with the CUDA Toolkit (`-DGGML_CUDA=on`).

## Data

Everything is stored under `data/` (git-ignored): `config.json`, `mocca.db`
(sessions), `models/` (downloads), `files/` (documents the file tools can read),
and `logs/mocca.log`.

## Build a Windows app (optional)

Package Mocca into a double-clickable Windows app (no Python needed by the
recipient) with PyInstaller. Requires **Python 3.12** installed (`py -3.12`).
From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1            # both variants
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant cpu
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant cuda
```

This produces one-folder apps under `packaging\windows\dist`:

| Folder        | For                | Notes                                            |
|---------------|--------------------|--------------------------------------------------|
| `Mocca`       | Any Windows PC     | CPU build. Smaller.                              |
| `Mocca-CUDA`  | NVIDIA GPU + driver| Bundles the CUDA runtime, so it's much larger (~1.5 GB). |

Zip the folder you want and share it. Each variant builds in its own throwaway
venv (`.venv-build-cpu` / `.venv-build-cuda`) so your development `.venv` is left
alone; the build also pulls in `pyinstaller`, `pystray`, and `pillow`. The
CUDA-build recipient still needs an up-to-date NVIDIA driver, but no CUDA Toolkit
(the runtime DLLs are bundled). The CPU build runs anywhere.

## Releases & versioning

The version lives in one place - the top-level `VERSION` file - which the app and
the build both read. Releases are **tag-driven**: pushing a `vX.Y.Z` tag runs the
GitHub Actions workflow ([.github/workflows/release.yml](.github/workflows/release.yml)),
which builds the Windows CPU and CUDA apps and publishes them as a GitHub Release
(the CUDA app builds fine on a GPU-less runner - a GPU is only needed to *run* it).

Use the helper to bump and release:

```bash
python scripts/bump_version.py patch              # 0.0.1 -> 0.0.2 (also: minor, major, or X.Y.Z)
python scripts/bump_version.py patch --tag        # ...and git commit + tag vX.Y.Z
python scripts/bump_version.py patch --tag --push # ...and push -> builds + publishes the release
```

For the very first release at the current version, just tag it:
`git tag v0.0.1 && git push --follow-tags`. You can also trigger the workflow
manually (Actions tab) to produce build artifacts without publishing a release.

## More

Asset credits are in [ATTRIBUTIONS.md](ATTRIBUTIONS.md).
