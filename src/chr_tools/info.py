"""Show what a RouterOS CHR image contains: version, packages, device-mode and first-boot script."""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from chr_tools.npk import parse_package

SYSTEM = "/var/pdb/system/image"  # an installed package is its .npk at /var/pdb/NAME/image
STORE = "/rw/store"  # created by the first boot
AUTORUN = "/rw/autorun.scr"  # runs on the first boot
ROSMODE = "/rw/rosmode.msg"  # device-mode

# /rw/rosmode.msg is a MikroTik M2 message: "M2" followed by fields. Each field starts with a
# little-endian u32 key, the field id in its low 24 bits and the value type in its high 8 bits.
# Booleans keep their value in the type, short types keep their value or length in one byte.
M2_FALSE, M2_TRUE, M2_U32_SHORT, M2_MESSAGE_SHORT = 0x00, 0x01, 0x09, 0x29
ACTIVE, MODE = 0x01, 0x0A  # message 0x02 would be a pending /system/device-mode/update
MODES = {"advanced": 0, "home": 1, "basic": 2, "rose": 3}
PROPERTIES = {  # in the order of /system/device-mode/print
    "flagged": 0x0B, "flagging-enabled": 0x0C, "scheduler": 0x0D, "socks": 0x0E, "fetch": 0x0F,
    "pptp": 0x10, "l2tp": 0x11, "bandwidth-test": 0x12, "traffic-gen": 0x13, "sniffer": 0x14,
    "ipsec": 0x15, "romon": 0x16, "proxy": 0x17, "hotspot": 0x18, "smb": 0x19, "email": 0x1A,
    "zerotier": 0x1B, "container": 0x1C, "install-any-version": 0x1D, "partitions": 0x1E,
    "routerboard": 0x1F,
}  # fmt: skip


def m2_field(field_id: int, kind: int, value: bytes = b"") -> bytes:
    return struct.pack("<I", field_id | kind << 24) + value


def m2_fields(message: bytes) -> Iterator[tuple[int, int, bytes]]:
    """Yields id, type and value of each field, for the types that device-mode uses."""
    if not message.startswith(b"M2"):
        raise ValueError("not an M2 message")
    offset = 2
    while offset < len(message):
        (key,) = struct.unpack_from("<I", message, offset)
        kind, start = key >> 24, offset + 4
        if kind in (M2_FALSE, M2_TRUE):
            offset = start
        elif kind == M2_U32_SHORT:
            offset = start + 1
        elif kind == M2_MESSAGE_SHORT:
            start, offset = start + 1, start + 1 + message[start]
        else:
            raise ValueError(f"unsupported M2 field type {kind:#04x}")
        yield key & 0xFFFFFF, kind, message[start:offset]


def encode_device_mode(settings: dict[str, str]) -> bytes:
    """Encodes settings such as {"mode": "rose", "container": "yes"}, sorted for a stable result."""
    fields = b"".join(
        m2_field(MODE, M2_U32_SHORT, bytes([MODES[value]]))
        if name == "mode"
        else m2_field(PROPERTIES[name], M2_TRUE if value == "yes" else M2_FALSE)
        for name, value in sorted(settings.items())
    )
    inner = b"M2" + fields
    return b"M2" + m2_field(ACTIVE, M2_MESSAGE_SHORT, bytes([len(inner)]) + inner)


def decode_device_mode(message: bytes) -> dict[str, str]:
    names = {field_id: name for name, field_id in PROPERTIES.items()}
    modes = {number: name for name, number in MODES.items()}
    settings = {}
    for field_id, _, value in m2_fields(message):
        if field_id != ACTIVE:
            continue
        for setting, kind, data in m2_fields(value):
            if setting == MODE:
                settings["mode"] = modes.get(data[0], str(data[0]))
            else:
                settings[names.get(setting, f"{setting:#04x}")] = "yes" if kind else "no"
    return dict(sorted(settings.items()))


def printable(script: bytes) -> str:
    """Shows control characters other than tab and newline as \\xNN, so that a script can't send
    escape sequences to the terminal."""
    text = script.decode(errors="replace")
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", lambda match: f"\\x{ord(match[0]):02x}", text)


