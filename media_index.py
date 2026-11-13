"""Read Matroska Cues using bounded, seekable source reads (ported from Tor)."""

def ebml_vint(buf, p, marker):


    first, n, mask = buf[p], 1, 0x80
    while n <= 8 and not first & mask:
        mask, n = mask >> 1, n + 1
    v = first if marker else first & (mask - 1)
    for i in range(1, n):
        v = v << 8 | buf[p + i]
    return v, n, not marker and v == (1 << 7 * n) - 1


def ebml(buf, pos, end):

    while pos < end - 1:
        eid, a, _ = ebml_vint(buf, pos, True)
        size, b, unknown = ebml_vint(buf, pos + a, False)
        data = pos + a + b
        yield eid, data, end if unknown else min(end, data + size)
        if unknown:
            return
        pos = data + size


class NoIndex(Exception):
    """A Matroska file without the keyframe index (Cues) the cuts come from."""


class SourceBroken(Exception):
    """The source index points beyond the available file."""


def keyframes(source):


    head = source.read_range(0, 256 * 1024)
    seg = next((a for eid, a, _ in ebml(head, 0, len(head)) if eid == 0x18538067), None)
    if seg is None:
        raise NoIndex("not a Matroska file")
    where, heads, seen = {}, [seg], set()
    while heads:
        at = heads.pop()
        if at in seen or len(seen) > 4:
            continue
        seen.add(at)
        buf, off = (head, at) if at + 65536 <= len(head) else (source.read_range(at, 65536), 0)
        for eid, a, b in ebml(buf, off, len(buf)):
            if eid == 0x1F43B675:
                break
            if eid != 0x114D9B74:
                continue
            for sid, sa, sb in ebml(buf, a, b):
                if sid == 0x4DBB:
                    target = p = None
                    for x, xa, xb in ebml(buf, sa, sb):
                        if x == 0x53AB:
                            target = int.from_bytes(buf[xa:xb], "big")
                        elif x == 0x53AC:
                            p = seg + int.from_bytes(buf[xa:xb], "big")
                    if target == 0x114D9B74 and p is not None:
                        heads.append(p)
                    elif target is not None and p is not None:
                        where.setdefault(target, p)
            break

    def element(eid, cap=32 * 2**20):

        if eid not in where:
            raise NoIndex(f"no {eid:x} in the SeekHead")
        size = source.size()
        if where[eid] >= size:
            raise SourceBroken(f"the file is cut short: {size / 1e9:.1f} GB of it, its index at {where[eid] / 1e9:.1f} GB")
        hdr = source.read_range(where[eid], 16)
        _, a, _ = ebml_vint(hdr, 0, True)
        size, b, unknown = ebml_vint(hdr, a, False)
        return source.read_range(where[eid] + a + b, cap if unknown else min(size, cap))

    def uint(buf, a, b):
        return int.from_bytes(buf[a:b], "big")
    info = element(0x1549A966, 65536)
    scale = next((uint(info, a, b) for eid, a, b in ebml(info, 0, len(info)) if eid == 0x2AD7B1), 1000000)
    tracks = element(0x1654AE6B, 2**20)
    video = None
    for eid, a, b in ebml(tracks, 0, len(tracks)):
        if eid == 0xAE:
            f = {x: uint(tracks, xa, xb) for x, xa, xb in ebml(tracks, a, b) if x in (0xD7, 0x83)}
            if f.get(0x83) == 1:
                video = f.get(0xD7)
                break
    cues = element(0x1C53BB6B)
    kf = []
    for eid, a, b in ebml(cues, 0, len(cues)):
        if eid != 0xBB:
            continue
        t, places = None, []
        for x, xa, xb in ebml(cues, a, b):
            if x == 0xB3:
                t = uint(cues, xa, xb)
            elif x == 0xB7:
                f = {y: uint(cues, ya, yb) for y, ya, yb in ebml(cues, xa, xb) if y in (0xF7, 0xF1, 0xF0)}
                places.append(f)
        hit = next((f for f in places if f.get(0xF7) == video and 0xF1 in f), None)
        if t is not None and hit:
            kf.append((t * scale / 1e9, seg + hit[0xF1], hit.get(0xF0)))
    if len(kf) < 2:
        raise NoIndex(f"{len(kf)} keyframes in the index")
    return sorted(kf)

