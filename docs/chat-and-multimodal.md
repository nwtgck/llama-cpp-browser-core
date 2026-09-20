# Native chat and multimodal bindings

This repository separates the heavy Wasm build and artifact publication from
application builds. It exposes upstream operations; applications own history, media input,
workers, generation, sampling, stopping, approval, tool execution, and cancellation.
Jinja and model-specific output parsing stay in upstream llama.cpp.

## Generated module

The Emscripten module works independently of the always-shipped `examples/runtime/`.
Applications can use that tested example to implement their own TypeScript host code:

```js
import createNative from './profiles/cpu-wasm32/core.mjs';
const native = await createNative();
const inputs = new native.common_chat_templates_inputs();
const messagesJSON = native.common_json.parse('[{"role":"user","content":"Hello"}]');
const messages = native.common_chat_msgs_parse_oaicompat(messagesJSON);
inputs.messages = messages;
// Create native.common_chat_templates(modelPointer, templateOverride, bos, eos),
// then call templates.apply(inputs). Release every owned Embind object.
messages.delete();
messagesJSON.delete();
inputs.delete();
```

Emscripten's `--emit-tsd` writes each profile's `core.d.ts` alongside `core.mjs`;
the package's profile import resolves those generated types. Embind registers the upstream message, tool, input, result, diff, delimiter,
parser-parameter, and PEG-arena types, plus their vectors, maps, and enums.
`common_json.parse()` / `dump(-1)` and the upstream OpenAI-compatible helpers
handle JSON. There is no lcore JSON field mapper or alternate message schema.
Constructors retain upstream defaults, including empty message/tool vectors.
`chat_template_kwargs` is the upstream string map: its values are serialized JSON.
Clock duration/time-point types expose `now` without a custom timestamp format;
`system_clock_period_num()` / `system_clock_period_den()` return exact integer
duration ratios. Upstream Jinja currently
recomputes its clock and overrides `add_bos`/`add_eos`.

`common_chat_templates` is a small owner for upstream's opaque, custom-deleter
resource. Its methods directly call upstream apply/source/capability/formatting
operations. `delete()` invokes upstream cleanup. Supply every bound argument;
C++ default arguments do not become JavaScript defaults. Its model argument is a checked
`bigint` pointer from the C API; string overrides are ordinary JS strings.

Embind owns returned value objects and STL containers; call `delete()` on them.
Class-valued properties and vector elements use standard copy behavior: modifying
a retrieved copy does not update its parent until assigned back. String conversion
uses Embind's standard allocation handling. Enum values are standard Embind enum
objects, not lcore integers or booleans. Size-based values follow the selected ABI;
compare absent diff indices with `common_chat_no_tool_call_index()`, which returns
the native `size_t` sentinel in the selected ABI's integer representation.

Keep a `common_peg_arena` and call `load()` once, then reuse it with
`common_chat_peg_parse(arena, cumulativeText, isPartial, parserParams)`. This passes
the existing native arena by const reference. Assigning `parserParams.parser`
copies it once; subsequent `common_chat_parse()` calls reuse that member. The caller
controls the cumulative text and previous parsed message. Full apply results expose
grammar/lazy triggers, generation prefixes, stops, preserved tokens, reasoning
metadata, serialized parsers, and delimiters; retain the fields required by the loop.

JSON-schema grammar conversion and reasoning-budget sampler primitives are also
bound. Sampler pointers use the C API's destructor or transfer to a sampler chain.
`common_reasoning_budget_get_end_match_copy()` returns an owned token-vector copy,
empty when no end marker has matched. Call `.delete()` on this copy after use. It
remains valid after sampler mutation or release; changing it does not alter the
sampler. The old borrowed-reference getter is not exported. No reasoning policy or
generation loop is added by these bindings.

Native chat calls are synchronous and may throw. Raw C calls retain their own
signatures and profile-specific suspension behavior. For Asyncify, use
`ccall(..., { async: true })` to wait for completion; awaiting a raw export does
not wait for a suspended operation. The example loader configures this automatically.
Applications must sequence all access to one instance,
including Embind calls during pending GPU operations. The optional reference
`core.api` Promise wrapper and its busy guard apply only to calls through that wrapper.

## Multimodal C API and validation

The generator includes non-deprecated `mtmd.h` / `mtmd-helper.h` C declarations,
records, and constants for mmproj loading, capability queries, RGB/PCM and encoded
file input, chunks, batching, encoding, decoder positions, and audio generation.
Large media buffers remain in linear memory through C APIs. Follow the pinned
headers for borrowed buffer sizes/lifetimes; chunk save/load preserves placeholder
metadata, not encodable media data. OpenAI message helpers accept text/media markers,
not image URLs or audio payloads; upstream serialization may concatenate media parts.

Subprocess video APIs are excluded (`MTMD_VIDEO=OFF`); applications can supply RGB frames.
Profiles remain single-threaded. Some upstream audio paths spawn threads, including
fixed four-thread Parakeet preprocessing, so `n_threads=1` is not a general remedy.
Audio generation is experimental upstream. Exposed APIs do not certify model support.

Actions checks four profile builds, standard type generation, package/schema consistency and size limits,
then exercises direct generated-module chat primitives and RGB/PCM allocation in
CPU Chromium. Trained tool-model inference, image/audio mmproj inference, and WebGPU
inference remain separate validations. Native host smoke covers C bindings only.
