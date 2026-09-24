// The worker owns one instance. No context pointer or model pipeline leaks into
// the application. ABI 1 deliberately supports one image at up to 512x512.
#include "stable-diffusion.h"
#include "json.hpp"
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef __EMSCRIPTEN__
#include <emscripten.h>
EM_JS(void, browser_progress, (int step, int steps, float seconds), {
    Module['sdbDispatchProgress'](step, steps, seconds);
});
EM_JS(void, browser_log, (int level, const char* message), {
    Module['sdbDispatchLog'](level, UTF8ToString(message));
});
#else
static void browser_progress(int, int, float) {}
static void browser_log(int, const char*) {}
#endif

namespace {
std::unique_ptr<sd_ctx_t, decltype(&free_sd_ctx)> context(nullptr, free_sd_ctx);
std::string error;
sd_image_t* images = nullptr;
int image_count = 0;
bool busy = false;
struct Busy {
    Busy() { if (busy) throw std::runtime_error("Concurrent core operation"); busy = true; }
    ~Busy() { busy = false; }
};
void release_images() {
    for (int i = 0; i < image_count; ++i) std::free(images[i].data);
    std::free(images); images = nullptr; image_count = 0;
}
void progress(int step, int steps, float seconds, void*) { browser_progress(step, steps, seconds); }
void log(sd_log_level_t level, const char* text, void*) { browser_log(static_cast<int>(level), text); }
using json = nlohmann::json;
json parse(const char* text) {
    if (!text || std::strlen(text) > 65536) throw std::runtime_error("Invalid request length");
    auto result = json::parse(text);
    if (!result.is_object()) throw std::runtime_error("Request must be an object");
    return result;
}
int integer(const json& value, const char* key, int min, int max) {
    const auto& entry = value.at(key);
    if (!entry.is_number_integer()) throw std::runtime_error(std::string("Expected integer: ") + key);
    const auto number = entry.get<int64_t>();
    if (number < min || number > max) throw std::runtime_error(std::string("Out of range: ") + key);
    return static_cast<int>(number);
}
std::string model_path(const json& value, const char* key) {
    auto path = value.value(key, std::string());
    if (!path.empty() && (path.size() > 4096 || path.find('\0') != std::string::npos ||
        path.find("..") != std::string::npos || path.compare(0, 8, "/models/") != 0))
        throw std::runtime_error("Model path must be inside /models/");
    return path;
}
}

