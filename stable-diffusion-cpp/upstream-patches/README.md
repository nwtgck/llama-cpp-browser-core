# Browser/WebGPU enablement patches

The user explicitly authorized the implementing agent to choose upstream patches
for this image-runtime experiment on 2026-09-24. The exception is local to
`stable-diffusion-cpp`; it does not relax llama-cpp's approval policy.

`series.json` is the ordered, exact-input/exact-output inventory. Apply only to
private build-tree copies, leaving both upstream checkouts clean. The independent
llama.cpp checkout is used only as a source for its `ggml/` subtree.

1. Model reader: avoid native thread spawning in single-threaded Emscripten.
2. Group normalization: decompose contiguous channel-divisible F32 shapes into
   existing normalization/reshape operations; retain other upstream paths.
3. WebGPU memory: report unknown device capacity, not a single-buffer ceiling.
   The **application** supplies a managed-memory budget. Allocation can still
   fail; this is not a measurement of free GPU memory or driver overhead.
4. Unsplit GGUF: metadata-only v2/v3 parsing and 64-bit file positions independent
   of Wasm address width. Avoid whole-model `size_t` accumulation, validate file
   ranges/type sizes/counts and keep per-tensor memory limits. Preserve supported
   higher-rank flattening with checked arithmetic. Synchronous random-access
   sources and the decision to disable mmap/prefetch belong to the caller.

Real GPU numerical parity, large dispatches, all architectures and quantizations
are not certified. There is no application resolution cap, sampler selection,
GPU budget or Qwen-specific policy in these patches or the bridge.

Removal criteria: replace each patch with an upstream equivalent when its same
threading/normalization/memory/large-file tests pass. Never silently skip a failed
patch, relax input hashes, or substitute a different upstream revision.
