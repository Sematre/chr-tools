# First-boot script without a metadata service, for chr_bootstrap.py --script.
# Replace the example key with your own.
# RouterOS runs it on the first boot and again after /system/reset-configuration, and keeps it
# readable for admins, so it must not contain secrets.

# Nobody knows this password, so from now on only SSH keys can log in.
/user/set admin password=[:rndstr length=32]
# A key that RouterOS rejects is logged and skipped.
:local addKey do={
  :onerror problem in={ /user/ssh-keys/add user=admin key=$1 } do={ :log warning "first boot: $problem" }
}
# The public keys that may log in as admin, separated by semicolons.
:local keys {"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGV4YW1wbGUga2V5LCByZXBsYWNlIGl0IHdpdGggeW91cnM you@example"}
:foreach key in=$keys do={ $addKey $key }
# After a configuration reset, this runs before ether1 exists and without the DHCP client
# that a new CHR has.
:retry command={ /interface/ethernet/get ether1 } delay=1 max=60
:if ([:len [/ip/dhcp-client/find interface=ether1]] = 0) do={ /ip/dhcp-client/add interface=ether1 }
