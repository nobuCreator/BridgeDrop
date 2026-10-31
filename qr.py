"""Minimal QR code encoder (byte mode, ECC level M, versions 1-6). Standard library only."""

# version: (total codewords, EC codewords per block, block count)
_VERSIONS = {
    1: (26, 10, 1),
    2: (44, 16, 1),
    3: (70, 26, 1),
    4: (100, 18, 2),
    5: (134, 24, 2),
    6: (172, 16, 4),
}
_ALIGN = {2: 18, 3: 22, 4: 26, 5: 30, 6: 34}

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_encode(data, ec_len):
    gen = [1]
    for i in range(ec_len):
        next_gen = [0] * (len(gen) + 1)
        for j, coef in enumerate(gen):
            next_gen[j] ^= coef
            next_gen[j + 1] ^= _mul(coef, _EXP[i])
        gen = next_gen
    rem = [0] * ec_len
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(ec_len):
            rem[i] ^= _mul(gen[i + 1], factor)
    return rem


def _codewords(data, version):
    total, ec_len, blocks = _VERSIONS[version]
    capacity = total - ec_len * blocks
    if len(data) + 2 > capacity:
        raise ValueError("data too long")

    bits = [0, 1, 0, 0]
    bits += [(len(data) >> i) & 1 for i in range(7, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)

    payload = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(payload) < capacity:
        payload.append(pad)
        pad = 0x11 if pad == 0xEC else 0xEC

    block_len = capacity // blocks
    data_blocks = [payload[i * block_len:(i + 1) * block_len] for i in range(blocks)]
    ec_blocks = [_rs_encode(block, ec_len) for block in data_blocks]
    out = [block[i] for i in range(block_len) for block in data_blocks]
    out += [block[i] for i in range(ec_len) for block in ec_blocks]
    return out


def _mask_bit(mask, x, y):
    return [
        (x + y) % 2,
        y % 2,
        x % 3,
        (x + y) % 3,
        (x // 3 + y // 2) % 2,
        x * y % 2 + x * y % 3,
        (x * y % 2 + x * y % 3) % 2,
        ((x + y) % 2 + x * y % 3) % 2,
    ][mask] == 0


def _penalty(m):
    size = len(m)
    score = 0
    lines = m + [[m[y][x] for y in range(size)] for x in range(size)]
    for line in lines:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        if run >= 5:
            score += run - 2
        text = "".join("1" if v else "0" for v in line)
        score += 40 * (text.count("10111010000") + text.count("00001011101"))
    for y in range(size - 1):
        for x in range(size - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    score += abs(dark * 20 // (size * size) - 10) * 10
    return score


def encode(text):
    """Return the QR code for text as a square list of rows of booleans (True = dark)."""
    data = text.encode("utf-8")
    for version, (total, ec_len, blocks) in _VERSIONS.items():
        if len(data) + 2 <= total - ec_len * blocks:
            break
    else:
        raise ValueError("data too long")

    size = version * 4 + 17
    base = [[False] * size for _ in range(size)]
    func = [[False] * size for _ in range(size)]

    def put(x, y, dark):
        base[y][x] = dark
        func[y][x] = True

    for i in range(size):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    if version in _ALIGN:
        c = _ALIGN[version]
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                put(c + dx, c + dy, max(abs(dx), abs(dy)) != 1)
    # Reserve the format areas so data placement skips them.
    for i in range(9):
        func[8][i] = func[i][8] = True
    for i in range(8):
        func[8][size - 1 - i] = func[size - 1 - i][8] = True

    words = _codewords(data, version)
    bits = [(word >> i) & 1 for word in words for i in range(7, -1, -1)]

    def build(mask):
        m = [row[:] for row in base]
        i = 0
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = (right + 1) & 2 == 0
            for vert in range(size):
                y = size - 1 - vert if upward else vert
                for j in range(2):
                    x = right - j
                    if func[y][x]:
                        continue
                    dark = bits[i] == 1 if i < len(bits) else False
                    i += 1
                    m[y][x] = dark ^ _mask_bit(mask, x, y)
            right -= 2

        # Format info: ECC level M is 0b00.
        value = mask
        rem = value
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        fmt = ((value << 10) | rem) ^ 0x5412
        bit = lambda n: (fmt >> n) & 1 == 1
        for n in range(6):
            m[n][8] = bit(n)
        m[7][8] = bit(6)
        m[8][8] = bit(7)
        m[8][7] = bit(8)
        for n in range(9, 15):
            m[8][14 - n] = bit(n)
        for n in range(8):
            m[8][size - 1 - n] = bit(n)
        for n in range(8, 15):
            m[size - 15 + n][8] = bit(n)
        m[size - 8][8] = True
        return m

    return min((build(mask) for mask in range(8)), key=_penalty)


def to_svg(text, border=4):
    matrix = encode(text)
    size = len(matrix) + border * 2
    path = "".join(
        f"M{x + border} {y + border}h1v1h-1z"
        for y, row in enumerate(matrix)
        for x, dark in enumerate(row)
        if dark
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{path}" fill="#000"/></svg>'
    )
