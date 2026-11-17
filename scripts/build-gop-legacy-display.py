#!/usr/bin/env python3
"""Build the GOP/flat-panel Legacy display backend for gtaxllte.

The board has no Metal GPU driver yet, so the render server draws with
QuartzCore's CPU renderer and this archive copies each completed swap into the
firmware GOP framebuffer.  The original IOMobileFramebufferLegacy lifecycle is
kept unchanged; only the concrete backend, the surface getters and the refresh
notification are bound to the original family's recorded addresses.

The ABI audit needs one prelinked candidate of this exact OS profile.  On a
fresh build tree the kernel step has not produced it yet; this recipe then
writes a bootstrap archive whose probe entry points are empty.  The workflow
builds that reference kernel first, then reruns the kernel step with this
script, which now finds ``kernel/kernel.macho`` and produces the real archive.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

from kernel_target import ARCH, IOS_VERSION, TRIPLE

if ARCH != 'arm64' or IOS_VERSION.split('.')[0] != '17':
    raise SystemExit('The legacy display ABI builder only supports arm64 iOS 17. '
                     'Its fixed original addresses and class layout are not qualified for iOS 26.')

ROOT = Path.cwd()  # the UniXNU tree: archive recipes run with cwd=tree
DEVICE = Path(os.environ.get('UNIXNU_DEVICE', ROOT / 'kits/gtaxllte'))
OUT = ROOT / 'build/native-legacy-display'
REPORT = OUT / 'build-report.json'
ARCHIVE = OUT / 'libNativeLegacyDisplay.late.a'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
from native_prelink import macho, backed_offset, exports
sys.path.insert(0, str(Path(__file__).resolve().parent))


def run(command, **keywords):
    return subprocess.check_output([str(value) for value in command], timeout=180, **keywords)


def require(value, why):
    if not value:
        raise SystemExit(why)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def report_archive(sources, **fields):
    REPORT.write_text(json.dumps(dict(
        source_sha256={str(Path(path).relative_to(ROOT)): sha(path) for path in sources},
        archive_sha256=sha(ARCHIVE), **fields), indent=2) + '\n')


def bootstrap_stub():
    """Kernel-pass reference: keep the probe entry points, skip the ABI body."""
    source = OUT / 'LegacyDisplayBootstrap.c'
    source.write_text(
        '/* Bootstrap reference for the prelinked display layout. */\n'
        'void native_legacy_display_probe(void) {}\n'
        'void native_legacy_display_late_probe(void) {}\n')
    object_path = OUT / 'LegacyDisplayBootstrap.o'
    sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
    run(['xcrun', 'clang', '-target', TRIPLE, '-isysroot', sdk, '-c', source, '-o', object_path])
    ARCHIVE.unlink(missing_ok=True)
    run(['xcrun', 'ar', 'rcs', ARCHIVE, object_path])
    report_archive([source], bootstrap=True, virtual_entries=0, owned_entries=[],
                   inherited_native_entries=[], guarded_backend_leaves=[],
                   original_instructions_modified=False, driver_initialized=False,
                   display_presented=False, desktop=False)
    print('bootstrap Legacy archive: reference kernel absent, probe stubs only')


reference = ROOT.parent / 'kernel/kernel.macho'
if not reference.is_file():
    bootstrap_stub()
    raise SystemExit(0)

# The reference kernel is this run's prelinked kernel with the display roots and
# the bootstrap probe.  It carries the exact original IOMobileGraphicsFamily
# extents and bytes that the final kernel will also use: prelinking places the
# original kexts at kernelcache-address + slide, independently of the native
# kernel's own size.
candidate = ROOT / 'build/xnu-prelink/native-owner-mobile-graphics-candidate.macho'
candidate.parent.mkdir(parents=True, exist_ok=True)
candidate.write_bytes(reference.read_bytes())
candidate.with_suffix('.json').write_bytes(reference.with_suffix('.json').read_bytes())

analysis = ROOT / 'build/display-analysis'
analysis.mkdir(parents=True, exist_ok=True)
audit_json = analysis / 'mobile-graphics-legacy-abi.json'
RUNTIME_SHA = '3044c2beff552911605994c0dc7aa73182066cbb5aed0b396999ceeb5a96da2b'
if not (ROOT / 'build/darwinboot-owner-hvf-mobile-graphics-legacy-class/gdb-stop.json').exists() and not audit_json.exists():
    # The class observation was taken on the unchanged original kernelcache;
    # the build keeps only its recorded hash.
    audit_json.write_text(json.dumps(dict(runtime_metadata_checkpoint_sha256=RUNTIME_SHA)) + '\n')

print(run(['python3', ROOT / 'scripts/audit-mobile-graphics-legacy.py'], text=True), end='')
audit = json.loads(audit_json.read_text())
pure = audit['abstract_slots']
methods = audit['methods']

header = '''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opaque ABI storage for the exact original class, not source-level access
// to its private fields. The actual original metadata supplies its full
// Framebuffer/Service/IOService ancestry. Only qualified base prototypes may
// be called from source; opaque entries are original dispatch targets only.
#pragma once
#include <IOKit/IOService.h>
#include <libkern/libkern.h>
struct OriginalLegacySwapRequest;
// Original state request (skip/requested and skip/powered word pairs, then the changed flag) taken by the state setter.
struct OriginalLegacyStateRequest {uint32_t skipRequested,requested,skipPowered,powered;uint8_t changed;uint8_t pad[11];};
// Original field offsets: vsync client queue in the display; rectangles, layer masks (updated, disabled, background),
// options, dimensions and surfaces in a swap request.
static constexpr unsigned kLegacyVsyncClients=5592;
static constexpr unsigned kRequestRect0=17,kRequestRect1=81,kRequestMasks=792,kRequestOptions=804,kRequestDims=816,kRequestSurfaces=2624;
// Backend leaves: pure original slots the owned backend implements only as guards.
#define NATIVE_LEGACY_LEAVES(LEAF) LEAF(211) LEAF(245) LEAF(246) LEAF(306) LEAF(307) LEAF(338) LEAF(345)
class IOMobileFramebufferLegacy : public IOService {
 OSDeclareAbstractStructors(IOMobileFramebufferLegacy)
 unsigned char originalPrivate[0x18a0-sizeof(IOService)];
public:
 // Both fields are qualified by the complete original slot-249 getter.
 // This modifies this object's own mode state, never a vtable or executable.
 void installBootDimensions(uint32_t width,uint32_t height) {
  *reinterpret_cast<uint32_t *>(reinterpret_cast<unsigned char *>(this)+5808)=width;
  *reinterpret_cast<uint32_t *>(reinterpret_cast<unsigned char *>(this)+5812)=height;
 }
 const OriginalLegacySwapRequest *pendingSwapRequest() const {
  const OriginalLegacySwapRequest *request=nullptr;
  memcpy(&request,reinterpret_cast<const unsigned char *>(this)+5480,sizeof(request));return request;
 }
 // The real normal scanout backend removes its pending head before calling
 // slot 295. Slot 312 is a discard path (terminal state 8), not presentation.
 // The queue is a tail queue (head at +5480, tail pointer at +5488, each request's next at +2960 and previous-link at +2968): taking the head
 // moves the next request, if a client already queued one behind it (int-132: swap 3 arrived during swap 2's GPU copy and the old
 // single-entry form refused to retire swap 2, so every wait on it stayed busy), up to the head. The original queue's reference
 // moves to the current-swap slot for the original completion body.
 bool activateOnlyPendingSwap(const OriginalLegacySwapRequest *request) {
  auto self=reinterpret_cast<unsigned char *>(this);
  auto bytes=reinterpret_cast<const unsigned char *>(request);
  const void *current=nullptr,*head=nullptr,*tail=nullptr,*next=nullptr,*previous=nullptr;
  memcpy(&current,self+5472,8);memcpy(&head,self+5480,8);memcpy(&tail,self+5488,8);
  if(!request || current || head!=request)return false;
  memcpy(&next,bytes+2960,8);memcpy(&previous,bytes+2968,8);
  if(previous!=self+5480)return false;
  if(next) {
   const void *link=self+5480;
   memcpy(const_cast<unsigned char *>(reinterpret_cast<const unsigned char *>(next))+2968,&link,8);
  } else if(tail!=bytes+2960)return false;
  memcpy(self+5472,&request,8);
  if(next) {memcpy(self+5480,&next,8);}
  else {const void *empty=nullptr,*emptyTail=self+5480;memcpy(self+5480,&empty,8);memcpy(self+5488,&emptyTail,8);}
  return true;
 }
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 IOReturn setProperties(OSObject *) override;
 void systemWillShutdown(IOOptionBits) override;
 IOService *probe(IOService *,SInt32 *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 IOWorkLoop *getWorkLoop() const override;
 IOReturn newUserClient(task_t,void *,UInt32,IOUserClient **) override;
 IOReturn setPowerState(unsigned long,IOService *) override;
'''
for index in range(168, 358):
    if index == 184:
        header += ' virtual bool isMainDisplay();\n'
    elif index == 188:
        header += ' virtual IOReturn getDotPitch(uint32_t *);\n'
    elif index == 213:
        header += ' virtual IOReturn supportedFrameInfo(uint32_t,uint32_t *,char *,uint32_t *)=0;\n'
    elif index == 249:
        header += ' virtual void getDisplayDimensions(uint32_t *,uint32_t *);\n'
    elif index == 290:
        header += ' virtual void kickSwapQueue();\n'
    elif index == 296:
        header += ' virtual bool validateSwap(const OriginalLegacySwapRequest *);\n'
    elif index == 295:
        header += ' virtual void completeCurrentSwap();\n'
    elif index == 337:
        header += ' virtual IOReturn collectCompletionReports(uint32_t,void *,uint32_t *,uint32_t)=0;\n'
    elif index == 288:
        header += ' virtual bool startHardware();\n'
    elif index == 354:
        header += ' virtual int applyStateRequest(OriginalLegacyStateRequest *,bool);\n'
    elif index == 301:
        header += ' virtual bool getRefreshAttributes(uint64_t *,bool);\n'
    elif index in pure:
        header += f' virtual __attribute__((noreturn)) void opaque{index}()=0;\n'
    else:
        header += f' virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque{index}();\n'
header += '};\nstatic_assert(sizeof(IOMobileFramebufferLegacy)==0x18a0,"Actual original instance extent");\n'
(OUT / 'OriginalLegacyFramebufferABI.h').write_text(header)

print(run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeLegacyDisplay'], text=True), end='')
obj = OUT / 'NativeLegacyDisplay.o'
raw = obj.read_bytes()
image = macho(raw)
symbol_table = next(at for command, at, size in image['commands'] if command == 2)
symoff, count, stroff, _ = struct.unpack_from('<4I', raw, symbol_table + 8)
symbols = []
for index in range(count):
    string_index, kind, section, _, value = struct.unpack_from('<IBBHQ', raw, symoff + index * 16)
    symbols.append((raw[stroff + string_index:raw.index(b'\0', stroff + string_index)].decode(), value, kind))
vtable = next(value for name, value, _ in symbols if name == '__ZTV19NativeLegacyDisplay')
relocations = {}
for segment in image['segments']:
    for section in segment['sections']:
        if section['va'] <= vtable < section['va'] + section['size']:
            relocation_offset, relocation_count = struct.unpack_from('<2I', raw, section['command_offset'] + 56)
            for index in range(relocation_count):
                offset, bits = struct.unpack_from('<2I', raw, relocation_offset + index * 8)
                if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                    relocations[section['va'] + offset] = symbols[bits & 0xffffff][0]
compiled = [relocations.get(vtable + 16 + index * 8) for index in range(358)]
require(all(compiled) and vtable + 16 + 358 * 8 not in relocations, 'Exact owned 358-entry vtable')

blob = candidate.read_bytes()
old_image = macho(blob)
names = {name: value for value, group in exports(blob, old_image).items() for name in group}


def read(va, length):
    at = backed_offset(old_image['segments'], va, length, len(blob))
    return blob[at:at + length]


bindings = {
    '__ZN25IOMobileFramebufferLegacyC2EPK11OSMetaClass': 0xfffffff0057865cc,
    '__ZN25IOMobileFramebufferLegacyD2Ev': 0xfffffff005786618,
    '__ZN25IOMobileFramebufferLegacy10gMetaClassE': 0xfffffff006b35940,
    '__ZTV25IOMobileFramebufferLegacy': int(audit['legacy_vptr'], 16) - 16,
}
owned = []
native = []
for index, name in enumerate(compiled):
    expected = int(methods[index]['address'], 16)
    if name.startswith(('__ZN19NativeLegacyDisplay', '__ZNK19NativeLegacyDisplay')):
        owned.append(index)
        require(index in (0, 1, 7, 14, 84, 86, 87, 141, 155, 184, 188, 288, 290, 296, 301) or index in pure,
                'Only qualified own methods and guarded leaves override')
    elif '25IOMobileFramebufferLegacy' in name:
        require(index not in pure, 'No unresolved original abstract backend target')
        bindings[name] = expected
    else:
        require(index < 168 and names.get(name) == expected, 'Actual inherited native base symbol ' + str(index))
        native.append(index)
required_owned = {14, 84, 86, 87, 141, 155, 184, 188, 288, 290, 296, 301} | set(pure)
require(required_owned <= set(owned), 'The concrete backend lifecycle overrides are incomplete')
require(set(owned) <= required_owned | {0, 1, 7, 84},
        'Only qualified own methods and guarded leaves override')
undefined = {name for name, _, kind in symbols if kind == 1 and 'IOMobileFramebufferLegacy' in name}
# Every actual original-class import must have a recorded address. A recorded
# address that the compiler did not import (the base vtable in some revisions)
# is harmless: it only adds an unused absolute symbol to the archive.
require(undefined <= set(bindings), 'All actual original-class imports qualified')

# Exact original IOSurface getters, independently observed through the real
# original swap request. These aliases call original code; no object layout
# is substituted and no original method/vtable is patched.
for name, address, field, wide in [
    ('descriptor', 0xfffffff004b9ead0, 48, True), ('width', 0xfffffff004b9ebbc, 88, True),
    ('height', 0xfffffff004b9ebc4, 96, True), ('stride', 0xfffffff004b9ebd4, 104, True),
    ('allocation', 0xfffffff004b9ebfc, 144, True), ('format', 0xfffffff004b9ebac, 128, False),
    ('planes', 0xfffffff004b9ebf4, 152, False)]:
    expected = (0xf9400000 if wide else 0xb9400000) | ((field // (8 if wide else 4)) << 10)
    require(read(address, 8) == struct.pack('<2I', expected, 0xd65f03c0), 'Exact original surface getter ' + name)
    bindings['_native_legacy_surface_' + name] = address
# The original display-refresh notification sender (self, timestamp, continuous host time, frame interval): it sends the
# six-word vsync message (type 5) to every client entry queued at +5592, the tail queue that selector 72's registration
# (slot 208) fills at +5512 + type*16. The platform subclass that normally calls it on each panel vblank is not part of
# this port.
require(read(0xfffffff005796720, 16) == bytes.fromhex('ff0302d1fd7b07a9fdc30191a0831ff8') and
        read(0xfffffff005796830, 4) == bytes.fromhex('08ed4af9') and
        read(0xfffffff00579685c, 16) == bytes.fromhex('e2230091c3008052240080521684ff97') and
        read(0xfffffff005795fbc, 4) == bytes.fromhex('0ab182d2'),
        'Original vsync sender, its +5592 queue and six-word drop-allowed async send')
bindings['_native_legacy_vsync_notify'] = 0xfffffff005796720
# Slot 301 fills the six refresh words that setDisplayRefreshProperties publishes as IOMFBDisplayRefresh (minimum
# interval, its mach time, maximum interval, its mach time, step, its mach time; intervals in 32.32 seconds): the generic
# body fixes the minimum at 0x04444444 (1/60 s), and the render server paces its display timer by it.
require(int(methods[301]['address'], 16) == 0xfffffff00579ea5c and
        read(0xfffffff00579ea68, 8) == bytes.fromhex('888888d28888a0f2') and
        read(0xfffffff00579ec54, 4) == bytes.fromhex('08b544f9'),
        'Original slot-301 refresh words with the 1/60 s minimum, called by setDisplayRefreshProperties')
# setDisplayRefreshProperties itself (self): fills the six words through slot 301 and publishes them as IOMFBDisplayRefresh.
require(read(0xfffffff00579ebb8, 16) == bytes.fromhex('ff0302d1fd7b07a9fdc30191a0031ff8') and
        read(0xfffffff00579ed54, 16) == bytes.fromhex('086540f90173ff90218c0c9100013fd6'),
        'Original setDisplayRefreshProperties publishing IOMFBDisplayRefresh through setProperty')
bindings['_native_legacy_set_refresh_properties'] = 0xfffffff00579ebb8


def branch(pc):
    word = struct.unpack('<I', read(pc, 4))[0]
    require(word & 0xfc000000 == 0x94000000, 'Actual protected base call')
    value = word & 0x3ffffff
    return pc + 4 * (value - (1 << 26) if value & (1 << 25) else value)


require(branch(0xfffffff0057865fc) == 0xfffffff00577b510 and
        branch(0xfffffff005786630) == 0xfffffff00577b55c,
        'Original protected Legacy C2/D2 really call their original Service bases')
require(read(0xfffffff005787ef8, 16) == bytes.fromhex('080040f9088144f900013fd6e0000037'),
        'Original bool start_hardware() call and result check')
getter = int(methods[249]['address'], 16)
for offset, field in [(0x30, 5808), (0x58, 5812)]:
    require(struct.unpack('<I', read(getter + offset, 4))[0] ==
            0xb9400000 | ((field // 4) << 10) | (8 << 5) | 8,
            'Actual original width/height getter fields')
require(int(methods[295]['address'], 16) == 0xfffffff005795b88 and
        read(0xfffffff005795b98, 16) == bytes.fromhex('e00740f9280080520101001272d3ff97'),
        'Actual original slot-295 passes true to the normal current completion body')
require(read(0xfffffff00578aa7c, 24) == bytes.fromhex('080040f9084545f9030080d2e20303aa0400805200013fd6'),
        'Original completion calls optional report slot 337 with null outputs and zero limit')
require(read(0xfffffff005777e9c, 16) == bytes.fromhex('080040f908e142f900013fd6c0000036'),
        'Original isMainDisplay dispatch uses only self and tests boolean bit zero')

assembly = OUT / 'original-bindings.S'
assembly.write_text(''.join(f'.globl {name}\n.set {name}, {value:#x}\n' for name, value in sorted(bindings.items())))
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', TRIPLE, '-isysroot', sdk, '-c', assembly, '-o', OUT / 'original-bindings.o'])
llvm = Path(run(['brew', '--prefix', 'llvm'], text=True).strip()) / 'bin'
late = OUT / 'NativeLegacyDisplay.late.o'
run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
     '--globalize-symbol=__GLOBAL__sub_I_NativeLegacyDisplay.cpp', obj, late])


def code(data, info):
    section = next(section for segment in info['segments'] for section in segment['sections']
                   if section['name'] == '__text')
    return data[section['fileoff']:section['fileoff'] + section['size']]


require(code(raw, image) == code(late.read_bytes(), macho(late.read_bytes())),
        'Only owned initializer section registration changes')

record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
                    .read_text().strip().rstrip(','))
source = ROOT / 'kits/gtaxllte/LegacyDisplayProbe.cpp'
command = []
arguments = record['arguments']
index = 0
while index < len(arguments):
    value = arguments[index]
    if value == record['file']:
        command.append(str(source))
    elif value == '-o':
        command += ['-o', str(OUT / 'LegacyDisplayProbe.o')]
        index += 1
    elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
        index += 1
    elif not value.startswith('-W'):
        command.append(value)
    index += 1
command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
            '-Wno-unnecessary-virtual-specifier', '-Wno-c99-designator', '-Wno-reorder-init-list',
            '-Wno-deprecated-enum-enum-conversion', '-I', str(ROOT / '.cache/xnu-work/osfmk')]
run(command, cwd=record['directory'])

print(run(['python3', ROOT / 'scripts/build-arm-driver.py', 'NativeGOPPresenter'], text=True), end='')
gop = ROOT / 'build/native-gop-presenter'
gop_late = gop / 'NativeGOPPresenter.late.o'
run([llvm / 'llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
     '--globalize-symbol=__GLOBAL__sub_I_NativeGOPPresenter.cpp', gop / 'NativeGOPPresenter.o', gop_late])
require(code((gop / 'NativeGOPPresenter.o').read_bytes(), macho((gop / 'NativeGOPPresenter.o').read_bytes())) ==
        code(gop_late.read_bytes(), macho(gop_late.read_bytes())),
        'Owned presenter instructions unchanged by late init staging')

ARCHIVE.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', ARCHIVE, late, OUT / 'original-bindings.o', OUT / 'LegacyDisplayProbe.o', gop_late])
sources = [
    ROOT / 'drivers/GopFramebuffer/NativeLegacyDisplay.cpp',
    ROOT / 'drivers/GopFramebuffer/NativeLegacyRequest.h',
    ROOT / 'drivers/GopFramebuffer/NativeGOPWire.h',
    ROOT / 'drivers/GopFramebuffer/NativeGpuTrace.h',
    ROOT / 'drivers/GopFramebuffer/NativeHalfCodes.h',
    ROOT / 'drivers/GopFramebuffer/NativeGOPPresenter.cpp',
    ROOT / 'kits/gtaxllte/LegacyDisplayProbe.cpp',
]
report_archive(sources,
               generated_abi_sha256=sha(OUT / 'OriginalLegacyFramebufferABI.h'),
               original_candidate_sha256=sha(candidate),
               original_audit_sha256=sha(audit_json),
               compiled_object_sha256=sha(obj), late_object_sha256=sha(late),
               bindings={name: hex(value) for name, value in bindings.items()},
               virtual_entries=358, owned_entries=owned, inherited_native_entries=native,
               guarded_backend_leaves=[index for index in pure if index not in (213, 337)],
               original_instructions_modified=False, driver_initialized=False,
               display_presented=False, desktop=False)
print('Built genuine original Legacy inheritance/dispatch; seven backend leaves still guarded, runtime lifecycle unqualified')
