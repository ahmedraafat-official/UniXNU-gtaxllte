// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Mali-T830 (Exynos 7870) read-only bring-up probe. It reads the G3D power
// domain, CMU_G3D state and, only when the domain is on, the GPU ID/version
// registers. It writes no GPU/PMU register; the first write milestone is M3.
#include <IOKit/IOLib.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <libkern/libkern.h>
#include <pexpert/pexpert.h>

namespace {
constexpr uint64_t kG3dPdBase = 0x10484000;
constexpr uint64_t kCmuBase = 0x11460000;
constexpr uint64_t kGpuBase = 0x11400000;

struct Mapped {
    IOMemoryDescriptor *memory;
    IOMemoryMap *map;
    volatile uint32_t *words;
};

Mapped map_words(uint64_t physical, uint32_t bytes)
{
    Mapped result = {};
    result.memory = IOMemoryDescriptor::withPhysicalAddress(physical, bytes, kIODirectionNone);
    if (result.memory) {
        result.map = result.memory->map(kIOMapAnywhere | kIOMapInhibitCache);
    }
    if (result.map) {
        result.words = reinterpret_cast<volatile uint32_t *>(result.map->getVirtualAddress());
    }
    return result;
}

uint32_t word(const Mapped &mapped, uint32_t offset)
{
    return mapped.words ? mapped.words[offset / 4] : 0xffffffffU;
}

void put(const Mapped &mapped, uint32_t offset, uint32_t value)
{
    if (mapped.words) {
        mapped.words[offset / 4] = value;
        __asm__ volatile("dsb sy" ::: "memory");
    }
}
} // namespace

extern "C" void native_mali_t830_probe(void)
{
    unsigned enabled = 0, reset = 0;
    PE_parse_boot_argn("gtaxllte-mali-probe", &enabled, sizeof(enabled));
    PE_parse_boot_argn("gtaxllte-mali-reset", &reset, sizeof(reset));
    if (!enabled && !reset) {
        return;
    }

    Mapped pd = map_words(kG3dPdBase, 0x20);
    uint32_t pd_status = word(pd, 0x04);
    uint32_t pd_control = word(pd, 0x00);
    uint32_t cmu_mux = 0xffffffffU, cmu_div = 0xffffffffU, cmu_gate = 0xffffffffU, cmu_gate2 = 0xffffffffU;
    uint32_t gpu_id = 0xffffffffU, l2_features = 0xffffffffU, core_features = 0xffffffffU;
    uint32_t reset_result = 0xffffffffU, reset_raw = 0xffffffffU;
    Mapped cmu = {}, gpu = {};
    // CMU_G3D and the GPU registers are inside the G3D domain; read them only
    // when the domain is on so a read-only probe cannot fault on a powered-off
    // block. The PMU domain control itself is always accessible.
    if ((pd_status & 0x1U) != 0) {
        cmu = map_words(kCmuBase, 0x1000);
        cmu_mux = word(cmu, 0x0208);
        cmu_div = word(cmu, 0x0400);
        cmu_gate = word(cmu, 0x0804);
        cmu_gate2 = word(cmu, 0x0808);
        gpu = map_words(kGpuBase, 0x100);
        gpu_id = word(gpu, 0x00);
        l2_features = word(gpu, 0x04);
        core_features = word(gpu, 0x08);

        // M3 first write: the Panfrost v5 software reset sequence. The GPU is
        // already clocked; no power/clock write is made in this probe.
        if (reset) {
            put(gpu, 0x28, 0x00000000U);          // GPU_INT_MASK
            put(gpu, 0x24, 0x00000100U);          // GPU_INT_CLEAR reset bit
            put(gpu, 0x30, 0x00000001U);          // GPU_CMD_SOFT_RESET
            for (unsigned attempt = 0; attempt < 10000; attempt++) {
                reset_raw = word(gpu, 0x20);      // GPU_INT_RAWSTAT
                if ((reset_raw & 0x00000100U) != 0) {
                    reset_result = 0;
                    break;
                }
                IODelay(10);
            }
            if (reset_result != 0) {
                // Hard reset fallback, as panfrost_gpu_soft_reset().
                put(gpu, 0x30, 0x00000002U);      // GPU_CMD_HARD_RESET
                for (unsigned attempt = 0; attempt < 10000; attempt++) {
                    reset_raw = word(gpu, 0x20);
                    if ((reset_raw & 0x00000100U) != 0) {
                        reset_result = 1;
                        break;
                    }
                    IODelay(10);
                }
            }
            put(gpu, 0x24, 0x00030781U);          // GPU_INT_CLEAR all
            put(gpu, 0x28, 0x00000081U);          // GPU_INT_MASK errors
            gpu_id = word(gpu, 0x00);
        }
    }

    panic("Mali-T830 probe: pd=%08x/%08x cmu=%08x/%08x/%08x/%08x gpu=%08x l2=%08x core=%08x reset=%08x raw=%08x",
          pd_control, pd_status, cmu_mux, cmu_div, cmu_gate, cmu_gate2,
          gpu_id, l2_features, core_features, reset_result, reset_raw);
}
