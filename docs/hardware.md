# Hardware and local inference

Hugmunn does not require CUDA. Cloud APIs need no local inference runtime or GPU.
For local models, choose a backend in Welcome and setup, then open **hugmunn →
Set up llama-server…**. Select the same backend and download or build a runtime.

| Backend | Hardware and prerequisites | Setup |
| --- | --- | --- |
| CPU | 64-bit CPU and enough RAM | Download a CPU runtime; no GPU driver needed |
| CUDA | Compatible NVIDIA GPU and driver | Prebuilt runtime or source build with CUDA toolkit/nvcc |
| Metal | Apple Silicon; compatible macOS | Download macOS runtime or build with Xcode command-line tools |
| Vulkan | Compatible AMD, Intel or NVIDIA GPU and Vulkan driver | Download Vulkan runtime; source builds also need Vulkan headers and `glslc` |
| ROCm / SYCL | Supported AMD / Intel hardware and vendor runtime | Build using upstream instructions, then browse to the existing llama-server |

Automatic **source builds** prefer Apple Metal, available CUDA, then Vulkan when
`vulkaninfo` is installed, otherwise CPU. CMake reports missing Vulkan build
prerequisites. Automatic **downloads** select CPU on Linux/Windows and the macOS
runtime on macOS; explicitly choose Vulkan or CUDA for GPU downloads elsewhere.
CPU mode forces zero GPU layers even if the selected binary supports a GPU.

Explicit backend choices launch the selected executable directly, so a previous
machine's shell script cannot override the CPU/GPU choice. Non-CUDA modes use
portable f16 KV caches and automatic flash attention; CUDA retains q8 caches.
The runtime's actual device support still depends on its build and drivers.

The installer selects current official archives, handles tar.gz and zip layouts,
keeps shared libraries beside the executable, and checks GitHub's SHA-256 digest
when the release asset supplies one. Downloads and source builds remain optional.
No runtime or model weights are included in Hugmunn's installers.

## Memory and smaller machines

Start with **Qwen3.5-0.8B · small CPU starter** (0.53 GB download). It is intended
for basic text chat; tools are disabled for this entry. Its default context is
4,096 tokens, so disable unnecessary skills if the context meter is full.
The larger Gemma 12B and Qwen 27B entries need substantially more memory.

The [README requirements](https://github.com/EinarOlafsson/hugmunn#system-requirements) are planning estimates.
Model weights are only part of the total: the OS, KV cache, prompt batches and
other applications also need memory. Free disk space is not available RAM.
Hugmunn's first-run hardware inventory stays on the machine and is not uploaded.
Unknown hardware is reported as unknown, rather than treated as zero memory.

## Verification and limits

Linux CPU inference was verified with the downloaded b11139 runtime and the
Qwen3.5-0.8B Q4 model, with `--n-gpu-layers 0`, through Hugmunn’s server/client. Backend
selection and archive extraction have automated tests, and desktop builds are
checked on Linux, macOS and Windows. Metal, AMD and Intel GPU inference have not
been benchmarked on physical devices in this change.

For additional backends and driver-specific requirements, follow the
[official llama.cpp build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md).
Qt's [supported platforms](https://doc.qt.io/qt-6/supported-platforms.html) define
the desktop dependencies; Python wheels may have additional OS/architecture limits.
