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
constexpr uint64_t kCmuBase = 0x10460000;
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

void release(Mapped &mapped)
{
    OSSafeReleaseNULL(mapped.map);
    OSSafeReleaseNULL(mapped.memory);
}

uint32_t word(const Mapped &mapped, uint32_t offset)
{
    return mapped.words ? mapped.words[offset / 4] : 0xffffffffU;
}
} // namespace

extern "C" void native_mali_t830_probe(void)
{
    unsigned enabled = 0;
    if (!PE_parse_boot_argn("gtaxllte-mali-probe", &enabled, sizeof(enabled)) || !enabled) {
        return;
    }

    Mapped pd = map_words(kG3dPdBase, 0x20);
    Mapped cmu = map_words(kCmuBase, 0x4000);
    uint32_t pd_status = word(pd, 0x04);
    uint32_t pd_control = word(pd, 0x00);
    uint32_t cmu0 = word(cmu, 0x0000);
    uint32_t cmu1 = word(cmu, 0x1000);
    uint32_t cmu2 = word(cmu, 0x2000);

    Mapped gpu = {};
    uint32_t gpu_id = 0xffffffffU, gpu_version = 0xffffffffU, gpu_status = 0xffffffffU;
    if ((pd_status & 0x1U) != 0) {
        gpu = map_words(kGpuBase, 0x100);
        gpu_id = word(gpu, 0x00);
        gpu_version = word(gpu, 0x04);
        gpu_status = word(gpu, 0x08);
    }

    panic("Mali-T830 probe: pd=%08x/%08x cmu=%08x/%08x/%08x gpu=%08x ver=%08x st=%08x",
          pd_control, pd_status, cmu0, cmu1, cmu2,
          gpu_id, gpu_version, gpu_status);
}
