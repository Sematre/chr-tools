#!/usr/bin/env python3
"""Download a RouterOS CHR image and extra packages from MikroTik, verified by their SHA-256 sums.

Files that are already present with the published sum are kept, so running it again only
downloads what is missing.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
from http.client import HTTPResponse
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

DOWNLOADS = "https://download.mikrotik.com/routeros"
NEWEST = "https://upgrade.mikrotik.com/routeros/NEWESTa7.{}"
CHANNELS = ("stable", "long-term", "testing", "development")
VERSION = re.compile(r"\d+\.\d+(\.\d+|(rc|beta)\d+)?")


def get(url: str) -> HTTPResponse:
    try:
        return urlopen(url, timeout=60)
    except HTTPError as error:
        raise OSError(f"{url}: {error.reason}") from None


def resolve(version: str) -> str:
    """Returns VERSION, or the newest version of a release channel."""
    if version in CHANNELS:
        with get(NEWEST.format(version)) as response:
            version = response.read().decode().split()[0]
    if not VERSION.fullmatch(version):
        raise ValueError(f"invalid RouterOS version {version!r}")
    return version


def sha256(path: Path) -> str:
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def published_sha256(url: str) -> str:
    with get(f"{url}.sha256") as response:
        return response.read().decode().split()[0]


def fetch(url: str, expected: str, directory: Path) -> Path:
    """Downloads URL into DIRECTORY, unless a file with the EXPECTED sum is already there."""
    target = directory / url.rsplit("/", 1)[1]
    if target.exists() and sha256(target) == expected:
        return target

    print(f"downloading {url}", file=sys.stderr)
    part = target.with_name(f".{target.name}.part")
    try:
        with get(url) as response, open(part, "wb") as file:
            shutil.copyfileobj(response, file)
        if sha256(part) != expected:
            raise ValueError(f"{target.name} does not match its published SHA-256 sum")
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", help=f"such as 7.24.5, or a channel: {', '.join(CHANNELS)}")
    parser.add_argument(
        "-a", "--architecture", choices=("x86", "arm64"), default="x86", help="default: x86"
    )
    parser.add_argument(
        "-p", "--package", action="append", default=[], metavar="NAME",
        help="extra package, such as container",
    )  # fmt: skip
    parser.add_argument(
        "-d", "--directory", type=Path, default=Path(), help="where to store the files, default: ."
    )
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    for name in args.package:
        if not re.fullmatch(r"[a-z0-9-]+", name):
            parser.error(f"invalid package name {name!r}")

    suffix = "-arm64" if args.architecture == "arm64" else ""
    try:
        version = resolve(args.version)
        names = [f"chr-{version}{suffix}.img.zip"]
        names += [f"{package}-{version}{suffix}.npk" for package in args.package]
        urls = [f"{DOWNLOADS}/{version}/{name}" for name in names]
        sums = [published_sha256(url) for url in urls]  # stops a typo before any large download
        args.directory.mkdir(parents=True, exist_ok=True)
        image, *packages = [
            fetch(url, sum_, args.directory) for url, sum_ in zip(urls, sums, strict=True)
        ]
    except (OSError, ValueError) as error:
        sys.exit(f"{parser.prog}: {error}")

    if args.json:
        result = {
            "version": version,
            "architecture": args.architecture,
            "image": str(image),
            "packages": [str(package) for package in packages],
        }
        print(json.dumps(result, indent=2))
    else:
        print(image, *packages, sep="\n")


if __name__ == "__main__":
    main()
