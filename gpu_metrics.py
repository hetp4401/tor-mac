"""Read driver GPU counters through IOKit; no processes, sudo, or GPU workloads."""
import ctypes as C
import math
import plistlib
import sys
import time


KEYS = {
    'Device Utilization %': 'gpu_percent',
    'Renderer Utilization %': 'gpu_renderer_percent',
    'Tiler Utilization %': 'gpu_tiler_percent',
    'In use system memory': 'gpu_memory_used',
    'Alloc system memory': 'gpu_memory_allocated',
}


class IOKitReader:
    def __init__(self):
        self.io = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        def bind(lib, name, result, args):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args
            return fn
        self.match = bind(self.io, 'IOServiceMatching', C.c_void_p, [C.c_char_p])
        self.services = bind(self.io, 'IOServiceGetMatchingServices', C.c_int, [C.c_uint32, C.c_void_p, C.POINTER(C.c_uint32)])
        self.next = bind(self.io, 'IOIteratorNext', C.c_uint32, [C.c_uint32])
        self.release = bind(self.io, 'IOObjectRelease', C.c_int, [C.c_uint32])
        self.kind = bind(self.io, 'IOObjectGetClass', C.c_int, [C.c_uint32, C.c_void_p])
        self.property = bind(self.io, 'IORegistryEntryCreateCFProperty', C.c_void_p, [C.c_uint32, C.c_void_p, C.c_void_p, C.c_uint32])
        self.string = bind(self.cf, 'CFStringCreateWithCString', C.c_void_p, [C.c_void_p, C.c_char_p, C.c_uint32])
        self.cf_release = bind(self.cf, 'CFRelease', None, [C.c_void_p])
        self.type_id = bind(self.cf, 'CFGetTypeID', C.c_ulong, [C.c_void_p])
        self.dict_type = bind(self.cf, 'CFDictionaryGetTypeID', C.c_ulong, [])
        self.data = bind(self.cf, 'CFPropertyListCreateData', C.c_void_p, [C.c_void_p, C.c_void_p, C.c_long, C.c_ulong, C.c_void_p])
        self.length = bind(self.cf, 'CFDataGetLength', C.c_long, [C.c_void_p])
        self.bytes = bind(self.cf, 'CFDataGetBytePtr', C.c_void_p, [C.c_void_p])


    def read(self):
        iterator = C.c_uint32()
        matching = self.match(b'IOAccelerator')
        if not matching:
            raise OSError('GPU service matching unavailable')
        # GetMatchingServices consumes the matching dictionary, including on failure.
        if self.services(0, matching, C.byref(iterator)):
            raise OSError('GPU registry unavailable')
        key = self.string(None, b'PerformanceStatistics', 0x08000100)
        if not key:
            self.release(iterator.value)
            raise MemoryError('GPU property key unavailable')
        rows = []
        try:
            while service := self.next(iterator.value):
                stats = None
                try:
                    stats = self.property(service, key, None, 0)
                    if not stats or self.type_id(stats) != self.dict_type():
                        continue
                    name = C.create_string_buffer(128)
                    self.kind(service, name)
                    data = self.data(None, stats, 200, 0, None)
                    if not data:
                        raise ValueError('GPU statistics unavailable')
                    try:
                        size = self.length(data)
                        if not 0 < size <= 65536:
                            raise ValueError('GPU statistics too large')
                        values = plistlib.loads(C.string_at(self.bytes(data), size))
                    finally:
                        self.cf_release(data)
                    rows.append({'name': name.value.decode(errors='replace'), 'statistics': values})
                finally:
                    if stats:
                        self.cf_release(stats)
                    self.release(service)
        finally:
            self.cf_release(key)
            self.release(iterator.value)
        return rows


def normalize(rows, now):
    devices = []
    for row in rows:
        values = {}
        for source, target in KEYS.items():
            n = row.get('statistics', {}).get(source)
            valid = isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) and n >= 0
            if target.endswith('_percent'):
                valid = valid and n <= 100
            values[target] = n if valid else None
        if any(n is not None for n in values.values()):
            devices.append({'name': str(row.get('name') or 'GPU')[:80], 'counter_names': sorted(str(k)[:80] for k in row.get('statistics', {}))[:64], **values})
    result = {key: None for key in KEYS.values()}
    result.update(gpu_state='available' if devices else 'unavailable', gpu_sample_at=now,
                  gpu_error=None, gpu_name=None, gpu_devices=len(devices))
    if not devices:
        result['gpu_reason'] = 'GPU counters are not exposed by this Mac'
        return result
    # Display the busiest adapter; each reading and its memory belong to that adapter.
    device = max(devices, key=lambda d: d['gpu_percent'] if d['gpu_percent'] is not None else -1)
    result.update({k: device[k] for k in KEYS.values()})
    result.update(gpu_name=device['name'], gpu_counter_names=device['counter_names'], gpu_reason='Busiest adapter' if len(devices) > 1 else None)
    return result


class GpuMetrics:
    def __init__(self, reader=None, platform=None):
        self.reader = reader
        self.platform = sys.platform if platform is None else platform
        self.data = normalize([], 0)

    def sample(self, now=None):
        now = time.time() if now is None else now
        if self.platform != 'darwin':
            return normalize([], now)
        try:
            if self.reader is None:
                self.reader = IOKitReader()
            self.data = normalize(self.reader.read(), now)
        except Exception:
            self.data = {**self.data, 'gpu_state': 'error', 'gpu_error': 'GPU telemetry sample failed'}
        return dict(self.data)
