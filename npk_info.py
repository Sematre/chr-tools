#!/usr/bin/env python3
"""Show the name, version and architecture of RouterOS .npk packages."""

import argparse
import json
import struct
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

# A package is the magic number and a size, followed by parts: a u16 type, a u32 size and the
# payload. All parts except the large payloads fit into the first 4 KiB.
MAGIC = b"\x1e\xf1\xd0\xba"
HEADER_SIZE = 4096
HEADER, DESCRIPTION, ARCHITECTURE, CHANNEL = 1, 2, 16, 24
STAGES = {b"c": "rc", b"b": "beta"}  # f marks a final release
ARCHITECTURES = {"i386": "x86"}  # the names MikroTik uses for its downloads


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    architecture: str
    channel: str | None  # testing and development releases have none
    built: datetime
    description: str

    def __str__(self) -> str:
        channel = f" ({self.channel})" if self.channel else ""
        built = f"{self.built:%Y-%m-%d %H:%M} UTC"
        return f"{self.name} {self.version}{channel} {self.architecture}, built {built}"


def parse_package(data: bytes) -> Package:
    if not data.startswith(MAGIC):
        raise ValueError("not an NPK package")
    header = data[:HEADER_SIZE]
    parts: dict[int, bytes] = {}
    offset = 8
    while offset + 6 <= len(header):
        kind, size = struct.unpack_from("<HI", header, offset)
        if offset + 6 + size > len(header):
            break
        parts.setdefault(kind, header[offset + 6 : offset + 6 + size])
        offset += 6 + size
    if HEADER not in parts or ARCHITECTURE not in parts:
        raise ValueError("incomplete NPK header")

    name, patch, stage, minor, major, built = struct.unpack_from("<16sBcBBI", parts[HEADER])
    if stage == b"f":
        version = f"{major}.{minor}.{patch}" if patch else f"{major}.{minor}"
    else:
        version = f"{major}.{minor}{STAGES.get(stage, stage.decode())}{patch}"
    architecture = parts[ARCHITECTURE].decode()
    return Package(
        name=name.rstrip(b"\0").decode(),
        version=version,
        architecture=ARCHITECTURES.get(architecture, architecture),
        channel=parts[CHANNEL].decode() if CHANNEL in parts else None,
        built=datetime.fromtimestamp(built, UTC),
        description=parts.get(DESCRIPTION, b"").decode(errors="replace"),
    )


def read_package(path: Path) -> Package:
    with open(path, "rb") as file:
        try:
            return parse_package(file.read(HEADER_SIZE))
        except ValueError as error:
            raise ValueError(f"{path}: {error}") from None


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages", nargs="+", type=Path, metavar="PACKAGE")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    try:
        packages = [read_package(path) for path in args.packages]
    except (OSError, ValueError) as error:
        sys.exit(f"{parser.prog}: {error}")

    if args.json:
        records = [asdict(package) for package in packages]
        print(json.dumps(records, default=datetime.isoformat, indent=2))
    else:
        for package in packages:
            print(package, package.description, sep="\n  ")


if __name__ == "__main__":
    main()
