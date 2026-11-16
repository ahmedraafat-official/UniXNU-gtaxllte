// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// NativeDwMmc: read-only block transport for the Samsung MSHC (dwmmc0) eMMC
// controller on the Exynos 7870 (SM-T585, gtaxllte).
//
// The first version is deliberately PIO: UEFI's MshcDxe already identified the
// card and enabled the CIU clock, so single-block CMD17 reads need no IDMAC
// descriptors, no SMMU programming and no interrupt setup. The media is the
// USERDATA window; the offsets come from the measured GPT (phase0/emmc-plan.md)
// until the root-window table is generated from the userdata build report.
//
// Register map: Mu-Silicium MshcHost.h (Samsung MSHC, not generic DWMSHC).
#include <IOKit/IOBufferMemoryDescriptor.h>
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <IOKit/storage/IOBlockStorageDevice.h>
#include <libkern/c++/OSData.h>
#include <kern/clock.h>
#include <kern/task.h>

namespace {
constexpr uint32_t kCtrl = 0x00;
constexpr uint32_t kPwrEn = 0x04;
constexpr uint32_t kClkDiv = 0x08;
constexpr uint32_t kClkEna = 0x10;
constexpr uint32_t kTimeout = 0x14;
constexpr uint32_t kCtype = 0x18;
constexpr uint32_t kBlkSiz = 0x1c;
constexpr uint32_t kByteCnt = 0x20;
constexpr uint32_t kCmdArg = 0x28;
constexpr uint32_t kCmd = 0x2c;
constexpr uint32_t kRintSts = 0x44;
constexpr uint32_t kStatus = 0x48;
constexpr uint32_t kFifoTh = 0x4c;
constexpr uint32_t kCdEtect = 0x50;
constexpr uint32_t kVerId = 0x6c;
constexpr uint32_t kHcon = 0x70;
constexpr uint32_t kBmod = 0x80;
constexpr uint32_t kIdSts = 0x90;
constexpr uint32_t kFifoLegacy = 0x100;
constexpr uint32_t kFifo240A = 0x200;

constexpr uint32_t kCmdResp = 1u << 6;
constexpr uint32_t kCmdCrc = 1u << 8;
constexpr uint32_t kCmdData = 1u << 9;
constexpr uint32_t kCmdWait = 1u << 13;
constexpr uint32_t kCmdHold = 1u << 29;
constexpr uint32_t kCmdStart = 1u << 31;
constexpr uint32_t kRintRxdr = 1u << 5;
constexpr uint32_t kRintDto = 1u << 3;
constexpr uint32_t kStatusBusy = 1u << 9;
constexpr uint32_t kErrors = (1u << 1) | (1u << 6) | (1u << 7) | (1u << 8) |
    (1u << 9) | (1u << 10) | (1u << 11) | (1u << 12) | (1u << 13) | (1u << 15);

// Measured GPT geometry (phase0/emmc-dump/partition-sectors.txt). The media
// starts at the USERDATA partition and exposes its 512-byte sectors.
constexpr uint64_t kFirstLba = 8013824;
constexpr uint64_t kUserDataSectors = 53043200;
constexpr uint32_t kTransferBytes = 64 * 1024;
constexpr uint32_t kSectorBytes = 512;
}  // namespace

class NativeDwMmc : public IOBlockStorageDevice {
  OSDeclareDefaultStructors(NativeDwMmc)
  IOMemoryMap *mapping = nullptr;
  IOBufferMemoryDescriptor *transfer = nullptr;
  IOLock *gate = nullptr;
  uintptr_t registers = 0;
  uint32_t fifo = 0;
  uint32_t itemWords = 1;
  bool online = false;

  uint32_t read32(uint32_t offset) const {
    return *reinterpret_cast<volatile uint32_t *>(registers + offset);
  }
  void write32(uint32_t offset, uint32_t value) {
    *reinterpret_cast<volatile uint32_t *>(registers + offset) = value;
    __asm__ volatile("dsb oshst" ::: "memory");
  }
  IOReturn readBlock(uint32_t lba, void *buffer);
  IOReturn readSectors(uint64_t sector, uint32_t bytes);

