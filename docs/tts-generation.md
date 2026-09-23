# Audio generation: bounded decoding and native language selection

This overlay is separate from `mtmd-audio-single-thread.patch`. The latter avoids
constructing preprocessing threads in builds that do not support them. It does
not disable GPU inference. Qwen without reference audio does not need that
speaker-preprocessing fix; Parakeet uses another covered preprocessing loop.

## Complete-output latency, not streaming playback

Upstream's Qwen helper flushes code2wav every 72 frames. Its final flush used to
pad even a short remainder to 72, execute the padded neural graph, then discard
the extra samples. The overlay builds the same causal graph with the actual
number of input frames (1 through the existing attention window). The attention
window, weights, sampling, sample rate, and intended output length are unchanged.
A full 72-frame block is not made smaller. This avoids unnecessary final-frame
computation; it is not a promised browser speedup or a streaming player.

The graph and input layout must change together. A copied `models.h` is also used
for the precompiled header and every mtmd translation unit, so no original and
modified C++ class layouts are mixed. All C++ sources/headers in tools/mtmd are
mirrored into the build tree; only five files are patched. No vendor source is
edited. Unknown source layout and patch conflicts fail configuration. Short
patch contexts permit blank-line-only formatting differences; every preimage is
required to match exactly once before application. Missing or ambiguous code is
a hard error, not a nearest-line guess.

The WebGPU BF16 vision overlay is applied to the TTS copy of clip.cpp, preserving
both changes. The audio preprocessing overlay remains independent. Provenance
records the intermediate hashes, overlay ordering, and final vision copy.

## Language capability

`mtmd_helper_gen_audio_supports_language_auto(ctx)` returns true only for a helper
that can construct a no-forced-language prefix with its loaded vocabulary. It
returns false for null, unsupported pipelines, and old vocabularies. The input
structure is unchanged. For a supporting helper, `lang="auto"` uses Qwen's
`codec_nothink`, `codec_think_bos`, `codec_think_eos` prefix, without a forced
language embedding. Explicit language IDs and the default-English input remain
unchanged. An unsupported `auto` is an error, never an English fallback.

The application must detect this query in the matching generated ABI schema,
then call it for the loaded helper. Adding a UI option alone does not update an
installed WebAssembly artifact. This is model-native language conditioning, not
a language-detection API returning a recognized language code.

## Instructions and model variants

This change does not add `instruct`, preset speaker, VoiceDesign, or CustomVoice
support. Base uses the existing Base pipeline. Instructions must never be
prepended to spoken text as an imitation of separate model conditioning.

## Tests

Run the standard Python tests after initializing the pinned submodule. Set
`LCB_TEST_LLAMA_SOURCE` only to test an alternate revision explicitly. Optional
`LCB_TEST_NATIVE_BUILD` points to a native CPU build of this modified core; it
enables actual decoder-graph and helper-prefix regression executables. These use
deterministic tiny weights / a controlled vocabulary, not downloaded trained
models. Short cold/warm blocks and contiguous state carry are compared with a
padded full-window reference. The test asserts that changing codes changes the
waveform. Compiler failures and numerical mismatches fail the tests.

Actual browser completion time, trained-model quality, WebGPU placement, and
Wasm build/runtime compatibility require release-artifact and device tests.
