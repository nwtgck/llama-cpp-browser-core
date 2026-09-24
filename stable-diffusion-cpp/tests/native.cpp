#include "ggml.h"
#include "ggml-cpu.h"
#include "stable-diffusion.h"
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <vector>
extern "C" uint32_t sdc_abi_version();
extern "C" void sdc_sd_ctx_params_init(uint64_t);
extern "C" void sdc_sd_img_gen_params_init(uint64_t);
extern "C" uint64_t sdc_test_gguf_offset(const char*);
extern "C" uint32_t sdc_test_gguf_value(const char*);
extern "C" void sdc_test_callbacks();
extern "C" void sdc_sd_set_log_callback(uint64_t, uint64_t);
extern "C" void sdc_sd_set_progress_callback(uint64_t, uint64_t);
static int logs_seen=0, progress_seen=0;
static void log_callback(sd_log_level_t, const char* message, void* data) {
    if (std::string(message).find("native callback probe") != std::string::npos && uintptr_t(data)==17) ++logs_seen;
}
static void progress_callback(int step, int steps, float seconds, void* data) {
    if (step==1 && steps==4 && seconds==0.125f && uintptr_t(data)==19) ++progress_seen;
}
ggml_tensor* ggml_ext_group_norm_32(ggml_context*, ggml_tensor*);
static void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
static void u32(std::ofstream& out, uint32_t v) { for (int i=0;i<4;++i) out.put(char(v >> (8*i))); }
static void u64(std::ofstream& out, uint64_t v) { for (int i=0;i<8;++i) out.put(char(v >> (8*i))); }
static void str(std::ofstream& out, const std::string& s) { u64(out,s.size());out.write(s.data(),s.size()); }
// Every filler is 1 GiB, so the GGUF can be indexed on Wasm32 without any
// individual tensor exceeding its address width. Only the final 4 bytes are read.
static uint64_t sparse_gguf(const char* path, uint32_t gib, uint32_t version) {
    std::ofstream out(path, std::ios::binary);
    u32(out,0x46554747);u32(out,version);u64(out,gib+1);u64(out,2);
    str(out,"tokenizer.ggml.tokens");u32(out,9);u32(out,8);u64(out,2);str(out,"alpha");str(out,"beta");
    str(out,"test.array");u32(out,9);u32(out,4);u64(out,2);u32(out,7);u32(out,9);
    for(uint32_t i=0;i<=gib;++i) {
        str(out,i==gib ? "weight" : "padding"+std::to_string(i));
        u32(out,1);u64(out,i==gib ? 1 : 268435456);u32(out,0);u64(out,uint64_t(i)*(1ULL<<30));
    }
    const uint64_t data=(uint64_t(out.tellp())+31)/32*32;
    const uint64_t offset=data+uint64_t(gib)*(1ULL<<30);
    out.seekp(static_cast<std::streamoff>(offset));u32(out,0x3f800000); // float32 1.0
    out.close(); return offset;
}
int main() {
    try {
        check(sdc_abi_version()==2,"ABI version");
        sd_ctx_params_t context{};sdc_sd_ctx_params_init(uint64_t(uintptr_t(&context)));
        sd_ctx_params_t reference{};sd_ctx_params_init(&reference);
        check(context.n_threads==reference.n_threads && context.enable_mmap==reference.enable_mmap &&
            context.auto_fit==reference.auto_fit && context.conditioning_cache_size==reference.conditioning_cache_size,
            "binding must retain upstream defaults");
        sd_img_gen_params_t image{};sdc_sd_img_gen_params_init(uint64_t(uintptr_t(&image)));
        sd_img_gen_params_t defaults{};sd_img_gen_params_init(&defaults);
        check(image.width==defaults.width && image.height==defaults.height && image.batch_count==defaults.batch_count &&
            image.sample_params.sample_method==defaults.sample_params.sample_method,"no generation policy in core");
        for(uint32_t gib : {0,2,4,8}) {
            const char* path="large-offset-test.gguf";
            const uint64_t offset=sparse_gguf(path,gib,gib==2?2:3);
            check(sdc_test_gguf_offset(path)==offset,"64-bit GGUF metadata offset");
            check(sdc_test_gguf_value(path)==0x3f800000,"C++ random-access tensor read");
            std::remove(path);
            std::printf("unsplit GGUF sparse fixture: offset=%llu, tensor bytes read=4\n",(unsigned long long)offset);
        }
        { // Wrong version / truncated metadata must not fall through to another parser.
            const char* path="invalid-test.gguf";std::ofstream out(path,std::ios::binary);
            u32(out,0x46554747);u32(out,99);u64(out,1);u64(out,0);out.close();
            check(sdc_test_gguf_offset(path)==UINT64_MAX,"invalid GGUF rejection");std::remove(path);
        }
        for (int kind=0; kind<7; ++kind) {
            // 0: higher-rank extension; others: malformed metadata or tensor table.
            const char* path="metadata-test.gguf";std::ofstream out(path,std::ios::binary);
            u32(out,0x46554747);u32(out,3);u64(out,kind==6?2:1);u64(out,kind==2||kind==3?1:0);
            if(kind==2) { str(out,"general.alignment");u32(out,4);u32(out,3); }
            if(kind==3) { str(out,"bad.array");u32(out,9);u32(out,4);u64(out,UINT64_MAX); }
            for(int tensor=0;tensor<(kind==6?2:1);++tensor) {
                str(out,"weight"); const int rank=kind==0?5:1;u32(out,rank);
                for(int dim=0;dim<rank;++dim)u64(out,kind==4?UINT64_MAX:1);
                u32(out,kind==5?UINT32_MAX:0);u64(out,kind==1?1024:0);
            }
            const uint64_t data=(uint64_t(out.tellp())+31)/32*32;
            out.seekp(static_cast<std::streamoff>(data));u32(out,0x3f800000);out.close();
            if(kind==0) check(sdc_test_gguf_value(path)==0x3f800000,"extended GGUF rank flattening");
            else check(sdc_test_gguf_offset(path)==UINT64_MAX,"malformed GGUF must be rejected");
            std::remove(path);
        }
        sdc_sd_set_log_callback(uint64_t(uintptr_t(&log_callback)),17);
        sdc_sd_set_progress_callback(uint64_t(uintptr_t(&progress_callback)),19);
        sdc_test_callbacks();
        check(logs_seen==1 && progress_seen==1,"native callback registration/data");
        sdc_sd_set_log_callback(0,0);sdc_sd_set_progress_callback(0,0);
        sdc_test_callbacks();check(logs_seen==1 && progress_seen==1,"native callback removal");
        for (int batch : {1, 2}) {
            ggml_init_params params{64 * 1024 * 1024, nullptr, false};
            auto ctx = ggml_init(params);
            auto input = ggml_new_tensor_4d(ctx, GGML_TYPE_F32, 17, 11, 64, batch);
            auto values = static_cast<float*>(input->data);
            for (int64_t i = 0; i < ggml_nelements(input); ++i) values[i] = std::sin(float(i) * 0.17f);
            auto reference = ggml_group_norm(ctx, input, 32, 1e-6f);
            auto actual = ggml_ext_group_norm_32(ctx, input);
            auto graph = ggml_new_graph(ctx);
            ggml_build_forward_expand(graph, reference);ggml_build_forward_expand(graph, actual);
            check(ggml_graph_compute_with_ctx(ctx, graph, 1)==GGML_STATUS_SUCCESS,"graph compute");
            float maximum = 0;
            for (int64_t i = 0; i < ggml_nelements(input); ++i)
                maximum = std::max(maximum, std::abs(static_cast<float*>(reference->data)[i] - static_cast<float*>(actual->data)[i]));
            std::printf("group-norm batch=%d max-absolute-error=%.9g\n", batch, maximum);
            check(std::isfinite(maximum) && maximum<=1e-4f,"normalization parity");ggml_free(ctx);
        }
        std::puts("Native public binding, large-file metadata/read and normalization tests passed; no model inference.");
    } catch(const std::exception& e) { std::fprintf(stderr,"%s\n",e.what());return 1; }
}
