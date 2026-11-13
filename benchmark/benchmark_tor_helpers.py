# Extracted unchanged function definitions from live Tor v91 using AST; no Tor startup code.
def ebml_vint(buf, p, marker):
    first, n, mask = (buf[p], 1, 128)
    while n <= 8 and (not first & mask):
        mask, n = (mask >> 1, n + 1)
    v = first if marker else first & mask - 1
    for i in range(1, n):
        v = v << 8 | buf[p + i]
    return (v, n, not marker and v == (1 << 7 * n) - 1)

def ebml(buf, pos, end):
    while pos < end - 1:
        eid, a, _ = ebml_vint(buf, pos, True)
        size, b, unknown = ebml_vint(buf, pos + a, False)
        data = pos + a + b
        yield (eid, data, end if unknown else min(end, data + size))
        if unknown:
            return
        pos = data + size

class NoIndex(Exception):
    """A Matroska file without the keyframe index (Cues) the cuts come from."""

class SourceBroken(Exception):
    """A source that can't play: a file TorBox has only part of, say."""

def mkv_keyframes(key):
    head = src_read(key, 0, 256 * 1024)
    seg = next((a for eid, a, _ in ebml(head, 0, len(head)) if eid == 408125543), None)
    if seg is None:
        raise NoIndex('not a Matroska file')
    where, heads, seen = ({}, [seg], set())
    while heads:
        at = heads.pop()
        if at in seen or len(seen) > 4:
            continue
        seen.add(at)
        buf, off = (head, at) if at + 65536 <= len(head) else (src_read(key, at, 65536), 0)
        for eid, a, b in ebml(buf, off, len(buf)):
            if eid == 524531317:
                break
            if eid != 290298740:
                continue
            for sid, sa, sb in ebml(buf, a, b):
                if sid == 19899:
                    target = p = None
                    for x, xa, xb in ebml(buf, sa, sb):
                        if x == 21419:
                            target = int.from_bytes(buf[xa:xb], 'big')
                        elif x == 21420:
                            p = seg + int.from_bytes(buf[xa:xb], 'big')
                    if target == 290298740 and p is not None:
                        heads.append(p)
                    elif target is not None and p is not None:
                        where.setdefault(target, p)
            break

    def element(eid, cap=32 * 2 ** 20):
        if eid not in where:
            raise NoIndex(f'no {eid:x} in the SeekHead')
        size = src_lookup(key)['size']
        if where[eid] >= size:
            raise SourceBroken(f'the file is cut short: {size / 1000000000.0:.1f} GB of it, its index at {where[eid] / 1000000000.0:.1f} GB')
        hdr = src_read(key, where[eid], 16)
        _, a, _ = ebml_vint(hdr, 0, True)
        size, b, unknown = ebml_vint(hdr, a, False)
        return src_read(key, where[eid] + a + b, cap if unknown else min(size, cap))

    def uint(buf, a, b):
        return int.from_bytes(buf[a:b], 'big')
    info = element(357149030, 65536)
    scale = next((uint(info, a, b) for eid, a, b in ebml(info, 0, len(info)) if eid == 2807729), 1000000)
    tracks = element(374648427, 2 ** 20)
    video = None
    for eid, a, b in ebml(tracks, 0, len(tracks)):
        if eid == 174:
            f = {x: uint(tracks, xa, xb) for x, xa, xb in ebml(tracks, a, b) if x in (215, 131)}
            if f.get(131) == 1:
                video = f.get(215)
                break
    cues = element(475249515)
    kf = []
    for eid, a, b in ebml(cues, 0, len(cues)):
        if eid != 187:
            continue
        t, places = (None, [])
        for x, xa, xb in ebml(cues, a, b):
            if x == 179:
                t = uint(cues, xa, xb)
            elif x == 183:
                f = {y: uint(cues, ya, yb) for y, ya, yb in ebml(cues, xa, xb) if y in (247, 241, 240)}
                places.append(f)
        hit = next((f for f in places if f.get(247) == video and 241 in f), None)
        if t is not None and hit:
            kf.append((t * scale / 1000000000.0, seg + hit[241], hit.get(240)))
    if len(kf) < 2:
        raise NoIndex(f'{len(kf)} keyframes in the index')
    return sorted(kf)

