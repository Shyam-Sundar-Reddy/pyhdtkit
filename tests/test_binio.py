from pyhdtkit.hdt.binio import (
    crc8,
    crc16,
    crc32c,
    pack_lsb_bitfields,
    unpack_lsb_bitfields,
    vbyte_decode,
    vbyte_encode,
)


def test_vbyte_round_trips() -> None:
    for value in [0, 1, 43, 127, 128, 614, 65535, 1_000_000]:
        encoded = vbyte_encode(value)
        decoded, pos = vbyte_decode(encoded, 0)
        assert decoded == value
        assert pos == len(encoded)


def test_vbyte_matches_known_fixture_bytes() -> None:
    # numstrings=43 (0xab, single byte, high bit already set)
    assert vbyte_decode(bytes([0xAB]), 0) == (43, 1)
    # bytes=614 across two bytes (0x66, 0x84)
    assert vbyte_decode(bytes([0x66, 0x84]), 0) == (614, 2)
    # blocksize=16 (0x90, single byte)
    assert vbyte_decode(bytes([0x90]), 0) == (16, 1)


def test_crc_checksums_match_known_fixture_values() -> None:
    # PFC section header [type=2, numstrings=43, bytes=614, blocksize=16]
    assert crc8(bytes.fromhex("02ab668490")) == 0x12
    # Global control info chunk, CRC16/ARC
    global_chunk = (
        b"$HDT\x01<http://purl.org/HDT/hdt#HDTv1>\x00\x00"
    )
    assert crc16(global_chunk) == 0x3576
    # Block-pointer-array data [00 a4 b3 9c 99], CRC-32C
    assert crc32c(bytes.fromhex("00a4b39c99")) == 0xB6B8251B


def test_lsb_bitfield_pack_unpack_round_trips() -> None:
    values = [0, 233, 459, 614]
    packed = pack_lsb_bitfields(values, 10)
    assert packed == bytes.fromhex("00a4b39c99")
    assert unpack_lsb_bitfields(packed, 10, 4) == values