 public:
  IOService *probe(IOService *, SInt32 *) override;
  bool start(IOService *) override;
  void stop(IOService *) override;
  void free() override;
  IOReturn doEjectMedia() override { return kIOReturnUnsupported; }
  IOReturn doFormatMedia(UInt64) override { return kIOReturnNotWritable; }
  UInt32 doGetFormatCapacities(UInt64 *, UInt32) const override { return 0; }
  char *getVendorString() override { return const_cast<char *>("Samsung"); }
  char *getProductString() override { return const_cast<char *>("MSHC eMMC read-only"); }
  char *getRevisionString() override { return const_cast<char *>("1"); }
  char *getAdditionalDeviceInfoString() override {
    return const_cast<char *>("dwmmc0 PIO, USERDATA window");
  }
  IOReturn reportBlockSize(UInt64 *value) override {
    *value = kSectorBytes;
    return kIOReturnSuccess;
  }
  IOReturn reportEjectability(bool *value) override {
    *value = false;
    return kIOReturnSuccess;
  }
  IOReturn reportMaxValidBlock(UInt64 *value) override {
    *value = kUserDataSectors - 1;
    return kIOReturnSuccess;
  }
  IOReturn reportMediaState(bool *present, bool *changed) override {
    *present = online;
    if (changed) *changed = false;
    return kIOReturnSuccess;
  }
  IOReturn reportRemovability(bool *value) override {
    *value = false;
    return kIOReturnSuccess;
  }
  IOReturn reportWriteProtection(bool *value) override {
    *value = true;
    return kIOReturnSuccess;
  }
  IOReturn doSynchronizeCache() override {
    return online ? kIOReturnSuccess : kIOReturnNotReady;
  }
  IOReturn doSynchronize(UInt64, UInt64, IOOptionBits) override {
    return doSynchronizeCache();
  }
  IOReturn getWriteCacheState(bool *value) override {
    *value = false;
    return kIOReturnSuccess;
  }
  IOReturn setWriteCacheState(bool) override { return kIOReturnUnsupported; }
  IOReturn doAsyncReadWrite(IOMemoryDescriptor *, UInt64, UInt64,
      IOStorageAttributes *, IOStorageCompletion *) override;
};
OSDefineMetaClassAndStructors(NativeDwMmc, IOBlockStorageDevice)

IOService *NativeDwMmc::probe(IOService *provider, SInt32 *score) {
  OSData *compatible = OSDynamicCast(OSData, provider->getProperty("compatible"));
  if (!compatible || compatible->getLength() < 24 ||
      memcmp(compatible->getBytesNoCopy(), "samsung,exynos-dw-mshc", 24)) {
    return nullptr;
  }
  return IOBlockStorageDevice::probe(provider, score);
}

IOReturn NativeDwMmc::readBlock(uint32_t lba, void *buffer) {
  auto *words = static_cast<uint32_t *>(buffer);
  uint32_t spin, rint = 0, status, count, done = 0;

  for (spin = 0; spin < 200000; spin++) {
    if (!(read32(kStatus) & kStatusBusy)) break;
  }
  write32(kRintSts, 0xffffffffu);
  write32(kTimeout, 0xffffffffu);
  write32(kBlkSiz, kSectorBytes);
  write32(kByteCnt, kSectorBytes);
  write32(kCmdArg, static_cast<uint32_t>(kFirstLba + lba));
  write32(kCmd, 17u | kCmdResp | kCmdCrc | kCmdData | kCmdWait | kCmdHold | kCmdStart);

  for (spin = 0; spin < 2000000 && done < kSectorBytes / 4; spin++) {
    rint = read32(kRintSts);
    if (rint & kErrors) break;
    status = read32(kStatus);
    count = (status >> 17) & 0x1fffu;
    while (count != 0 && done < kSectorBytes / 4) {
      for (uint32_t w = 0; w < itemWords && done < kSectorBytes / 4; w++) {
        words[done++] = read32(fifo + w * 4);
      }
      count--;
    }
    if (count != 0) write32(kRintSts, kRintRxdr);
  }
  if (done == kSectorBytes / 4) {
    for (spin = 0; spin < 200000; spin++) {
      rint = read32(kRintSts);
      if (rint & (kRintDto | kErrors)) break;
    }
  }
  return (done == kSectorBytes / 4 && (rint & kRintDto) && !(rint & kErrors)) ?
      kIOReturnSuccess : kIOReturnTimeout;
}

