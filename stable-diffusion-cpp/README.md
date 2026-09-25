# stable-diffusion.cpp browser core (experimental)

An independently pinned image runtime in Browser Inference Core. It shares the
compiler and publication machinery, not llama-cpp's model implementation or
upstream patch policy. WebAssembly size optimization is deliberately separate
from functional browser bring-up.

## Boundary: native capability, application policy

ABI 2 is generated from the pinned `include/stable-diffusion.h`: all 63 public
functions, 18 public records and 140 constants (including filesystem/error helpers) are exposed. Native
pointers and 64-bit integers are represented as `bigint` on both Wasm widths.
Record sizes, offsets and constants come from the compiled binary. A SHA-256
schema fingerprint prevents mismatching JavaScript helpers and Wasm layouts.

There is **no** `sdb_load(JSON)`/`sdb_generate(JSON)` dispatcher, singleton model,
fixed model directory, preset sampler, resolution limit, memory budget or model
specific configuration in the bridge. Upstream parameter initialization retains
upstream defaults. This is a breaking replacement of the experimental ABI 1;
old artifacts must not be relabeled as ABI 2.

The application owns model selection, file acquisition/storage, mounting paths,
profile selection, resource budgets, sampler/scheduler, generation settings,
context lifetime, callbacks, image encoding, Worker protocol and cancellation.
The raw upstream API and normalized `sdc_*` calls are available; optional helpers
in `examples/runtime` add typed record access and serialization, not policy.
See [host contract](examples/runtime/README.md).

For example, the caller initializes `sd_ctx_params_t`, fills its chosen file
paths, calls `new_sd_ctx`, initializes/fills `sd_img_gen_params_t`, then calls
`generate_image`. Returned image data must be copied before `free_sd_images`.
The caller frees the context before releasing its disk-backed file sources.
Callback registrations are upstream module-global state. Callbacks must not
throw or re-enter a suspended native operation. The application must unregister
callbacks before removing their function-table entries.

## Repository-native model files

`mountReadOnlyFile(core, path, {size, read(destination, offset)})` accepts caller-
owned synchronous random access. No Blob, OPFS, download, persistence, locking,
or ownership policy is built into it. The source receives safe JavaScript
integer offsets, not truncated 32-bit integers. Reads are chunked; whole files
are not copied into JavaScript or Wasm memory. File names have no special policy
in the core. The consumer controls which complete files and relative paths it mounts.

The image-specific upstream patch keeps file positions in `uint64_t`, and parses
GGUF v2/v3 metadata without accumulating the complete tensor payload in `size_t`.
It validates dimensions, type/block sizes, alignment, arrays and file bounds,
and preserves the upstream extended-GGUF higher-rank flattening with checked
arithmetic. Tensor bytes remain lazily read. The generic reader rejects mmap;
applications using it must set `enable_mmap=false` and avoid prefetch threads.

**File size is not resident memory usage.** A file larger than 4 GiB may work in
Wasm32, but individual tensors, staging buffers and actual live allocations still
have that runtime's limits. Wasm64 does not remove GPU buffer or physical-memory
limits. Several component files (diffusion/text encoder/VAE) are not GGUF shards;
each can be a complete unsplit GGUF. Unsupported quantization or architecture
remains unsupported even when its file is readable.

### Safetensors and already-sharded repositories

`_sdc_model_io_capabilities()` is an additive ABI 2 capability query. Bit 0
means 64-bit safetensors file positions with bounded metadata validation; bit 1
means complete standard GGUF shard-group loading. This build returns `3`. A
consumer must not infer these capabilities from ABI 2 alone: older ABI 2 builds
did not contain these loader changes. The image manifest also records
`safetensorsFileOffsetBits: 64` and `ggufShards: true`.

Single safetensors files retain their original bytes, dtype and file size.
Metadata is bounded at 100 MiB; positions and aggregate payload size use 64-bit
integers, while each individual decoded tensor still must fit the address space.
Duplicate keys, invalid ranges, holes/overlaps and unsafe index references are
rejected. Index files are limited to 16 MiB and reference only local safetensors
siblings; nested indices, traversal and duplicate shard tensors are rejected.

Standard GGUF shards are resolved using their original five-digit filenames and
`split.no`, `split.count`, `split.tensors.count`. All members and tensor identities
are validated before the group is accepted. No file is split, concatenated or
converted. This is a loader capability, not repository discovery: the application
must mount every required sibling before passing the chosen component path.

The upstream quantization/backend restrictions still apply (in particular,
INT8 tensorwise/convrot is not enabled by supporting the safetensors container).
A missing model component or unsupported operation is not repaired by changing
file formats. Model-family detection, compatible-candidate selection, optional
features and presets remain application responsibilities.

## Build and distribution

```sh
python3 scripts/setup_toolchain.py
. .tools/emsdk/emsdk_env.sh
python3 stable-diffusion-cpp/scripts/build.py --profile webgpu-wasm32-jspi --variant browser
```

Initialize submodules first. Profiles are `webgpu-wasm32-asyncify`,
`webgpu-wasm32-jspi`, and `webgpu-wasm64-jspi`, each with separate `browser` and
`test` variants. They are single-threaded. Application-created Workers remain
possible and are not the same thing as native pthreads.

Shared pins are in repository-root `toolchain/config.json`. Image source pins
are in `config/upstreams.json`. The independent `vendor/ggml-webgpu-source`
checkout contributes **only** `ggml/`; stable-diffusion.cpp's nested ggml is not
linked. `upstream-patches/series.json` records exact patch inputs/outputs. Changes
to these pins must be deliberate, not inherited from llama-cpp updates.

Compile/packaging jobs are parallel to llama's jobs; final aggregate publication
waits for both. Toolchain and license transfer is independent. See the shared
[cache policy](../toolchain/README.md). The inner image manifest is format 2,
ABI 2, with schema fingerprint and large-file capabilities; the parent remains
format 3. Existing llama imports are unchanged.

## Verification scope

Native checks use real generated bindings, sparse unsplit GGUF files whose last
tensor starts beyond 2/4/8 GiB, actual C++ file reads, malformed metadata, callback
registration and numerical comparison of patched normalization to upstream CPU.
They also read sparse safetensors beyond 20 GiB and validate complete/missing/
duplicate GGUF groups and local safetensors indices.
Synthetic tensors are **not** a trained model or an image-generation benchmark.

CI browser smoke uses real modules in dedicated Workers for all six variants:
parameter round trips (including >2^53 seeds), caller-owned file reads, native
GGUF indexing/reading across 4/8 GiB in test builds, and log/progress registration,
notification and unregistration. Additional test-variant probes exercise
safetensors beyond 20 GiB and complete/missing GGUF shard groups. Test-only probes are absent from browser
artifacts. The fixture never allocates a multi-gigabyte JavaScript array.

These tests do not request a GPU device or prove real-model image inference.
`validation.realModelInference` stays false. Model/operator/dispatch limits,
precision, peak memory, repeated generations and performance require actual
browser/model validation. Exposed APIs are not claims that every feature has
been verified. Initial application trials should use a small model and output.
