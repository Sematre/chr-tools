# First-boot script on Google Compute Engine, for chr_bootstrap.py --script.
# SSH keys come from the ssh-keys metadata of the instance and the project,
# user-data (RouterOS script) from the user-data metadata. RouterOS runs a startup-script itself.
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

# Returns the value at a metadata path, or nothing for 404 Not Found.
:local get do={
  :local value ""
  :onerror missing,details in={
    :set value ([/tool/fetch url=("http://169.254.169.254/computeMetadata/v1/" . $1) output=user as-value \
      http-header-field="Metadata-Flavor: Google"]->"data")
  } do={ :if (($details->"code") != 404) do={ :error $missing } }
  :return $value
}
:onerror failure in={
  :retry command={ [$get "instance/id"] } delay=2 max=30
  :local keys [$get "instance/attributes/ssh-keys"]
  :if ([$get "instance/attributes/block-project-ssh-keys"] != "true") do={
    :set keys ($keys . "\n" . [$get "project/attributes/ssh-keys"])
  }
  # Each line is USER:KEY. Every key logs in as admin.
  :foreach row in=[:deserialize from=dsv delimiter="\t" options=dsv.plain $keys] do={
    :local line ($row->0)
    :if ([:len $line] > 0) do={ $addKey [:pick $line ([:find $line ":"] + 1) [:len $line]] }
  }
  :local userdata [$get "instance/attributes/user-data"]
  :if ([:len $userdata] > 0) do={ :local run [:parse $userdata]; $run }
  :log info "first boot: done"
} do={ :log error "first boot: $failure" }
