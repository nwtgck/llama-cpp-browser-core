// Exercise the actual helper prefix builder with a deterministic vocabulary and
// embedding table. No trained model, text classifier, or GPU is simulated here.
#include "mtmd-helper-gen.cpp"
#include <cassert>
#include <iostream>

static std::vector<std::string> pieces = {
    "<|codec_0|>", "<|codec_bos|>", "<|codec_eos_token|>", "<|codec_pad|>",
    "<|codec_think|>", "<|codec_think_bos|>", "<|codec_think_eos|>",
    "<tts_pad>", "<tts_text_bos>", "<tts_text_eod>", "<|codec_nothink|>",
    "<|codec_language_english|>", "<|codec_language_japanese|>", "role0", "role1", "role2", "body"
};
static std::vector<float> decoded;
static mtmd_gen_audio_type pipeline_type = MTMD_GEN_AUDIO_TYPE_QWEN3TTS;
extern "C" {
const llama_model * llama_get_model(const llama_context *) { return reinterpret_cast<const llama_model *>(1); }
const llama_vocab * llama_model_get_vocab(const llama_model *) { return reinterpret_cast<const llama_vocab *>(2); }
int32_t llama_model_n_embd(const llama_model *) { return 4; }
llama_rope_type llama_model_rope_type(const llama_model *) { return LLAMA_ROPE_TYPE_NORM; }
int32_t llama_vocab_n_tokens(const llama_vocab *) { return (int32_t) pieces.size(); }
const char * llama_vocab_get_text(const llama_vocab *, llama_token token) { return pieces.at(token).c_str(); }
} // extern C
uint32_t llama_model_get_tok_embd(const llama_model *, float * out) {
    if (out) for (size_t t = 0; t < pieces.size(); ++t) for (int i = 0; i < 4; ++i) out[t * 4 + i] = float(100 * t + i);
    return (uint32_t) pieces.size() * 4;
}
extern "C" {
int32_t llama_tokenize(const llama_vocab *, const char *, int32_t, llama_token * tokens, int32_t capacity, bool, bool) {
    if (capacity < 9) return -9;
    const llama_token ids[] = {13, 14, 15, 16, 13, 14, 15, 13, 14};
    std::copy(ids, ids + 9, tokens); return 9;
}
int32_t llama_decode(llama_context *, llama_batch batch) {
    decoded.insert(decoded.end(), batch.embd, batch.embd + batch.n_tokens * 4); return 0;
}
mtmd_gen_audio_info mtmd_gen_audio_get_info(const mtmd_context *) { return {pipeline_type, 24000, "base"}; }
mtmd_gen_inp mtmd_gen_inp_default(const mtmd_context *) { mtmd_gen_inp input{}; input.top_k = 50; input.top_p = 1; return input; }
}
static std::vector<float> prompt(const char * lang) {
    auto * helper = mtmd_helper_gen_audio_init(reinterpret_cast<llama_context *>(3), reinterpret_cast<mtmd_context *>(4));
    mtmd_helper_gen_audio_inp input{}; input.prompt = "body"; input.prompt_len = 4; input.lang = lang;
    assert(mtmd_helper_gen_audio_set_input(helper, &input) == 0);
    decoded.clear(); assert(mtmd_helper_gen_audio_step_prompt(helper, 128) == 0);
    mtmd_helper_gen_audio_free(helper); return decoded;
}
static void row(const std::vector<float> & values, int index, int a, int b) {
    for (int i = 0; i < 4; ++i) assert(values.at(index * 4 + i) == float(100 * (a + b) + 2 * i));
}
int main() {
    assert(!mtmd_helper_gen_audio_supports_language_auto(nullptr));
    auto * helper = mtmd_helper_gen_audio_init(reinterpret_cast<llama_context *>(3), reinterpret_cast<mtmd_context *>(4));
    assert(mtmd_helper_gen_audio_supports_language_auto(helper));
    const auto en = prompt("en"), ja = prompt("ja"), automatic = prompt("auto");
    assert(prompt(nullptr) == en); assert(prompt("english") == en);
    assert(en.size() == 44 && ja.size() == 44 && automatic.size() == 40);
    row(en, 3, 7, 4); row(en, 4, 7, 5); row(en, 5, 7, 11); row(en, 6, 7, 6);
    row(ja, 5, 7, 12);
    row(automatic, 3, 7, 10); row(automatic, 4, 7, 5); row(automatic, 5, 7, 6);
    assert(std::equal(en.begin() + 7 * 4, en.end(), automatic.begin() + 6 * 4));
    pieces[10] = "missing auto token";
    assert(!mtmd_helper_gen_audio_supports_language_auto(helper));
    mtmd_helper_gen_audio_inp input{}; input.prompt = "body"; input.prompt_len = 4; input.lang = "auto";
    assert(mtmd_helper_gen_audio_set_input(helper, &input) != 0);
    assert(prompt("ja") == ja);
    mtmd_helper_gen_audio_free(helper);
    pipeline_type = MTMD_GEN_AUDIO_TYPE_POCKETTTS;
    helper = mtmd_helper_gen_audio_init(reinterpret_cast<llama_context *>(3), reinterpret_cast<mtmd_context *>(4));
    assert(!mtmd_helper_gen_audio_supports_language_auto(helper)); mtmd_helper_gen_audio_free(helper);
    std::cout << "PASS: native helper auto/manual/default prefixes, missing-token refusal and capability negotiation\n";
}
