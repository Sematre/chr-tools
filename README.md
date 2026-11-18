# chr-tools

Prepare RouterOS Cloud Hosted Router (CHR) images offline, so that every instance sets itself up
on its first boot with optional cloud-init support. These tools put a first-boot script, device-mode
settings and extra packages into MikroTik's official image without ever booting it, so each
instance still generates its own system ID and SSH host key and detects its own network cards.

Configuring and maintaining a running router is out of scope.

## Requirements

Python 3.11 or newer, without further packages. `chr-bootstrap` and `chr-info` also need
e2fsprogs (`debugfs` and `e2fsck`). `chr-fetch` and `npk-info` run wherever Python does.

## Quick start

Recommended project manager: [uv](https://docs.astral.sh/uv/)

```sh
# Downloads the image and packages of a version or channel
uv run chr-fetch stable --architecture arm64 --package container

# Writes a new image with a first-boot script, device-mode settings and extra packages
uv run chr-bootstrap --script cloud-init/hetzner.rsc \
  --device-mode mode=advanced --device-mode container=yes \
  --package container-7.24.5-arm64.npk chr-7.24.5-arm64.img.zip router.img

# Shows the version, state, packages, device-mode, first-boot script and free space of an image
uv run chr-info router.img
uv run chr-info router.img --script | diff - cloud-init/hetzner.rsc
```

Pick the first-boot script for your platform from the [list of scripts](#first-boot--cloud-init-scripts).

Every tool prints JSON with `--json`, reports errors on stderr and exits with 0 on success, 1 on
errors and 2 on invalid arguments.

## First-boot / cloud-init scripts

A new CHR accepts the user `admin` without a password over SSH, Telnet, FTP, HTTP (REST), WinBox
and the API, from any address and without a firewall. The scripts close that gap and leave
everything else to automation. Those in [`cloud-init`](cloud-init) take their settings from the
platform and can be used unchanged. [`examples/first-boot.rsc`](examples/first-boot.rsc) is for
platforms without a metadata service and needs your SSH keys before use. Each script only does
the essentials:

1. It sets a random password for `admin` that nobody knows, so only SSH keys can log in.
2. It adds the SSH keys of the platform for `admin`.
3. It makes sure that `ether1` has a DHCP client, which a configuration reset removes.
4. It runs the platform's user-data as RouterOS script, kept in memory. This is the place for
   anything else, such as users with passwords for an API client, services or a firewall.

| Script                        | Platform              | SSH keys and user-data from                |
| ----------------------------- | --------------------- | ------------------------------------------ |
| `examples/first-boot.rsc`     | any                   | keys in the script, no user-data           |
| `cloud-init/hetzner.rsc`      | Hetzner Cloud         | metadata service                           |
| `cloud-init/aws.rsc`          | Amazon EC2            | instance metadata service, IMDSv2 required |
| `cloud-init/gcp.rsc`          | Google Compute Engine | `ssh-keys` and `user-data` metadata        |
| `cloud-init/digitalocean.rsc` | DigitalOcean          | metadata service                           |
| `cloud-init/vultr.rsc`        | Vultr                 | metadata service                           |
| `cloud-init/akamai.rsc`       | Akamai Cloud (Linode) | metadata service                           |
| `cloud-init/oracle.rsc`       | Oracle Cloud          | instance metadata service                  |
| `cloud-init/openstack.rsc`    | OpenStack             | Nova metadata service                      |
| `cloud-init/nocloud.rsc`      | Proxmox VE and others | cloud-init drive (NoCloud)                 |
