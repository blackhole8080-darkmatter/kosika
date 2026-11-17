"""Ed25519 signatures, in one readable file.

A pack says what a program is; a signature says who made it. Doing that with
a dependency would put a cryptographic stack between a reader and sixty lines
of arithmetic, so this is RFC 8032's reference construction: the twisted
Edwards curve, SHA-512, and nothing else from the standard library.

The one thing this module promises is what the RFC's test vectors check:
signing the same message with the same seed twice gives the same signature,
and a signature made by one key verifies under that key and no other.

    seed = secrets.token_bytes(32)          # the private half, never shared
    public = public_key(seed)               # 32 bytes, travels with a signature
    made = sign(seed, b"a thing said")      # 64 bytes
    assert verify(public, b"a thing said", made)

`verify` returns False for every malformed input rather than raising: a pack
that carries nonsense in its signature field is a pack that does not verify,
and the caller already reports that in the same words.
"""

from __future__ import annotations

import hashlib

#: The curve's field prime, 2**255 - 19.
Q = 2**255 - 19
#: The order of the base point, 2**252 + 27742317777372353535851937790883648493.
L = 2**252 + 27742317777372353535851937790883648493
#: -121665/121666, the curve's d.
_D = -121665 * pow(121666, Q - 2, Q) % Q
#: A square root of -1, for the one case where the first root is wrong.
_SQRT_M1 = pow(2, (Q - 1) // 4, Q)

#: A point in extended coordinates: x, y, z, and x*y/z.
Point = tuple[int, int, int, int]


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _recover_x(y: int, sign: int) -> int | None:
    """The x a compressed point's y implies, or None if y is not on the curve."""

    if y >= Q:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, Q - 2, Q) % Q
    if x2 == 0:
        return 0 if sign == 0 else None
    x = pow(x2, (Q + 3) // 8, Q)
    if (x * x - x2) % Q != 0:
        x = x * _SQRT_M1 % Q
    if (x * x - x2) % Q != 0:
        return None
    if (x & 1) != sign:
        x = Q - x
    return x


_G_Y = 4 * pow(5, Q - 2, Q) % Q
_G_X = _recover_x(_G_Y, 0)
#: The base point, which every public key is a multiple of.
_G: Point = (int(_G_X or 0), _G_Y, 1, int(_G_X or 0) * _G_Y % Q)


def _add(left: Point, right: Point) -> Point:
    a = (left[1] - left[0]) * (right[1] - right[0]) % Q
    b = (left[1] + left[0]) * (right[1] + right[0]) % Q
    c = 2 * left[3] * right[3] * _D % Q
    d = 2 * left[2] * right[2] % Q
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _multiply(by: int, point: Point) -> Point:
    result: Point = (0, 1, 1, 0)
    while by > 0:
        if by & 1:
            result = _add(result, point)
        point = _add(point, point)
        by >>= 1
    return result


def _equal(left: Point, right: Point) -> bool:
    if (left[0] * right[2] - right[0] * left[2]) % Q != 0:
        return False
    return (left[1] * right[2] - right[1] * left[2]) % Q == 0


def _compress(point: Point) -> bytes:
    inverse = pow(point[2], Q - 2, Q)
    x = point[0] * inverse % Q
    y = point[1] * inverse % Q
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(compressed: bytes) -> Point | None:
    if len(compressed) != 32:
        return None
    y = int.from_bytes(compressed, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % Q)


def _expand(seed: bytes) -> tuple[int, bytes]:
    """The scalar and the nonce prefix a 32-byte seed stands for."""

    digest = _sha512(seed)
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def _mod_l(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little") % L


def public_key(seed: bytes) -> bytes:
    """The 32 bytes a signature by `seed` verifies under."""

    if len(seed) != 32:
        raise ValueError("An Ed25519 seed is 32 bytes")
    scalar, _ = _expand(seed)
    return _compress(_multiply(scalar, _G))


def sign(seed: bytes, message: bytes) -> bytes:
    """The 64-byte signature of `message` made by `seed`."""

    if len(seed) != 32:
        raise ValueError("An Ed25519 seed is 32 bytes")
    scalar, prefix = _expand(seed)
    public = _compress(_multiply(scalar, _G))
    nonce = _mod_l(prefix + message)
    made = _compress(_multiply(nonce, _G))
    challenge = _mod_l(made + public + message)
    return made + ((nonce + challenge * scalar) % L).to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether `signature` is `message` signed by the key `public`."""

    if len(public) != 32 or len(signature) != 64:
        return False
    key = _decompress(public)
    if key is None:
        return False
    made = _decompress(signature[:32])
    if made is None:
        return False
    scalar = int.from_bytes(signature[32:], "little")
    if scalar >= L:
        return False
    challenge = _mod_l(signature[:32] + public + message)
    return _equal(_multiply(scalar, _G), _add(made, _multiply(challenge, key)))
