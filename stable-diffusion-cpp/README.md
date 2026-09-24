# Experimental stable-diffusion.cpp browser runtime

This directory owns its source pins, patches, profiles, bridge, tests and build
outputs. It does not change llama-cpp's Wasm, model pipelines or approval policy.

The first target is text-to-image with a self-contained Stable Diffusion 1.5
checkpoint, one image, 128..512 pixel multiples of 64, Euler sampling, and tiled
VAE decoding. Separate component paths are exposed for experiments; their presence
is not a claim that Qwen Image or every upstream architecture fits a browser.

## Build

From repository root initialize submodules recursively. Then:

```sh
python3 llama-cpp/scripts/setup_toolchain.py
. llama-cpp/.tools/emsdk/emsdk_env.sh
python3 stable-diffusion-cpp/scripts/build.py --profile webgpu-wasm32-jspi --variant browser
```

Every profile/variant pair has its own build tree. The compiler/Dawn pins are
currently shared with `llama-cpp/config/toolchain.json`; the image upstreams are
independently pinned in `config/upstreams.json`. `vendor/ggml-webgpu-source` is a
llama.cpp checkout used solely for its `ggml/` subtree. Updating llama-cpp's
upstream does not silently change the image backend. `upstream-patches` has the
explicit user-approved experimental exception and the complete patch inventory.

## ABI 1

The consumer provides `wasmBinary` and mounts local Blobs through `FS/WORKERFS`
under `/models/`. Do not copy whole multi-gigabyte model files into MEMFS. Invoke
`sdb_load` and `sdb_generate` through `ccall(..., {async:true})`, serially. A false
return means read `sdb_error`. Image output is RGB8 and borrowed until
`sdb_release_image`/`sdb_unload`; copy it before releasing. Progress and log
callbacks must not call into the core. A dedicated Worker per operation provides
hard cancellation without re-entering suspended Wasm; terminate it after copying
the result or on cancellation/error. The core performs no model downloads.

Load JSON fields: `model` OR `diffusion`, optional `vae`, `clipL`, `clipG`, `t5`,
`llm`, and required `gpuBudgetMiB` (512..16384). Paths are under `/models/`.
Generate JSON fields: `prompt`, `negativePrompt`, `width`, `height`, `steps`
(1..100), `guidance` (0..30), `seed` (0..2147483647).

The managed-memory budget is NOT available physical GPU memory. Driver/scratch
allocations can exceed it and device allocation may fail. Unsupported operations
may use CPU fallback. Heavy CPU/GPU transfers and performance remain unverified.
Initial profiles use single-threaded wasm32 with JSPI or Asyncify. No file://
support is claimed by this image experiment.

## Verification scope

Native tests exercise the actual bridge's rejection paths and compare the
patched normalization entry point against upstream CPU GROUP_NORM. CI compilation
and module-boundary browser smoke tests do not certify real-model inference.
`validation.realModelInference` remains false until a genuine model run is
recorded. Never replace it with a synthetic success flag.