extern "C" {
#ifdef SDCB_TEST_HOOKS
// A deterministic callback-path test, NOT a model or GPU execution result.
void sdb_test_callbacks() {
    browser_progress(1, 4, 0.125f);
    browser_log(static_cast<int>(SD_LOG_INFO), "browser callback probe");
}
#endif
int sdb_abi_version() { return 1; }
const char* sdb_error() { return error.c_str(); }
const char* sdb_model_version() { return context ? sd_get_model_version_name(context.get()) : "Unloaded"; }
int sdb_load(const char* request) {
    try {
        Busy guard; error.clear(); release_images(); context.reset();
        const auto cfg = parse(request);
        const auto model = model_path(cfg, "model");
        const auto diffusion = model_path(cfg, "diffusion");
        const auto vae = model_path(cfg, "vae");
        const auto clip_l = model_path(cfg, "clipL");
        const auto clip_g = model_path(cfg, "clipG");
        const auto t5 = model_path(cfg, "t5");
        const auto llm = model_path(cfg, "llm");
        if (model.empty() == diffusion.empty()) throw std::runtime_error("Choose a checkpoint OR diffusion model");
        const int budget = integer(cfg, "gpuBudgetMiB", 512, 16384);
        const std::string budget_gib = std::to_string(budget / 1024.0);
        sd_ctx_params_t params;
        sd_ctx_params_init(&params);
        params.model_path = model.empty() ? nullptr : model.c_str();
        params.diffusion_model_path = diffusion.empty() ? nullptr : diffusion.c_str();
        params.vae_path = vae.empty() ? nullptr : vae.c_str();
        params.clip_l_path = clip_l.empty() ? nullptr : clip_l.c_str();
        params.clip_g_path = clip_g.empty() ? nullptr : clip_g.c_str();
        params.t5xxl_path = t5.empty() ? nullptr : t5.c_str();
        params.llm_path = llm.empty() ? nullptr : llm.c_str();
        params.n_threads = 1;
        params.enable_mmap = false;
        params.disable_prefetch = true;
        params.eager_load = false;
        params.auto_fit = false;
        params.conditioning_cache_size = 0;
        params.max_vram = budget_gib.c_str();
        params.model_args = "qwen_image_2_1_prefix_cache=false";
        params.params_backend = "disk";
#ifdef __EMSCRIPTEN__
        params.backend = "WebGPU";
#else
        params.backend = "CPU"; // Native boundary/math verification only.
#endif
        // Leave large/fused attention kernels off until real-device parity tests.
        params.flash_attn = false;
        params.diffusion_flash_attn = false;
        sd_set_log_callback(log, nullptr);
        sd_set_progress_callback(progress, nullptr);
        context.reset(new_sd_ctx(&params));
        if (!context) throw std::runtime_error("Model initialization failed; see core diagnostics");
        return 1;
    } catch (const std::exception& failure) { error = failure.what(); }
      catch (...) { error = "Unknown model initialization failure"; }
    browser_log(static_cast<int>(SD_LOG_ERROR), error.c_str());
    return 0;
}
int sdb_generate(const char* request) {
    try {
        Busy guard; error.clear(); release_images();
        if (!context) throw std::runtime_error("Model is not loaded");
        const auto cfg = parse(request);
        const auto prompt = cfg.at("prompt").get<std::string>();
        const auto negative = cfg.at("negativePrompt").get<std::string>();
        if (prompt.empty() || prompt.size() > 16384 || negative.size() > 16384 ||
            prompt.find('\0') != std::string::npos || negative.find('\0') != std::string::npos)
            throw std::runtime_error("Invalid prompt");
        sd_img_gen_params_t params;
        sd_img_gen_params_init(&params);
        params.prompt = prompt.c_str(); params.negative_prompt = negative.c_str();
        params.width = integer(cfg, "width", 128, 512);
        params.height = integer(cfg, "height", 128, 512);
        if (params.width % 64 || params.height % 64) throw std::runtime_error("Dimensions must be multiples of 64");
        params.batch_count = 1;
        params.seed = integer(cfg, "seed", 0, std::numeric_limits<int>::max());
        params.sample_params.sample_steps = integer(cfg, "steps", 1, 100);
        const double guidance = cfg.at("guidance").get<double>();
        if (!std::isfinite(guidance) || guidance < 0 || guidance > 30) throw std::runtime_error("Invalid guidance");
        params.sample_params.guidance.txt_cfg = static_cast<float>(guidance);
        params.sample_params.sample_method = EULER_SAMPLE_METHOD;
        params.sample_params.scheduler = sd_get_default_scheduler(context.get(), EULER_SAMPLE_METHOD);
        params.vae_tiling_params.enabled = true;
        // Upstream tile sizes are LATENT pixels (32 -> 256 decoded SD1.5 pixels).
        params.vae_tiling_params.tile_size_x = 32;
        params.vae_tiling_params.tile_size_y = 32;
        params.vae_tiling_params.target_overlap = 0.25f;
        sd_cancel_generation(context.get(), SD_CANCEL_RESET);
        if (!generate_image(context.get(), &params, &images, &image_count) || image_count != 1 || !images ||
            images[0].width != static_cast<uint32_t>(params.width) || images[0].height != static_cast<uint32_t>(params.height) ||
            images[0].channel != 3 || !images[0].data)
            throw std::runtime_error("Image generation failed or returned an unexpected image");
        return 1;
    } catch (const std::exception& failure) { error = failure.what(); }
      catch (...) { error = "Unknown generation failure"; }
    release_images();
    browser_log(static_cast<int>(SD_LOG_ERROR), error.c_str());
    return 0;
}
const uint8_t* sdb_image_data() { return images ? images[0].data : nullptr; }
int sdb_image_width() { return images ? static_cast<int>(images[0].width) : 0; }
int sdb_image_height() { return images ? static_cast<int>(images[0].height) : 0; }
void sdb_release_image() { if (!busy) release_images(); }
void sdb_unload() { if (!busy) { release_images(); context.reset(); } }
}
