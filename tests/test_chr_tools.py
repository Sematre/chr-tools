import json
import struct
import subprocess
from pathlib import Path

import pytest

from chr_tools.bootstrap import bootstrap
from chr_tools.info import (
    Image,
    decode_device_mode,
    describe,
    encode_device_mode,
    main,
    printable,
    tool,
)
from chr_tools.npk import parse_package


def npk(
    name: str, version=(7, 24, 5), stage=b"f", architecture=b"arm64", channel=b"stable"
) -> bytes:
    major, minor, patch = version
    header = name.encode().ljust(16, b"\0") + bytes([patch]) + stage + bytes([minor, major])
    parts = [(1, header + struct.pack("<I", 1790691687)), (16, architecture)]
    parts += [(24, channel)] if channel else []
    body = b"".join(struct.pack("<HI", kind, len(data)) + data for kind, data in parts)
    return b"\x1e\xf1\xd0\xba" + struct.pack("<I", len(body)) + body


@pytest.fixture
def image(tmp_path: Path) -> Path:
    """A minimal CHR-like image: ext4 on MBR partition 2, holding a system package."""
    try:
        mke2fs = tool("mke2fs")
    except RuntimeError:
        pytest.skip("needs e2fsprogs")
    root = tmp_path / "root"
    (root / "rw").mkdir(parents=True)
    (root / "var/pdb/system").mkdir(parents=True)
    (root / "var/pdb/system/image").write_bytes(npk("system"))
    partition = bytes([0, 0, 0, 0, 0x83, 0, 0, 0]) + struct.pack("<II", 2048, 16384)
    mbr = bytes(462) + partition + bytes(32) + b"\x55\xaa"
    path = tmp_path / "chr.img"
    path.write_bytes(mbr.ljust(9 << 20, b"\0"))
    subprocess.run(
        [mke2fs, "-qF", "-t", "ext4", "-d", root, "-E", "offset=1048576", path, "8M"], check=True
    )
    return path


@pytest.mark.parametrize(
    ("patch", "stage", "version"),
    [(5, b"f", "7.24.5"), (0, b"f", "7.24"), (3, b"c", "7.24rc3"), (3, b"b", "7.24beta3")],
)
def test_package_version(patch: int, stage: bytes, version: str) -> None:
    assert parse_package(npk("container", (7, 24, patch), stage)).version == version


def test_package_fields() -> None:
    package = parse_package(npk("container", architecture=b"i386", channel=None))
    assert (package.name, package.architecture, package.channel) == ("container", "x86", None)
    with pytest.raises(ValueError, match="not an NPK package"):
        parse_package(b"/system/identity/set name=router")


def test_device_mode_encoding() -> None:
    settings = {"container": "yes", "mode": "advanced", "zerotier": "no"}
    message = encode_device_mode(settings)
    assert message == bytes.fromhex("4d32010000290f4d321c0000010a000009001b000000")
    assert decode_device_mode(message) == settings


def test_bootstrap(image: Path, tmp_path: Path) -> None:
    script = tmp_path / "first-boot.rsc"
    script.write_text("/system/identity/set name=router\n")
    package = tmp_path / "container.npk"
    package.write_bytes(npk("container"))
    output = tmp_path / "out.img"
    original = image.read_bytes()

    def run() -> None:
        settings = {"mode": "rose", "container": "yes"}
        bootstrap(image, output, script=script, settings=settings, packages=[package])

    run()
    info = describe(Image(output))
    assert (info.booted, info.packages) == (False, ["container", "system"])
    assert info.device_mode == {"container": "yes", "mode": "rose"}
    assert info.first_boot_script == script.read_bytes()
    assert image.read_bytes() == original

    first = output.read_bytes()
    run()
    assert output.read_bytes() == first


def test_script_output(image: Path, tmp_path: Path, capsysbinary: pytest.CaptureFixture) -> None:
    script = tmp_path / "first-boot.rsc"
    script.write_bytes(b"/system/identity/set name=router\r\n:log info caf\xc3\xa9\n")
    output = tmp_path / "out.img"
    bootstrap(image, output, script=script)

    main([str(output), "--script"])
    assert capsysbinary.readouterr().out == script.read_bytes()
    main([str(output), "--json"])
    text = json.loads(capsysbinary.readouterr().out)["first_boot_script"]
    assert text == script.read_bytes().decode()
    with pytest.raises(SystemExit, match="has no first-boot script"):
        main([str(image), "--script"])


def test_printable() -> None:
    assert printable(b"ok\t\x1b[2J\r\n\xc2\x9b\xff") == "ok\t\\x1b[2J\\x0d\n\\x9b\ufffd"


def test_dry_run(image: Path, tmp_path: Path) -> None:
    commands = bootstrap(image, tmp_path / "out.img", settings={"container": "yes"}, dry_run=True)
    assert "write rw/rosmode.msg /rw/rosmode.msg\n" in commands
    assert not (tmp_path / "out.img").exists()


def test_rejects_package_of_other_version(image: Path, tmp_path: Path) -> None:
    package = tmp_path / "container.npk"
    package.write_bytes(npk("container", (7, 25, 3), b"c"))
    with pytest.raises(ValueError, match=r"7\.25rc3 arm64, the image is 7\.24\.5 arm64"):
        bootstrap(image, tmp_path / "out.img", packages=[package])
    assert not list(tmp_path.glob("*out.img*"))


def test_rejects_booted_image(image: Path, tmp_path: Path) -> None:
    Image(image).debugfs("-w", "-R", "mkdir /rw/store")
    with pytest.raises(ValueError, match="already booted"):
        bootstrap(image, tmp_path / "out.img", settings={"container": "yes"})
