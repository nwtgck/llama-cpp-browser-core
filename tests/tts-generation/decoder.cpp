// Execute upstream's actual code2wav graph with deterministic, tiny random
// weights. This validates causal short-window equivalence, not trained quality.
#include "models.h"
#include "ggml-cpu.h"
#include <algorithm>
#include <cstdio>
#include <stdexcept>

struct clip_ctx { clip_model model; };
// Only the graph allocation shell is a fixture. All decoder operations, state
// slots, convolution/attention code and CPU kernels are the real source code.
clip_graph::clip_graph(clip_ctx * ctx, const clip_image_f32 & image)
    : model(ctx->model), hparams(model.hparams), proj_type(model.proj_type), img(image),
      patch_size(1), n_patches_x(1), n_patches_y(1), n_patches(1),
      n_embd(hparams.n_embd), n_head(1), n_head_kv(1), d_head(4), n_layer(1),
      n_mmproj_embd(4), eps(1e-5f), kq_scale(.5f), flash_attn_type(CLIP_FLASH_ATTN_TYPE_DISABLED) {
    ctx0_ptr.reset(ggml_init({512 * 1024 * 1024, nullptr, false}));
    ctx0 = ctx0_ptr.get(); gf = ggml_new_graph_custom(ctx0, 8192, false);
}
clip_graph::clip_graph(const clip_graph & p)
    : model(p.model), hparams(p.hparams), proj_type(p.proj_type), img(p.img),
      patch_size(p.patch_size), n_patches_x(p.n_patches_x), n_patches_y(p.n_patches_y), n_patches(p.n_patches),
      n_embd(p.n_embd), n_head(p.n_head), n_head_kv(p.n_head_kv), d_head(p.d_head), n_layer(p.n_layer),
      n_mmproj_embd(p.n_mmproj_embd), eps(p.eps), kq_scale(p.kq_scale), flash_attn_type(p.flash_attn_type),
      ctx0(p.ctx0), gf(p.gf) {}
