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
#include <IOKit/IOBSD.h>
#include <IOKit/storage/IOBlockStorageDevice.h>
#include <libkern/c++/OSData.h>
#include <libkern/c++/OSDictionary.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSNumber.h>
#include <libkern/c++/OSString.h>
#include <kern/clock.h>
#include <kern/task.h>
#include <kern/thread.h>
#include <mach/vm_types.h>
#include <pexpert/pexpert.h>
#include <sys/param.h>
#include <sys/types.h>

extern "C" void gtaxllte_fb_mark(unsigned int, unsigned int);
#include <libkern/crypto/sha2.h>

namespace {
constexpr uint32_t kPwrEn = 0x04;
constexpr uint32_t kClkEna = 0x10;
constexpr uint32_t kTimeout = 0x14;
constexpr uint32_t kBlkSiz = 0x1c;
constexpr uint32_t kByteCnt = 0x20;
constexpr uint32_t kCmdArg = 0x28;
constexpr uint32_t kCmd = 0x2c;
constexpr uint32_t kRintSts = 0x44;
constexpr uint32_t kStatus = 0x48;
constexpr uint32_t kVerId = 0x6c;
constexpr uint32_t kHcon = 0x70;
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

class NativeDwMmc;

// One read-only APFS window of the SHNGROOT manifest, exposed as a 512-byte
// block device on top of the parent's PIO transport. The window starts in a
// kernel thread after the parent's start() returns, the same ownership order
// ShengUfs uses to avoid starting a child from inside a parent's start.
class NativeDwMmcWindow : public IOBlockStorageDevice {
  OSDeclareDefaultStructors(NativeDwMmcWindow)
  NativeDwMmc *parent = nullptr;
  uint64_t firstSector = 0;
  uint64_t sectorCount = 0;
  char label[32] = {};

