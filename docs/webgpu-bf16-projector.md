# WebGPU vision BF16 compatibility workaround

The pinned WebGPU backend does not accept Brain Floating Point 16 (BF16) weights
for matrix multiplication. A vision graph with those weights can silently assign
its largest projections to the single-threaded CPU backend while other nodes use
WebGPU. The profile name alone does not establish where a node is executed.

## Scope

WebGPU builds enable `LCB_WEBGPU_BF16_PROJECTOR`. The mtmd vision loader expands
BF16 weights to 32-bit floating point (F32) when its selected backend is WebGPU.
It does not rewrite the model file, change language-model weights, convert audio
weights, change non-BF16 types, or affect CPU-only profiles. This is not a general
WebGPU BF16 kernel implementation. The choice is limited to the pinned backend;
review or remove it when upstream BF16 support changes.

The vendor submodule is never edited. `scripts/prepare_mtmd.py` copies `clip.cpp`
into each build tree and applies `upstream-patches-only-as-a-last-resort-with-explicit-user-approval/mtmd-webgpu-bf16.patch` there. CMake
replaces that one mtmd translation unit. The helper is `bridge/mtmd-bf16.h`.
Configure fails if the patch or upstream target source layout does not match.
The small allocation-error guard included in the patch is already present in
upstream reference commit `ec91ab5add06555970f98d9c5361d884f3f530f8`; the other
changes are local to this workaround.

## Why this lives in the loader

Changing WebGPU's `supports_op()` alone would only claim BF16 kernel support
that the pinned implementation does not provide. Expanding at load time lets
the existing graph see F32 types, strides and sizes before allocation and
scheduling, without a per-image conversion or a rewritten model file. Original
GGUF tensor metadata is retained separately for file offsets and progress.

The build-tree copy avoids changing the pinned vendor checkout or duplicating
the whole mtmd target. A failed configure must stop the build rather than
silently omit the workaround or reuse an older patched translation unit.

## Upstream update / removal criteria

A patch that still applies is not proof that this workaround is still needed.
When changing the pinned llama.cpp commit, check BF16 input acceptance and the
corresponding kernel implementation for the vision graph's operations/shapes.
Compare actual assignments, memory, speed and outputs with the workaround
disabled before keeping its extra resident memory cost. Native BF16 support
is a reason to review removal, not to keep converting unconditionally.

When retiring it, remove the overlay hook, preparation script, embedded patch,
conversion helper and profile flag together, and update their tests/docs.
Coordinate changes to the `lcb_clip:` records with the host parser; no emitted
conversion record does not mean a successful load with zero converted tensors.

## Memory, precision and failure behavior

The destination allocation is F32, but file offsets, reads and loading progress
use original tensor sizes. Converted weights occupy twice their BF16 storage
size in resident weight buffers. Total application memory is not necessarily
doubled. A reusable buffer pair consumes at most 1.5 MiB of conversion scratch
space (0.5 MiB BF16 input and 1 MiB F32 output), excluding allocator overhead.
There is no additional full-size F32 temporary tensor.

Finite BF16 values are exactly representable in F32. This is a storage expansion,
not a claim of all-F32 inference: the existing WebGPU matmul kernels can still use
F16 intermediate storage. Numerical results need not be bit-identical to CPU
execution. Actual image recognition quality and speed require model/browser
validation. Large weights may still exceed device buffer limits or cause memory
pressure after expansion; F32 eligibility alone is not a GPU-placement guarantee.

Truncated input is rejected before uploading an incomplete chunk. Address-space
overflow is rejected, upload failures propagate, and initial load cancellation
is honored. A failed graph allocation returns failure instead of using an
unallocated graph. This does not add cooperative cancellation inside native
kernels or change the host's worker-termination strategy.

## Diagnostics

Two fixed, metadata-only native records are emitted for WebGPU vision graphs:

```text
lcb_clip: bf16-f32 tensors=<count> source_bytes=<original> destination_bytes=<expanded>
lcb_clip: matmul placement cpu=<count> webgpu=<count> other=<count> cpu_bf16=<count>
```

The first record is emitted after a successful weight load, including zero
counts when no BF16 weights were present. It is not emitted on `no_alloc` or
failed loads. The second reports actual scheduler assignments of `MUL_MAT`
nodes after graph allocation, not estimated GPU eligibility or elapsed kernel
time. Other operation kinds are not counted. It adds one metadata scan and one
log record per image graph, with no GPU synchronization or tensor readback.
Neither record contains model paths, tensor names, prompts or tensor values.

For performance measurements, disable host per-node tracing. Asking for each
node's completion forces extra synchronization, independent of these summaries.

## Local checks

```sh
python3 -m unittest discover -s tests -p test_mtmd_overlay.py -v
cmake -S tests/mtmd-bf16 -B build/mtmd-bf16-tests -DLCB_LLAMA_SOURCE="$PWD/vendor/llama.cpp"
cmake --build build/mtmd-bf16-tests --target mtmd-bf16-test --parallel 2
build/mtmd-bf16-tests/mtmd-bf16-test
```

`LCB_TEST_LLAMA_SOURCE` can point the overlay test at a separately checked-out
copy of the pinned source. That test checks application, source immutability and
C++ syntax when clang++ is installed. The native conversion test links the real
GGML conversion routine and checks bit patterns, chunk boundaries, offsets,
truncation, overflow and upload errors. It does not emulate GPU performance.
`tests/mtmd-overlay/CMakeLists.txt` can additionally configure the real native
mtmd target with the overlay; compiling its `clip.cpp` object checks the CMake
source replacement without building a complete runtime.

Normal `scripts/build.py` explicitly enables this workaround for WebGPU and
disables it for CPU profiles, recording the option in `cmakeCommand` provenance.
For a manual comparison build, use a separate build directory and configure
`-DLCB_WEBGPU_BF16_PROJECTOR=OFF`; invoking the normal build script again restores
the profile default. Do not confuse a no-workaround build with the normal one.

Backend detection uses the `WebGPU` registry identity, not the display name,
which includes an adapter suffix. Host tests exercise that distinction without
initializing a graphics device.