def keyframe_cuts(kf, start, duration):
    kf = [(t - start, p, r) for t, p, r in kf if 0 <= t - start < duration]
    if not kf or kf[0][0] >= 0.5:
        return (None, None, None)
    cuts, pos, aligned = ([0.0], [kf[0][1]], [kf[0][2] is not None and kf[0][2] < 64])
    for t, p, r in kf[1:]:
        if t - cuts[-1] >= PIECE_S - 0.05:
            cuts.append(round(t, 3))
            pos.append(p)
            aligned.append(r is not None and r < 64)
    if duration - cuts[-1] < 0.5 and len(cuts) > 1:
        cuts.pop()
        pos.pop()
        aligned.pop()
    return (cuts + [duration], pos, aligned)

def piece_cuts(kf, start, duration):
    near = sorted(((t - start, p, r) for t, p, r in kf if 0 <= t - start < duration)) if kf else []
    if near:
        gaps = sorted((b[0] - a[0] for a, b in zip(near, near[1:])))
        if gaps and gaps[len(gaps) // 2] <= 2 * PIECE_S:
            cuts, pos, aligned = keyframe_cuts(kf, start, duration)
            if cuts:
                return (cuts, pos, aligned, None)
    n = max(1, round(duration / PIECE_S))
    cuts = [round(i * PIECE_S, 3) for i in range(n)] + [duration]
    if not near:
        return (cuts, None, None, None)
    pos, aligned, pend, j, e = ([], [], [], 0, 0)
    for i in range(n):
        while j + 1 < len(near) and near[j + 1][0] <= cuts[i] + 0.002:
            j += 1
        while e < len(near) and near[e][0] < cuts[i + 1] - 0.002:
            e += 1
        t, p, r = near[j]
        pos.append(p)
        aligned.append(r is not None and r < 64 and (abs(t - cuts[i]) <= 0.002))
        if e >= len(near):
            pend.append(None)
        elif near[e][2] is not None and near[e][2] < 64:
            pend.append(near[e][1])
        elif e + 1 < len(near):
            span_b, span_t = (near[e + 1][1] - near[e][1], near[e + 1][0] - near[e][0])
            pend.append(near[e][1] + min(span_b, int(span_b * 1.5 / span_t) if span_t > 0 else span_b) + 2 ** 16)
        else:
            pend.append(None)
    return (cuts, pos, aligned, pend)

def piece_span(key, m, orig=False):
    info = probed[key]
    pos, aligned = (info['opos'], info.get('oaligned') or []) if orig else (info['pos'], info.get('aligned') or [])
    size = src_meta[key].get('size') or src_lookup(key)['size']
    pend = None if orig else info.get('pend')
    if pend:
        e = pend[m] if m < len(pend) else None
        return (pos[m], e if e is not None and e > pos[m] else min(size, pos[m] + 96 * 2 ** 20), True)
    if m + 1 >= len(pos):
        return (pos[m], min(size, pos[m] + 96 * 2 ** 20), False)
    if m + 1 < len(aligned) and aligned[m + 1]:
        return (pos[m], pos[m + 1], False)
    return (pos[m], pos[m + 2] if m + 2 < len(pos) else min(size, pos[m + 1] + 32 * 2 ** 20), True)

def piece_cut(key, m, orig=False):
    info = probed[key]
    cuts, pos = (info['ocuts'], info['opos']) if orig else (info['cuts'], info['pos'])
    return (cuts[m], round(cuts[m + 1] - cuts[m], 3), pos[m] if pos else None, pos[m + 1] if pos and m + 1 < len(pos) else None)

def muxed(info):
    return info['a'] if MUXED else -1

def mac_body(key, m, last=None):
    info = probed[key]
    last = m if last is None else last
    pb, pe, cut = piece_span(key, m)
    if last != m:
        _, pe, cut = piece_span(key, last)
    head = src_head(key, info['pos'][0], m)
    unit, tracks = mkv_head(head)
    video = next((num for num, kind in tracks if kind == 1), None)
    numbers, order = ([num for num, kind in tracks if kind == 2], [x['i'] for x in info['audios']])
    audio = numbers[order.index(info['a'])] if muxed(info) >= 0 and info['a'] in order and (len(numbers) == len(order)) else None
    t, (lt, ll) = (piece_cut(key, m)[0], piece_cut(key, last)[:2])
    begin, end = ((info['start'] + t - 0.002) / unit, (info['start'] + lt + ll + 0.002) / unit)
    hevc, nal = (info.get('vcodec') == 'hevc', info.get('vcodec') in ('h264', 'hevc'))
    more = last + 1 < len(info['pos'])
    full = pe - pb + (min((pe - pb) // 2 + 2 ** 16, -pe % SRC_BLOCK) if more else 0)
    late = max(1.0, 20 / (info.get('fps') or 24)) / unit
    span = mkv_until(key, pb, full, m, video, end + late) if info.get('pend') else src_read(key, pb, full, m)
    while True:
        blocks = [b for b in mkv_blocks(span, 0, len(span)) if b[1] is not None and b[0] in (video, audio)]
        if blocks:
            _, x, _ = ebml_vint(span, blocks[-1][2], True)
            size, y, _ = ebml_vint(span, blocks[-1][2] + x, False)
            if blocks[-1][2] + x + y + size > len(span):
                blocks.pop()
        vids = [i for i, b in enumerate(blocks) if b[0] == video]
        shown = [i for i in vids if blocks[i][1] < end]
        if not shown:
            raise RuntimeError(f'piece {m}: no blocks of its video in its bytes')
        first, stop = (max([i for i in vids if blocks[i][1] <= begin + 0.004 / unit and mkv_key(span, *blocks[i][2:5])] or vids[:1]), shown[-1])
        pics = lambda i: mkv_pics(span, blocks[i][4] + 3, blocks[i][5], hevc) if nal and blocks[i][6] == 1 else None
        z = vids[-1]
        nk = next((pics(i) for i in vids if blocks[i][1] >= end - 0.004 / unit and mkv_key(span, *blocks[i][2:5])), None)
        if not more or (blocks[z][1] >= end and (not mkv_key(span, *blocks[z][2:5]))) or (nk and all((p[0] in ((18, 20) if hevc else (5,)) for p in nk))):
            break
        more = False
        span += src_read(key, pb + len(span), min((pe - pb) // 2, 8 * 2 ** 20) + 2 ** 16, m)
    k0, seen = (blocks[first][1], {i: pics(i) for i in vids if first <= i <= stop and (hevc or blocks[i][1] < begin)})
    top = max([p[1] for v in seen.values() if v for p in v] or [0]) if None not in seen.values() else 99

    def unneeded(i):
        return blocks[i][1] < begin and bool(seen[i]) and all((p[0] <= 14 and p[0] % 2 == 0 and (p[1] == top) or (6 <= p[0] <= 9 and blocks[i][1] < k0) if hevc else p[1] == 0 for p in seen[i]))
    keep = [b for i, b in enumerate(blocks) if b[0] == video and first <= i <= stop and (not unneeded(i)) or (b[0] == audio and begin - AUDIO_PRE / unit <= b[1] < end + 0.1 / unit)]
    kept = [num for num, kind in tracks if num in (video, audio)]
    return (mac_head(head, kept) + mkv_clusters([(span[b[2]:b[3]], b[4] - b[2], b[1]) for b in keep]), kept.index(audio) if audio in kept else -1)

def mkv_until(key, start, n, piece, track, until):
    size = src_meta[key].get('size') or src_lookup(key)['size']
    land_now(key, piece)
    for nb in range(start // SRC_BLOCK + 1, (min(start + n, size) - 1) // SRC_BLOCK + 1):
        if wanted_block(key, nb):
            threading.Thread(target=quietly, args=(src_block, key, nb, True, piece), daemon=True).start()
    span, step = (b'', max(2 ** 20, n // 8))
    while len(span) < n:
        more = src_read(key, start + len(span), min(n - len(span), step), piece)
        if not more:
            break
        span += more
        for tr, ts, p, _, _, _, _ in mkv_blocks(span, 0, len(span)):
            if tr == track and ts is not None and (ts >= until):
                _, a, _ = ebml_vint(span, p, True)
                length, b, _ = ebml_vint(span, p + a, False)
                if p + a + b + length <= len(span):
                    return span
    return span

def mac_head(head, keep):

    def el(eid, data):
        return eid.to_bytes((eid.bit_length() + 7) // 8, 'big') + (len(data) | 1 << 56).to_bytes(8, 'big') + data
    top = list(ebml(head, 0, len(head)))
    out = [head[:next((b for eid, _, b in top if eid == 440786851))], b'\x18S\x80g\x01' + b'\xff' * 7]
    for eid, a, b in ebml(head, next((a for eid, a, _ in top if eid == 408125543)), len(head)):
        if eid == 524531317:
            break
        if eid == 357149030:
            out.append(el(eid, head[a:b]))
        elif eid == 374648427:
            out.append(el(eid, b''.join((el(174, head[xa:xb]) for x, xa, xb in ebml(head, a, b) if x == 174 and any((y == 215 and int.from_bytes(head[ya:yb], 'big') in keep for y, ya, yb in ebml(head, xa, xb)))))))
    return b''.join(out)

def mkv_pics(buf, p, e, hevc):
    out = []
    while p + 5 < e:
        h = buf[p + 4]
        if h >> 1 & 63 < 32 if hevc else h & 31 in (1, 5):
            out.append((h >> 1 & 63, (buf[p + 5] & 7) - 1) if hevc else (h & 31, h >> 5))
        p += 4 + int.from_bytes(buf[p:p + 4], 'big')
    return out if p == e and out else None

def mkv_head(buf):
    seg = next((a for eid, a, _ in ebml(buf, 0, len(buf)) if eid == 408125543), None)
    unit, tracks = (0.001, [])
    for eid, a, b in ebml(buf, seg or 0, len(buf)):
        if eid == 524531317:
            break
        if eid == 357149030:
            unit = next((int.from_bytes(buf[xa:xb], 'big') / 1000000000.0 for x, xa, xb in ebml(buf, a, b) if x == 2807729), unit)
        elif eid == 374648427:
            for x, xa, xb in ebml(buf, a, b):
                if x == 174:
                    f = {y: int.from_bytes(buf[ya:yb], 'big') for y, ya, yb in ebml(buf, xa, xb) if y in (215, 131)}
                    tracks.append((f.get(215), f.get(131)))
    return (unit, tracks)

def mkv_blocks(buf, p, end):
    ts = None
    while p < end - 2:
        try:
            eid, a, _ = ebml_vint(buf, p, True)
            size, b, unknown = ebml_vint(buf, p + a, False)
        except IndexError:
            return
        data = p + a + b
        stop = end if unknown else min(end, data + size)
        if eid == 524531317:
            ts, p = (None, data)
            continue
        if eid == 231:
            ts = int.from_bytes(buf[data:stop], 'big')
        elif eid in (163, 160):
            blk, blk_end = (data, stop) if eid == 163 else next(((xa, xb) for x, xa, xb in ebml(buf, data, stop) if x == 161), (None, None))
            if blk is not None and blk + 4 <= blk_end:
                track, k, _ = ebml_vint(buf, blk, False)
                rel = int.from_bytes(buf[blk + k:blk + k + 2], 'big', signed=True)
                laced = buf[blk + k + 2] & 6 and blk + k + 3 < blk_end
                yield (track, None if ts is None else ts + rel, p, stop, blk + k, blk_end, buf[blk + k + 3] + 1 if laced else 1)
        p = stop

def mkv_key(buf, s, z, r):
    if buf[s] == 163:
        return bool(buf[r + 2] & 128)
    _, b, _ = ebml_vint(buf, s + 1, False)
    return not any((x == 251 for x, _, _ in ebml(buf, s + 1 + b, z)))

def mkv_clusters(blocks):
    out, k = ([], 0)
    while k < len(blocks):
        j = k
        while j < len(blocks) and abs(blocks[j][2] - blocks[k][2]) < 30000:
            j += 1
        out.append(mkv_cluster(blocks[k][2], blocks[k:j]))
        k = j
    return b''.join(out)

def mkv_cluster(ts, blocks):
    out = bytearray(b'\xe7\x88' + ts.to_bytes(8, 'big'))
    for blk, r, t in blocks:
        at = len(out) + r
        out += blk
        out[at:at + 2] = (t - ts).to_bytes(2, 'big', signed=True)
    return b'\x1fC\xb6u' + (len(out) | 1 << 56).to_bytes(8, 'big') + bytes(out)
