# Mocca

A simple, standalone local AI chat app. Download any GGUF model and chat with
it - everything runs on your machine. No accounts, no cloud, no telemetry. The
assistant can also search the web when you ask it something current (a single
toggle turns this off to stay fully offline).

![Mocca - the chat UI with the monochrome dark theme](https://i.imgur.com/fSRgBVR.png)

## Quick start

> **Python 3.11 or 3.12** gives the widest prebuilt-wheel coverage, including the
> NVIDIA CUDA build. Newer Python (3.13/3.14) has no prebuilt CUDA wheel, but the
> **Vulkan** GPU build is a `py3-none` wheel that installs on any Python 3.x - so
> a GPU still works there (and on AMD/Intel).

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1   |   Arch/Linux: source .venv/bin/activate
python scripts/setup.py     # installs deps; auto-picks the best GPU build (NVIDIA/AMD/Intel)
python scripts/run.py
```

`scripts/setup.py` installs the dependencies and auto-picks the engine build for
your hardware: **CUDA** for an NVIDIA GPU (on Python 3.10-3.12), the vendor-neutral
**Vulkan** build for an **AMD** or **Intel** GPU (or NVIDIA on a newer Python), or
the **CPU** build when there's no GPU. Force a choice with `--cuda` / `--vulkan` /
`--cpu`. See [GPU acceleration](#gpu-acceleration) below. (Plain
`pip install -r requirements.txt` still works for a CPU-only setup.)

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

## GPU acceleration

Running models on a GPU is far faster than CPU. `python scripts/setup.py` sets
this up automatically by detecting your GPU vendor and installing the matching
prebuilt `llama-cpp-python` wheel - **NVIDIA, AMD, and Intel** are all supported:

| Your GPU | Build picked | Notes |
| --- | --- | --- |
| NVIDIA (Python 3.10-3.12) | **CUDA** | Fastest. Needs a current NVIDIA driver, but **no** CUDA Toolkit - the runtime comes from pip wheels (`nvidia-*-cu12`), added to the DLL search path at startup. |
| AMD or Intel (any) <br> NVIDIA (Python 3.13/3.14) | **Vulkan** | Vendor-neutral. Needs only an up-to-date GPU driver (the Vulkan loader, `vulkan-1.dll`, ships with it). The wheel is `py3-none`, so it installs on any Python 3.x. |
| none | **CPU** | Runs anywhere. |

- Either GPU build **defaults GPU layers to 99 on first run** (offloads the whole
  model), so it uses the GPU out of the box. Change it in **Settings** - the value
  caps to the model's real layer count, and partial offload is only needed for
  models too large for your VRAM. (The CPU build defaults to 0.)
- Rough VRAM use for a 3B Q4 model is ~2.5 GB, so an 8 GB card has plenty of room
  - you can also raise **Context size** for longer chats.
- To force or re-run: `python scripts/setup.py --cuda` / `--vulkan` / `--cpu`.
  Verify with
  `python -c "from src import engine; import llama_cpp; print(llama_cpp.llama_supports_gpu_offload())"`
  - it should print `True`.
- CUDA is the fastest on NVIDIA, but Vulkan works there too (and is the only
  prebuilt GPU option on Python 3.13/3.14). Force it with `--vulkan` if you'd
  rather not install the CUDA runtime wheels.

## Data

Everything is stored under `data/` (git-ignored): `config.json`, `mocca.db`
(sessions), `models/` (downloads), `files/` (documents the file tools can read),
and `logs/mocca.log`.

## Build a Windows app (optional)

Package Mocca into a double-clickable Windows app (no Python needed by the
recipient) with PyInstaller. Requires **Python 3.12** installed (`py -3.12`).
From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1            # cpu + cuda
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant cpu
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant cuda
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant vulkan
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Variant all     # all three
```

This produces one-folder apps under `packaging\windows\dist`:

| Folder         | For                  | Notes                                            |
|----------------|----------------------|--------------------------------------------------|
| `Mocca`        | Any Windows PC       | CPU build. Smaller.                              |
| `Mocca-CUDA`   | NVIDIA GPU + driver  | Bundles the CUDA runtime, so it's much larger (~1.5 GB). |
| `Mocca-Vulkan` | NVIDIA/AMD/Intel GPU | Runs on any vendor's GPU; needs only an up-to-date GPU driver (the Vulkan loader ships with it). No bundled runtime, so it stays small. |

Zip the folder you want and share it. Each variant builds in its own throwaway
venv (`.venv-build-<variant>`) so your development `.venv` is left alone; the
build also pulls in `pyinstaller`, `pystray`, and `pillow`. The CUDA-build
recipient needs an up-to-date NVIDIA driver (but no CUDA Toolkit - the runtime
DLLs are bundled); the Vulkan-build recipient needs an up-to-date NVIDIA/AMD/Intel
driver. The CPU build runs anywhere.

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
