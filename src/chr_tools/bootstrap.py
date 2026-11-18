"""Prepare a never-booted RouterOS CHR image offline.

Installs a first-boot script, device-mode settings and extra packages. The input is never changed,
and the same inputs always produce a byte-identical output. Honors SOURCE_DATE_EPOCH.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from chr_tools.info import (
    AUTORUN,
    MODES,
    PROPERTIES,
    ROSMODE,
    STORE,
    SYSTEM,
    Image,
    copy_raw,
    encode_device_mode,
    tool,
)
from chr_tools.npk import parse_package, read_package

FILE_MODE, DIRECTORY_MODE = 0o100644, 0o040755


def device_mode_setting(text: str) -> tuple[str, str]:
    name, _, value = text.partition("=")
    if name == "mode":
        choices = list(MODES)
    elif name in PROPERTIES:
        choices = ["yes", "no"]
    else:
        raise argparse.ArgumentTypeError(f"unknown device-mode property {name!r}")
    if value not in choices:
        raise argparse.ArgumentTypeError(f"{name} must be one of {', '.join(choices)}")
    return name, value


def stage(
    image: Image, root: Path, script: Path | None, settings: dict[str, str], packages: list[Path]
) -> list[str]:
    """Writes each file the image should receive below ROOT, at its path in the image."""
    files = {}
    if script:
        files[AUTORUN] = script.read_bytes()
    if settings:
        files[ROSMODE] = encode_device_mode(settings)
    system = parse_package(image.read(SYSTEM))
    for path in packages:
        package = read_package(path)
        target = f"/var/pdb/{package.name}/image"  # what RouterOS writes when it installs a package
        if package.name == "system" or not re.fullmatch(r"[a-z0-9-]+", package.name):
            raise ValueError(f"{path} is not an extra package")
        if (package.version, package.architecture) != (system.version, system.architecture):
            raise ValueError(
                f"{path} is for {package.version} {package.architecture}, "
                f"the image is {system.version} {system.architecture}"
            )
        if target in files:
            raise ValueError(f"package {package.name} is given twice")
        files[target] = path.read_bytes()

    for target, data in files.items():
        (root / target[1:]).parent.mkdir(parents=True, exist_ok=True)
        (root / target[1:]).write_bytes(data)
    return sorted(files)


def render(image: Image, paths: list[str]) -> str:
    """Returns the debugfs commands that write the staged files.

    Each command must succeed, so any message from debugfs is an error. Files are written in byte
    order of their path and a missing directory right before its first file, which keeps the
    allocation of inodes and blocks the same on every run.
    """
    commands, created = [], set()
    for path in paths:
        directory = path.rsplit("/", 1)[0]
        if directory not in created and not image.exists(directory):
            commands += [f"mkdir {directory}", *attributes(directory, DIRECTORY_MODE)]
            created.add(directory)
        if image.exists(path):
            commands.append(f"rm {path}")
        commands += [f"write {path[1:]} {path}", *attributes(path, FILE_MODE)]
    return "".join(f"{command}\n" for command in commands)


def attributes(path: str, mode: int) -> list[str]:
    return [f"sif {path} mode 0{mode:o}", f"sif {path} uid 0", f"sif {path} gid 0"]


def apply(image: Image, root: Path, commands: str, time: int) -> None:
    """Runs the commands with every new timestamp set to TIME.

    debugfs exits with 0 even when commands fail, but prints nothing except its version banner
    to stderr when all of them work.
    """
    environment = {**os.environ, "E2FSPROGS_FAKE_TIME": str(time)}
    result = image.debugfs("-w", "-f", "-", input=commands.encode(), cwd=root, env=environment)
    lines = result.stderr.decode().splitlines()
    errors = [line for line in lines if not line.startswith("debugfs ")]
    if errors:
        raise RuntimeError("debugfs failed:\n" + "\n".join(errors))


def verify(image: Image, root: Path, paths: list[str], time: int) -> None:
    for path in paths:
        if image.read(path) != (root / path[1:]).read_bytes():
            raise RuntimeError(f"writing {path} failed")
        if image.mtime(path) != time:
            raise RuntimeError(
                "this e2fsprogs ignores E2FSPROGS_FAKE_TIME, the image is not reproducible"
            )
    fsck = subprocess.run(
        [tool("e2fsck"), "-fn", image.device], check=False, capture_output=True, text=True
    )
    if fsck.returncode:
        raise RuntimeError(f"e2fsck found errors after writing:\n{fsck.stdout}")


def bootstrap(
    source: Path,
    output: Path,
    *,
    script: Path | None = None,
    settings: dict[str, str] | None = None,
    packages: list[Path] | None = None,
    dry_run: bool = False,
) -> str:
    """Writes the prepared image to OUTPUT, or only returns the debugfs commands for a dry run."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if epoch and not epoch.isdigit():
        raise ValueError(f"SOURCE_DATE_EPOCH must be a number of seconds, not {epoch!r}")
    work = output.with_name(f".{output.name}.part")
    try:
        copy_raw(source, work)
        image = Image(work, name=source)
        if image.exists(STORE):
            raise ValueError(f"{source} was already booted")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = stage(image, root, script, settings or {}, packages or [])
            commands = render(image, paths)
            if dry_run:
                return commands
            # Without SOURCE_DATE_EPOCH, the time the system package was written into the image.
            time = int(epoch) if epoch else image.mtime(SYSTEM)
            apply(image, root, commands, time)
            verify(image, root, paths, time)
        work.replace(output)
        return commands
    finally:
        work.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "image", type=Path, help="never-booted raw CHR image, or MikroTik's .img.zip"
    )
    parser.add_argument("output", type=Path, help="where to write the prepared raw image")
    parser.add_argument(
        "-s", "--script", type=Path, help="RouterOS script that runs once on the first boot"
    )
    parser.add_argument(
        "-m", "--device-mode", type=device_mode_setting, action="append", default=[],
        metavar="KEY=VALUE", help="device-mode property, such as mode=rose or container=yes",
    )  # fmt: skip
    parser.add_argument(
        "-p", "--package", type=Path, action="append", default=[], metavar="NPK",
        help="extra package of the image's version and architecture",
    )  # fmt: skip
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="print the debugfs commands only"
    )
    args = parser.parse_args(argv)

    settings: dict[str, str] = {}
    for name, value in args.device_mode:
        if name in settings:
            parser.error(f"device-mode {name} is given twice")
        settings[name] = value
    if not (args.script or settings or args.package):
        parser.error("nothing to do, give --script, --device-mode or --package")
    try:
        commands = bootstrap(
            args.image,
            args.output,
            script=args.script,
            settings=settings,
            packages=args.package,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError, RuntimeError) as error:
        sys.exit(f"{parser.prog}: {error}")
    if args.dry_run:
        print(commands, end="")
    else:
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
