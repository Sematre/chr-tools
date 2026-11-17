# First-boot script on Hetzner Cloud, for chr_bootstrap.py --script.
# SSH keys and user-data (RouterOS script) come from the Hetzner Cloud metadata service.
# RouterOS runs it on the first boot and again after /system/reset-configuration, and keeps it
# readable for admins, so it must not contain secrets.

# Nobody knows this password, so from now on only SSH keys can log in.
/user/set admin password=[:rndstr length=32]
# A key that RouterOS rejects is logged and skipped.
:local addKey do={
  :onerror problem in={ /user/ssh-keys/add user=admin key=$1 } do={ :log warning "first boot: $problem" }
}
# After a configuration reset, this runs before ether1 exists and without the DHCP client
# that a new CHR has.
:retry command={ /interface/ethernet/get ether1 } delay=1 max=60
:if ([:len [/ip/dhcp-client/find interface=ether1]] = 0) do={ /ip/dhcp-client/add interface=ether1 }

:local url "http://169.254.169.254/hetzner/v1"
:onerror failure in={
  :local metadata
  :retry command={
    :set metadata ([/tool/fetch url="$url/metadata" output=user as-value]->"data")
  } delay=2 max=30
  # The keys are the list items that follow "public-keys:" in the YAML document.
  :local inKeys false
  :foreach row in=[:deserialize from=dsv delimiter="\t" options=dsv.plain $metadata] do={
    :local line ($row->0)
    :if ($inKeys && ([:pick $line 0 2] = "- ")) do={
      $addKey [:pick $line 2 [:len $line]]
    } else={ :set inKeys ($line = "public-keys:") }
  }
  # Without user-data, the service answers with 204 No Content.
  :local userdata ([/tool/fetch url="$url/userdata" output=user as-value]->"data")
  :if ([:len $userdata] > 0) do={ :local run [:parse $userdata]; $run }
  :log info "first boot: done"
} do={ :log error "first boot: $failure" }
