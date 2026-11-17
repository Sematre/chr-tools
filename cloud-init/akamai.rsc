# First-boot script on Akamai Cloud (Linode), for chr_bootstrap.py --script.
# SSH keys and base64 encoded user-data (RouterOS script) come from the
# Linode metadata service.
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

:local url "http://169.254.169.254/v1"
:onerror failure in={
  :local token
  :retry command={
    :set token ([/tool/fetch url="$url/token" http-method=put output=user as-value \
      http-header-field="Metadata-Token-Expiry-Seconds: 300"]->"data")
  } delay=2 max=30
  :local keys ([/tool/fetch url="$url/ssh-keys" output=user as-value \
    http-header-field="Metadata-Token: $token,Accept: application/json"]->"data")
  # The keys of root and of all other users chosen for the Linode log in as admin.
  :foreach user,list in=([:deserialize from=json options=json.no-string-conversion $keys]->"users") do={
    :foreach key in=$list do={ $addKey $key }
  }
  # A 404 Not Found response means that there is no user-data.
  :local userdata ""
  :onerror missing,details in={
    :set userdata ([/tool/fetch url="$url/user-data" http-header-field="Metadata-Token: $token" output=user as-value]->"data")
  } do={ :if (($details->"code") != 404) do={ :error $missing } }
  :set userdata [:convert from=base64 $userdata]
  :if ([:len $userdata] > 0) do={ :local run [:parse $userdata]; $run }
  :log info "first boot: done"
} do={ :log error "first boot: $failure" }