IOReturn NativeDwMmc::readSectors(uint64_t sector, uint32_t bytes) {
  if (!online || !bytes || (bytes & (kSectorBytes - 1)) ||
      sector >= kUserDataSectors || bytes / kSectorBytes > kUserDataSectors - sector) {
    return kIOReturnBadArgument;
  }
  auto *buffer = static_cast<uint8_t *>(transfer->getBytesNoCopy());
  for (uint32_t offset = 0; offset < bytes; offset += kSectorBytes) {
    IOReturn status = readBlock(static_cast<uint32_t>(sector + offset / kSectorBytes),
        buffer + offset);
    if (status != kIOReturnSuccess) return status;
  }
  return kIOReturnSuccess;
}

bool NativeDwMmc::start(IOService *provider) {
  if (!IOBlockStorageDevice::start(provider) ||
      provider->getResources() != kIOReturnSuccess) {
    return false;
  }
  mapping = provider->mapDeviceMemoryWithIndex(0,
      kIOMapAnywhere | kIOMapInhibitCache);
  if (!mapping || mapping->getLength() < 0x2000) return false;
  registers = static_cast<uintptr_t>(mapping->getVirtualAddress());
  uint32_t verid = read32(kVerId);
  if (verid == 0 || verid == 0xffffffffu) return false;
  fifo = ((verid & 0xffffu) < 0x240au) ? kFifoLegacy : kFifo240A;
  itemWords = (((read32(kHcon) >> 7) & 7) == 2) ? 2 : 1;
  if (!(read32(kPwrEn) & 1u) || !(read32(kClkEna) & 1u)) return false;
  transfer = IOBufferMemoryDescriptor::inTaskWithOptions(kernel_task,
      static_cast<IOOptionBits>(kIODirectionIn), kTransferBytes, 0x1000);
  if (!transfer) return false;
  gate = IOLockAlloc();
  if (!gate) return false;
  uint8_t first[kSectorBytes];
  if (readBlock(0, first) != kIOReturnSuccess) return false;
  online = true;
  registerService();
  return true;
}

IOReturn NativeDwMmc::doAsyncReadWrite(IOMemoryDescriptor *buffer, UInt64 block,
    UInt64 count, IOStorageAttributes *, IOStorageCompletion *completion) {
  if (!buffer || !completion || !completion->action || !count ||
      block >= kUserDataSectors || count > kUserDataSectors - block ||
      count > UINT64_MAX / kSectorBytes ||
      buffer->getLength() < count * kSectorBytes) {
    return kIOReturnBadArgument;
  }
  if (buffer->getDirection() != kIODirectionIn) return kIOReturnNotWritable;
  uint64_t actual = 0;
  IOReturn result = kIOReturnNotReady;
  IOLockLock(gate);
  while (online && actual < count * kSectorBytes) {
    uint64_t left = count * kSectorBytes - actual;
    uint32_t bytes = static_cast<uint32_t>(left > kTransferBytes ? kTransferBytes : left);
    result = readSectors(block + actual / kSectorBytes, bytes);
    if (result) break;
    if (buffer->writeBytes(actual, transfer->getBytesNoCopy(), bytes) != bytes) {
      result = kIOReturnIOError;
      break;
    }
    actual += bytes;
  }
  IOLockUnlock(gate);
  completion->action(completion->target, completion->parameter, result, actual);
  return kIOReturnSuccess;
}

void NativeDwMmc::stop(IOService *provider) {
  online = false;
  IOBlockStorageDevice::stop(provider);
}

void NativeDwMmc::free() {
  online = false;
  if (transfer) {
    transfer->release();
    transfer = nullptr;
  }
  if (mapping) {
    mapping->release();
    mapping = nullptr;
  }
  if (gate) {
    IOLockFree(gate);
    gate = nullptr;
  }
  IOBlockStorageDevice::free();
}
