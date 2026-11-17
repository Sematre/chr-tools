## How bootstrapping works

### First-boot script

It is installed as `/rw/autorun.scr`. RouterOS runs it once on its first
boot, in my tests before SSH accepted any connection. RouterOS then keeps it as the custom default configuration:
admins can read it, and it runs again after `/system/reset-configuration`, at that point before
`ether1` exists and without the DHCP client of a new CHR. So it must not contain secrets, which
belong into user-data instead.

### Device-mode

It is stored in `/rw/rosmode.msg`, a MikroTik "M2" message without a signature or
a binding to the installation. The tool writes the active settings directly, so neither a power
cycle nor a button press is needed. `mode` is advanced, home, basic or rose. The yes/no properties
are those of `/system/device-mode/print`: flagged, flagging-enabled, scheduler, socks, fetch,
pptp, l2tp, bandwidth-test, traffic-gen, sniffer, ipsec, romon, proxy, hotspot, smb, email,
zerotier, container, install-any-version, partitions and routerboard.

### Packages

An installed package is the unchanged `.npk` file at `/var/pdb/<name>/image`, which
is what RouterOS writes when it installs one, so the package is active on the first boot. It has
to match the image's version and architecture, and RouterOS still checks its signature. Package
sets differ per architecture. The RouterOS partition has about 69 MiB free, and about 59 MiB
(x86) or 20 MiB (arm64) with every package installed.

### Reproducibility

The same inputs always produce a byte-identical image, whatever the time,
user, umask, paths or argument order. All new files get one timestamp (`SOURCE_DATE_EPOCH`, or
the time the system package was written into the image), fixed modes and root ownership, and they
are written in sorted order. Before the result replaces the output, the tool checks the written
contents and timestamps and runs `e2fsck`.

## First-boot / cloud-init scripts

RouterOS mounts ISO 9660 drives by itself, which includes the cloud-init drive of Proxmox VE.
`nocloud.rsc` takes the keys from `ssh_authorized_keys` of a `#cloud-config` user-data and runs any
other user-data as RouterOS script.

Judging by its binaries, RouterOS also contains its own setup for Amazon EC2, Google Compute
Engine and Azure. It did not start under QEMU, so it is untested here. Its EC2
part has no IMDSv2 support, which is why `aws.rsc` asks to require IMDSv2, and on Compute Engine it
runs the `startup-script` metadata. For Azure, it reads the provisioning disk itself, so there is no
example. Without its key lines, `first-boot.rsc` still sets the random password for `admin` there.

All scripts were tested on RouterOS 7.24.5 under QEMU, against metadata services that follow
cloud-init's implementation for each platform.

## Deploying an image

The image must not boot before it reaches its target. Booting binds the default DHCP client to the
MAC address of `ether1`, and on a new VM that network card becomes `ether2` without network access.

MikroTik's guides for cloud providers write the raw image to the disk from a rescue system with
`dd`, which works the same with a prepared image. Proxmox VE imports raw images directly,
`hcloud-upload-image` turns them into Hetzner Cloud snapshots, and `qemu-img convert` produces
qcow2, VMDK or VHDX files for other platforms.

## Development

```sh
pip install pytest ruff mypy
pytest && ruff check && ruff format --check && mypy *.py tests
```

The tests build a small CHR-like image with `mke2fs` and need no downloads.
