# First-boot script with a cloud-init drive, for chr_bootstrap.py --script.
# RouterOS mounts the drive itself, for example the NoCloud drive of
# Proxmox VE. SSH keys come from ssh_authorized_keys in a #cloud-config user-data, any other
# user-data runs as RouterOS script.
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

:onerror failure in={
  :local seed
  :retry command={
    :set seed [:pick [/file/find where name~"/user-data\$"] 0]
    :if ([:typeof $seed] = "nil") do={ :error "no cloud-init drive" }
  } delay=1 max=30
  :local userdata [/file/get $seed contents]
  :if ([:pick $userdata 0 13] = "#cloud-config") do={
    # The keys are the list items that follow "ssh_authorized_keys:".
    :local inKeys false
    :foreach row in=[:deserialize from=dsv delimiter="\t" options=dsv.plain $userdata] do={
      :local line ($row->0)
      :if ($inKeys && ([:pick $line 0 2] = "- ")) do={
        $addKey [:pick $line 2 [:len $line]]
      } else={ :set inKeys ($line = "ssh_authorized_keys:") }
    }
    # A #cloud-config is not RouterOS script.
    :set userdata ""
  }
  :if ([:len $userdata] > 0) do={ :local run [:parse $userdata]; $run }
  :log info "first boot: done"
} do={ :log error "first boot: $failure" }
