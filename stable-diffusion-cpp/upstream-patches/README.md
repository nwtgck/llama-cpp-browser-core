# Browser/WebGPU enablement patches

The user explicitly authorized the implementing agent to choose all upstream
patches needed for this image-runtime experiment on 2026-09-24. This exception is
local to `stable-diffusion-cpp`; it does not relax llama-cpp's approval policy.

`series.json` is the ordered, exact-input/exact-output inventory. Patches are
applied only to private build-tree copies. Both upstream checkouts remain clean.
The ggml copy comes from an independently pinned llama.cpp checkout **only to use
its ggml directory**; no llama model pipeline is linked into the image runtime.

1. Model reader: eliminate thread spawning in single-threaded Emscripten.
2. Group normalization: use existing NORM for contiguous channel-divisible F32
   shapes, retaining upstream/CPU handling outside that case.
3. WebGPU memory: report unknown capacity, not a single-buffer limit. The bridge
   requires a positive managed GPU budget; allocation can still fail. The budget
   is not a measurement of physical GPU capacity and excludes driver overhead.

Large-dispatch coverage, arbitrary model architectures/quantizations and real
GPU numerical parity are NOT certified by these changes. Initial image requests
are capped at 512x512, with 32-latent-pixel VAE tiles. These conservative bounds
are not a proof that every operator fits every device.

Removal criteria: use upstream equivalents once browser threading, normalization
and memory reporting satisfy the same tests. Never silently skip a failed patch.