ggml_tensor * clip_graph::build_mm(ggml_tensor * w, ggml_tensor * x) const { return ggml_mul_mat(ctx0, w, x); }
void clip_graph::cb(ggml_tensor * t, const char * name, int layer) const { ggml_format_name(t, "%s_%d", name, layer); }
struct shell : clip_graph {
    using clip_graph::clip_graph;
    ggml_cgraph * build() override { return gf; }
};
using States = std::map<std::string, std::vector<float>>;
struct Result { std::vector<float> audio; States state; };
struct Fixture {
    ggml_context_ptr weights{ggml_init({16 * 1024 * 1024, nullptr, false})};
    clip_ctx ctx;
    int serial = 0;
    ggml_tensor * tensor(int64_t a, int64_t b = 1, int64_t c = 1, float fill = NAN) {
        auto * t = ggml_new_tensor_3d(weights.get(), GGML_TYPE_F32, a, b, c);
        auto * data = (float *) t->data;
        for (int64_t i = 0; i < ggml_nelements(t); ++i) data[i] = std::isnan(fill) ? .25f * std::sin(float(++serial) * .37f) : fill;
        return t;
    }
    Fixture() {
        auto & h = ctx.model.hparams;
        h.n_embd = 4; h.wav_tfm_swa = 72; h.wav_tfm_n_layer = 1;
        h.wav_tfm_n_head = h.wav_tfm_n_head_kv = 1;
        h.wav_tfm_eps = 1e-5f; h.wav_tfm_rope_theta = 10000;
        auto & w = ctx.model.c2w;
        w.quant_first_cb_w = tensor(4, 8); w.quant_rest_cb_w = tensor(4, 8, 15);
        w.quant_first_out_w = tensor(4, 4); w.quant_rest_out_w = tensor(4, 4);
        w.pre_conv_w = tensor(3, 4, 4); w.pre_conv_b = tensor(4);
        w.tfm_in_proj_w = tensor(4, 4); w.tfm_in_proj_b = tensor(4);
        w.tfm_out_proj_w = tensor(4, 4); w.tfm_out_proj_b = tensor(4);
        w.tfm_output_norm_w = tensor(4, 1, 1, 1);
        w.tfm_layers.resize(1); auto & l = w.tfm_layers[0];
        l.q_w = tensor(4, 4); l.k_w = tensor(4, 4); l.v_w = tensor(4, 4); l.o_w = tensor(4, 4);
        l.ln_1_w = tensor(4, 1, 1, 1); l.ln_2_w = tensor(4, 1, 1, 1);
        l.ff_gate_w = tensor(4, 8); l.ff_up_w = tensor(4, 8); l.ff_down_w = tensor(8, 4);
        w.upsample.resize(2);
        for (auto & u : w.upsample) {
            u.conv_w = tensor(2, 4, 4); u.conv_b = tensor(4);
            u.dwconv_w = tensor(7, 1, 4); u.dwconv_b = tensor(4);
            u.norm_w = tensor(4, 1, 1, 1); u.norm_b = tensor(4);
            u.pw1_w = tensor(4, 8); u.pw1_b = tensor(8);
            u.pw2_w = tensor(8, 4); u.pw2_b = tensor(4); u.gamma = tensor(4, 1, 1, .1f);
        }
        w.dac_entry_w = tensor(7, 4, 4); w.dac_entry_b = tensor(4);
        w.dac.resize(4); const int strides[] = {8, 5, 4, 3};
        for (size_t i = 0; i < w.dac.size(); ++i) {
            auto & d = w.dac[i]; d.snake_alpha = tensor(4, 1, 1, 1); d.snake_beta = tensor(4, 1, 1, 1);
            d.conv_w = tensor(2 * strides[i], 4, 4); d.conv_b = tensor(4);
            d.res.resize(3); for (auto & r : d.res) {
                r.act1_alpha = tensor(4, 1, 1, 1); r.act1_beta = tensor(4, 1, 1, 1);
                r.act2_alpha = tensor(4, 1, 1, 1); r.act2_beta = tensor(4, 1, 1, 1);
                r.conv1_w = tensor(7, 4, 4); r.conv1_b = tensor(4);
                r.conv2_w = tensor(1, 4, 4); r.conv2_b = tensor(4);
            }
        }
        w.dac_post_snake_alpha = tensor(4, 1, 1, 1); w.dac_post_snake_beta = tensor(4, 1, 1, 1);
        w.dac_post_conv_w = tensor(7, 4, 1); w.dac_post_conv_b = tensor(1);
    }
    Result run(int frames, int real, int offset, const States & initial) {
        clip_image_f32 image; shell p(&ctx, image);
        clip_graph_qwen3tts_gen::code2wav decoder(p);
        for (const auto & slot : list_c2w_state_slots(ctx.model.hparams, ctx.model)) {
            auto * t = ggml_new_tensor_2d(p.ctx0, GGML_TYPE_F32, slot.ne0, slot.ne1);
            ggml_set_zero(t);
            auto found = initial.find(slot.name);
            if (found != initial.end()) std::copy(found->second.begin(), found->second.end(), (float *) t->data);
            decoder.state_in[slot.name] = t;
        }
        auto * codes = ggml_new_tensor_2d(p.ctx0, GGML_TYPE_I32, frames, 16);
        for (int g = 0; g < 16; ++g) for (int f = 0; f < frames; ++f)
            ((int32_t *) codes->data)[g * frames + f] = f < real ? (f + offset + 3 * g) % 8 : 0;
        auto * audio = decoder.decode(codes);
        ggml_build_forward_expand(p.gf, audio);
        for (const auto & state : decoder.state_out) ggml_build_forward_expand(p.gf, state.second);
        if (ggml_graph_compute_with_ctx(p.ctx0, p.gf, 1) != GGML_STATUS_SUCCESS) throw std::runtime_error("compute failed");
        Result out;
        out.audio.assign((float *) audio->data, (float *) audio->data + ggml_nelements(audio));
        if (out.audio.size() != (size_t) frames * 1920) throw std::runtime_error("wrong output length");
        for (const auto & [name, t] : decoder.state_out) out.state[name].assign((float *) t->data, (float *) t->data + ggml_nelements(t));
        return out;
    }
};
static void compare(const std::vector<float> & a, const std::vector<float> & b) {
    if (b.size() < a.size()) throw std::runtime_error("short reference");
    float diff = 0;
    for (size_t i = 0; i < a.size(); ++i) {
        if (!std::isfinite(a[i]) || !std::isfinite(b[i])) throw std::runtime_error("non-finite sample");
        diff = std::max(diff, std::abs(a[i] - b[i]));
    }
    if (diff > 2e-5f) throw std::runtime_error("causal prefix differs: " + std::to_string(diff));
    std::printf("samples=%zu max_abs_error=%.9g\n", a.size(), diff);
}
int main() {
    Fixture f;
    const auto warm = f.run(72, 72, 0, {});
    const auto other_codes = f.run(72, 72, 1, {});
    float code_effect = 0;
    for (size_t i = 0; i < warm.audio.size(); ++i) code_effect = std::max(code_effect, std::abs(warm.audio[i] - other_codes.audio[i]));
    std::printf("changed-code effect=%.9g\n", code_effect);
    if (code_effect < 1e-4f) throw std::runtime_error("fixture is insensitive to its codes");
    for (bool warmed : {false, true}) for (int count : {1, 2, 10, 36, 71, 72}) {
        auto states = warmed ? warm.state : States{};
        auto padded = f.run(72, count, 72, states);
        auto short_result = f.run(count, count, 72, states);
        compare(short_result.audio, padded.audio);
        if (short_result.state.at("tfm_pos")[0] != (warmed ? 72 : 0) + count) throw std::runtime_error("padding advanced position state");
    }
    std::vector<float> joined; States state;
    int pos = 0;
    for (int count : {10, 20, 42}) {
        auto out = f.run(count, count, pos, state); pos += count; state = out.state;
        joined.insert(joined.end(), out.audio.begin(), out.audio.end());
    }
    compare(joined, warm.audio);
    std::puts("PASS: real CPU decoder graph, cold/warm short tails and continued chunks");
}