 public:
  bool configure(NativeDwMmc *owner, uint64_t first, uint64_t count,
      const char *name);
  bool start(IOService *provider) override;
  void stop(IOService *provider) override;
  void free() override;
  const char *windowLabel() const { return label; }
  IOReturn doEjectMedia() override { return kIOReturnUnsupported; }
  IOReturn doFormatMedia(UInt64) override { return kIOReturnNotWritable; }
  UInt32 doGetFormatCapacities(UInt64 *, UInt32) const override { return 0; }
  char *getVendorString() override { return const_cast<char *>("Samsung"); }
  char *getProductString() override { return const_cast<char *>(label); }
  char *getRevisionString() override { return const_cast<char *>("1"); }
  char *getAdditionalDeviceInfoString() override {
    return const_cast<char *>("SHNGROOT window, MSHC PIO");
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
    *value = sectorCount - 1;
    return kIOReturnSuccess;
  }
  IOReturn reportMediaState(bool *present, bool *changed) override {
    *present = true;
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
  IOReturn doSynchronizeCache() override { return kIOReturnSuccess; }
  IOReturn doSynchronize(UInt64, UInt64, IOOptionBits) override {
    return kIOReturnSuccess;
  }
  IOReturn getWriteCacheState(bool *value) override {
    *value = false;
    return kIOReturnSuccess;
  }
  IOReturn setWriteCacheState(bool) override { return kIOReturnUnsupported; }
  IOReturn doAsyncReadWrite(IOMemoryDescriptor *, UInt64, UInt64,
      IOStorageAttributes *, IOStorageCompletion *) override;
};

class NativeDwMmc : public IOBlockStorageDevice {
  OSDeclareDefaultStructors(NativeDwMmc)
  IOMemoryMap *mapping = nullptr;
  IOBufferMemoryDescriptor *transfer = nullptr;
  IOLock *gate = nullptr;
  uintptr_t registers = 0;
  uint32_t fifo = 0;
  uint32_t itemWords = 1;
  bool online = false;
 public:
  struct Window { char name[32]; uint64_t offset; uint64_t size; };
  Window windows[4];
  uint32_t windowCount = 0;
  bool readManifest();
  bool readWindow(uint64_t sector, uint32_t bytes, IOMemoryDescriptor *buffer,
      uint64_t bufferOffset);

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
  static const char match[] = "samsung,exynos-dw-mshc";
  OSData *compatible = OSDynamicCast(OSData, provider->getProperty("compatible"));
  if (!compatible || compatible->getLength() < sizeof(match) ||
      memcmp(compatible->getBytesNoCopy(), match, sizeof(match))) {
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

bool NativeDwMmc::readWindow(uint64_t sector, uint32_t bytes,
    IOMemoryDescriptor *buffer, uint64_t bufferOffset) {
  IOLockLock(gate);
  IOReturn status = readSectors(sector, bytes);
  bool copied = status == kIOReturnSuccess &&
      buffer->writeBytes(bufferOffset, transfer->getBytesNoCopy(), bytes) == bytes;
  IOLockUnlock(gate);
  return copied;
}

static uint32_t gtaxllte_le32(const uint8_t *p) {
  uint32_t value;
  memcpy(&value, p, sizeof(value));
  return value;
}

static uint64_t gtaxllte_le64(const uint8_t *p) {
  uint64_t value;
  memcpy(&value, p, sizeof(value));
  return value;
}

bool NativeDwMmc::readManifest(void) {
  if (readSectors(0, 4096) != kIOReturnSuccess) return false;
  auto *raw = static_cast<uint8_t *>(transfer->getBytesNoCopy());
  if (memcmp(raw, "SHNGROOT", 8)) return false;
  uint32_t version = gtaxllte_le32(raw + 8);
  uint32_t count = gtaxllte_le32(raw + 12);
  if (!((version == 1 && count >= 1 && count <= 4) ||
      (version == 2 && count >= 1 && count <= 8))) return false;
  SHA256_CTX context;
  uint8_t digest[32];
  SHA256_Init(&context);
  SHA256_Update(&context, raw, 4064);
  SHA256_Final(digest, &context);
  if (memcmp(digest, raw + 4064, sizeof(digest))) return false;
  uint64_t limit = kUserDataSectors * kSectorBytes;
  for (uint32_t index = 0; index < count && windowCount < 4; index++) {
    const uint8_t *entry = raw + 24 + 128 * index;
    uint64_t offset = gtaxllte_le64(entry + 32);
    uint64_t size = gtaxllte_le64(entry + 40);
    uint32_t blockSize = gtaxllte_le32(entry + 48);
    uint32_t flags = gtaxllte_le32(entry + 52);
    if (blockSize != kSectorBytes || flags != 1) continue;
    if (!size || (offset & (kSectorBytes - 1)) || (size & (kSectorBytes - 1)) ||
        offset < 4096 || offset >= limit || size > limit - offset) return false;
    uint32_t length = 0;
    while (length < 32 && entry[length] >= 0x20 && entry[length] <= 0x7e) {
      length++;
    }
    if (!length || length == 32 || entry[length] != 0) return false;
    Window &window = windows[windowCount];
    bzero(&window, sizeof(window));
    strlcpy(window.name, reinterpret_cast<const char *>(entry),
        sizeof(window.name));
    window.offset = offset;
    window.size = size;
    windowCount++;
  }
  return windowCount != 0;
}

static void gtaxllte_publish_windows(void *argument, wait_result_t) {
  auto *owner = static_cast<NativeDwMmc *>(argument);
  for (uint32_t index = 0; index < owner->windowCount; index++) {
    auto *window = OSTypeAlloc(NativeDwMmcWindow);
    if (!window) continue;
    if (!window->init(nullptr) ||
        !window->configure(owner, owner->windows[index].offset / kSectorBytes,
            owner->windows[index].size / kSectorBytes, owner->windows[index].name) ||
        !window->attach(owner) || !window->start(owner)) {
      window->release();
      continue;
    }
    gtaxllte_fb_mark(273, 0x00FF00FF);  /* SHNGROOT window service started */
  }
  owner->release();
}

bool NativeDwMmc::start(IOService *provider) {
  gtaxllte_fb_mark(270, 0x00FF00FF);  /* NativeDwMmc::start */
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
  gtaxllte_fb_mark(271, 0x00FF00FF);  /* first media sector read */
  online = true;
  if (readManifest()) {
    gtaxllte_fb_mark(272, 0x00FF00FF);  /* SHNGROOT manifest parsed */
    retain();
    thread_t thread = nullptr;
    if (kernel_thread_start(gtaxllte_publish_windows, this, &thread) == KERN_SUCCESS) {
      thread_deallocate(thread);
    } else {
      release();
    }
  }
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

bool NativeDwMmcWindow::configure(NativeDwMmc *owner, uint64_t first,
    uint64_t count, const char *name) {
  if (!owner || !count || first + count > kUserDataSectors) return false;
  parent = owner;
  firstSector = first;
  sectorCount = count;
  strlcpy(label, name, sizeof(label));
  setProperty("SHNGROOTWindow", label);
  return true;
}

bool NativeDwMmcWindow::start(IOService *provider) {
  if (!parent || !IOBlockStorageDevice::start(provider)) return false;
  registerService();
  return true;
}

void NativeDwMmcWindow::stop(IOService *provider) {
  IOBlockStorageDevice::stop(provider);
}

void NativeDwMmcWindow::free() {
  parent = nullptr;
  IOBlockStorageDevice::free();
}

IOReturn NativeDwMmcWindow::doAsyncReadWrite(IOMemoryDescriptor *buffer,
    UInt64 block, UInt64 count, IOStorageAttributes *,
    IOStorageCompletion *completion) {
  gtaxllte_fb_mark(274, 0x00FF00FF);   /* APFS/IOStorage read the window */
  if (!buffer || !completion || !completion->action || !count ||
      block >= sectorCount || count > sectorCount - block ||
      count > UINT64_MAX / kSectorBytes ||
      buffer->getLength() < count * kSectorBytes) {
    return kIOReturnBadArgument;
  }
  if (buffer->getDirection() != kIODirectionIn) return kIOReturnNotWritable;
  uint64_t actual = 0;
  IOReturn result = kIOReturnNotReady;
  while (actual < count * kSectorBytes) {
    uint64_t left = count * kSectorBytes - actual;
    uint32_t bytes = static_cast<uint32_t>(left > kTransferBytes ? kTransferBytes : left);
    if (!parent->readWindow(firstSector + (block + actual / kSectorBytes),
        bytes, buffer, actual)) {
      gtaxllte_fb_mark(275, 0x00FF00FF);   /* window read failed */
      result = kIOReturnIOError;
      break;
    }
    actual += bytes;
  }
  if (actual == count * kSectorBytes) result = kIOReturnSuccess;
  completion->action(completion->target, completion->parameter, result, actual);
  return kIOReturnSuccess;
}
OSDefineMetaClassAndStructors(NativeDwMmcWindow, IOBlockStorageDevice)

// The unverified owner bring-up path. The signed OS/SystemOS policies are not
// built for this board yet; the owner cache and scratch Data mounts still use
// the original XNU mount code, and these three hooks answer its block-device
// questions from the two read-only SHNGROOT windows. The boot argument is
// deliberately explicit: without it the hooks refuse every device.
namespace {

bool allowUnverifiedWindow()
{
  unsigned allow = 0;
  PE_parse_boot_argn("gtaxllte-allow-unverified", &allow, sizeof(allow));
  return allow != 0;
}

bool mediaHasWindow(IOService *media, NativeDwMmcWindow **window = nullptr)
{
  unsigned depth = 0;
  for (IOService *provider = media; provider && depth++ < 64; provider = provider->getProvider()) {
    NativeDwMmcWindow *candidate = OSDynamicCast(NativeDwMmcWindow, provider);
    if (candidate) {
      if (window) *window = candidate;
      return true;
    }
  }
  return false;
}

IOService *copySingleMedia(OSDictionary *matching)
{
  OSIterator *iterator = matching ? IOService::getMatchingServices(matching) : nullptr;
  if (!iterator) return nullptr;
  IOService *found = nullptr;
  unsigned matches = 0;
  while (OSObject *object = iterator->getNextObject()) {
    IOService *media = OSDynamicCast(IOService, object);
    if (!media) continue;
    if (++matches != 1) {
      found = nullptr;
      break;
    }
    found = media;
    found->retain();
  }
  iterator->release();
  return found;
}

IOService *copyMediaForDevice(dev_t device)
{
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(2);
  OSNumber *mjr = OSNumber::withNumber(static_cast<unsigned>(major(device)), 32);
  OSNumber *mnr = OSNumber::withNumber(static_cast<unsigned>(minor(device)), 32);
  if (!matching || !properties || !mjr || !mnr ||
      !properties->setObject(kIOBSDMajorKey, mjr) ||
      !properties->setObject(kIOBSDMinorKey, mnr) ||
      !matching->setObject("IOPropertyMatch", properties)) {
    OSSafeReleaseNULL(mnr);
    OSSafeReleaseNULL(mjr);
    OSSafeReleaseNULL(properties);
    OSSafeReleaseNULL(matching);
    return nullptr;
  }
  mjr->release();
  mnr->release();
  properties->release();
  IOService *media = copySingleMedia(matching);
  matching->release();
  return media;
}

IOService *copyMediaForUUID(const char *uuid)
{
  OSDictionary *matching = IOService::serviceMatching("IOMedia");
  OSDictionary *properties = OSDictionary::withCapacity(1);
  OSString *wanted = OSString::withCString(uuid);
  if (!matching || !properties || !wanted ||
      !properties->setObject("UUID", wanted) ||
      !matching->setObject("IOPropertyMatch", properties)) {
    OSSafeReleaseNULL(wanted);
    OSSafeReleaseNULL(properties);
    OSSafeReleaseNULL(matching);
    return nullptr;
  }
  wanted->release();
  properties->release();
  IOService *media = copySingleMedia(matching);
  matching->release();
  return media;
}

} // namespace

extern "C" int native_verified_block_root_authorized(dev_t device)
{
  if (!allowUnverifiedWindow() || device == NODEV) return 0;
  IOService *media = copyMediaForDevice(device);
  bool authorized = media && mediaHasWindow(media);
  if (media) media->release();
  return authorized ? 1 : 0;
}

extern "C" int native_verified_block_volume_device(const char *uuid, dev_t *device,
    char *path, size_t size)
{
  if (!allowUnverifiedWindow() || !uuid || strlen(uuid) != 36 || !device || !path || size < 6) return 0;
  *device = NODEV;
  path[0] = 0;
  // The SystemOS window is published with the OS window; APFS probes both and
  // sets each IOMedia's UUID. The root mount returns before this call, but a
  // few short retries cover a late SystemOS probe.
  for (unsigned attempt = 0; attempt < 50; attempt++) {
    IOService *media = copyMediaForUUID(uuid);
    if (media && mediaHasWindow(media)) {
      OSString *name = OSDynamicCast(OSString, media->getProperty(kIOBSDNameKey));
      OSNumber *mjr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMajorKey));
      OSNumber *mnr = OSDynamicCast(OSNumber, media->getProperty(kIOBSDMinorKey));
      if (name && mjr && mnr && name->getLength() &&
          name->getLength() < size - 6 &&
          mjr->unsigned64BitValue() <= 255 &&
          mnr->unsigned64BitValue() <= 0xffffff) {
        *device = static_cast<dev_t>(makedev(mjr->unsigned32BitValue(), mnr->unsigned32BitValue()));
        snprintf(path, size, "/dev/%s", name->getCStringNoCopy());
        media->release();
        return 1;
      }
    }
    if (media) media->release();
    IOSleep(100);
  }
  return 0;
}

extern "C" int native_verified_block_manifest_sha384(dev_t device, const char *uuid,
    uint8_t digest[48])
{
  if (!allowUnverifiedWindow() || !digest || !uuid || strlen(uuid) != 36) return 0;
  IOService *media = copyMediaForDevice(device);
  bool authorized = media && mediaHasWindow(media);
  if (media) media->release();
  if (!authorized) return 0;
  static const char domain[] = "gtaxllte.unverified.window.v1";
  SHA384_CTX context;
  SHA384_Init(&context);
  SHA384_Update(&context, domain, sizeof(domain));
  SHA384_Update(&context, uuid, 36);
  SHA384_Final(digest, &context);
  return 1;
}

// NativeRamBlock's persistence hooks are optional weak imports. This board has
// no UFS store window yet, so the volatile packed Data seed is the only mode;
// provide the no-store answers directly instead of pulling NativeUfs in. The
// loader-memory release is likewise absent in this prelinked kernel.
extern "C" bool ml_static_mfree_prepare(vm_offset_t, vm_size_t) { return false; }
extern "C" unsigned native_ufs_store_mode(void) { return 0; }
extern "C" int native_ufs_store_open(uint64_t, uint64_t *, const char **) { return 0; }
extern "C" int native_ufs_store_read(uint64_t, uint32_t, void *) { return 0; }
extern "C" int native_ufs_store_write(uint64_t, uint32_t, const void *) { return 0; }
extern "C" int native_ufs_store_write_cached(uint64_t, uint32_t, const void *) { return 0; }
extern "C" int native_ufs_store_sync(void) { return 0; }
extern "C" unsigned native_ufs_store_gpt_check(void) { return 0; }

// The GPU addon patch references this RamBlock helper even when no addon is
// built; answer without a seed.
extern "C" int native_ram_block_gpu_addon(const uint8_t **data, uint64_t *bytes)
{
  if (data) *data = nullptr;
  if (bytes) *bytes = 0;
  return 0;
}