def to_json(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def tool(name: str) -> str:
    """Finds an e2fsprogs tool, also in the sbin directories that PATH often lacks."""
    path = shutil.which(name, path=f"{os.environ.get('PATH', '')}:/usr/sbin:/sbin")
    if not path:
        raise RuntimeError(f"{name} not found, install e2fsprogs")
    return path


def copy_raw(source: Path, target: Path) -> None:
    """Copies a raw image, or extracts it from a .zip as MikroTik publishes it."""
    if source.suffix != ".zip":
        shutil.copyfile(source, target)
        return
    with zipfile.ZipFile(source) as archive:
        images = [name for name in archive.namelist() if name.endswith(".img")]
        if len(images) != 1:
            raise ValueError(f"{source}: expected one .img file in the archive")
        with archive.open(images[0]) as data, open(target, "wb") as file:
            shutil.copyfileobj(data, file)


class Image:
    """The RouterOS filesystem on partition 2 of a raw CHR image, accessed with debugfs."""

    def __init__(self, path: Path, name: Path | None = None) -> None:
        """NAME is the file the image came from, for messages."""
        with open(path, "rb") as file:
            mbr = file.read(512)
        # The start sector of the second MBR entry, which x86 and arm64 images share with their GPT.
        start = struct.unpack_from("<I", mbr, 446 + 16 + 8)[0] if mbr[510:] == b"\x55\xaa" else 0
        self.device = f"{path.resolve()}?offset={start * 512}"
        self.debugfs_path = tool("debugfs")
        if not start or not self.exists(SYSTEM):
            raise ValueError(f"{name or path} is not a RouterOS CHR image")

    def debugfs(self, *args: str, **kwargs) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [self.debugfs_path, *args, self.device], check=False, capture_output=True, **kwargs
        )

    def query(self, request: str) -> str:
        return self.debugfs("-R", request).stdout.decode()

    def read(self, path: str) -> bytes:
        return self.debugfs("-R", f"cat {path}").stdout

    def exists(self, path: str) -> bool:
        return bool(self.query(f"stat {path}"))

    def mtime(self, path: str) -> int:
        return int(re.findall(r"mtime: 0x([0-9a-f]+)", self.query(f"stat {path}"))[0], 16)

    def listdir(self, path: str) -> list[str]:
        names = [line.split("/")[5] for line in self.query(f"ls -p {path}").splitlines() if line]
        return sorted(name for name in names if name not in (".", ".."))

    def check(self) -> None:
        fsck = subprocess.run(
            [tool("e2fsck"), "-fn", self.device], check=False, capture_output=True, text=True
        )
        if fsck.returncode:
            raise RuntimeError(f"e2fsck found errors after writing:\n{fsck.stdout}")


@dataclass(frozen=True)
class ImageInfo:
    version: str
    architecture: str
    channel: str | None
    built: datetime
    booted: bool
    packages: list[str]
    device_mode: dict[str, str]
    first_boot_script: bytes | None
    free_bytes: int


def describe(image: Image) -> ImageInfo:
    system = parse_package(image.read(SYSTEM))
    stats = dict(re.findall(r"^(Free blocks|Block size): +(\d+)$", image.query("stats -h"), re.M))
    return ImageInfo(
        version=system.version,
        architecture=system.architecture,
        channel=system.channel,
        built=system.built,
        booted=image.exists(STORE),
        packages=image.listdir("/var/pdb"),
        device_mode=decode_device_mode(image.read(ROSMODE)) if image.exists(ROSMODE) else {},
        first_boot_script=image.read(AUTORUN) if image.exists(AUTORUN) else None,
        free_bytes=int(stats["Free blocks"]) * int(stats["Block size"]),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="raw CHR image, or MikroTik's .img.zip")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print JSON")
    output.add_argument("--script", action="store_true", help="print only the first-boot script")
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory() as temp:
            raw = args.image
            if raw.suffix == ".zip":
                raw = Path(temp, "chr.img")
                copy_raw(args.image, raw)
            info = describe(Image(raw, name=args.image))
    except (OSError, ValueError, RuntimeError) as error:
        sys.exit(f"{parser.prog}: {error}")

    script = info.first_boot_script
    if args.script:
        if script is None:
            sys.exit(f"{parser.prog}: {args.image} has no first-boot script")
        # Unchanged for files and pipes, so that it can go back into chr-bootstrap --script.
        if sys.stdout.isatty():
            print(printable(script), end="")
        else:
            sys.stdout.buffer.write(script)
        return
    if args.json:
        print(json.dumps(asdict(info), default=to_json, indent=2))
        return
    channel = f" ({info.channel})" if info.channel else ""
    settings = " ".join(f"{name}={value}" for name, value in info.device_mode.items())
    built = f"{info.built:%Y-%m-%d %H:%M} UTC"
    rows = {
        "RouterOS": f"{info.version}{channel} {info.architecture}, built {built}",
        "State": "booted" if info.booted else "never booted",
        "Packages": " ".join(info.packages),
        "Device-mode": settings or "RouterOS defaults",
        "First boot": "no script" if script is None else f"{len(script)} byte script, see --script",
        "Free space": f"{info.free_bytes / 2**20:.1f} MiB",
    }
    for label, value in rows.items():
        print(f"{label:<12} {value}")


if __name__ == "__main__":
    main()
