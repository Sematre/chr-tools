# First-boot script on Amazon EC2, for chr_bootstrap.py --script.
# SSH keys and user-data (RouterOS script) come from the instance metadata service.
# Require IMDSv2 on the instance, so that RouterOS's own EC2 setup, which uses IMDSv1, does
# not add the key and run the user-data a second time.
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

:local url "http://169.254.169.254/latest"
:onerror failure in={
  :local token
  :retry command={
    :set token ([/tool/fetch url="$url/api/token" http-method=put output=user as-value \
      http-header-field="X-aws-ec2-metadata-token-ttl-seconds: 300"]->"data")
  } delay=2 max=30
  :local header "X-aws-ec2-metadata-token: $token"
  :local key ([/tool/fetch url="$url/meta-data/public-keys/0/openssh-key" http-header-field=$header \
    output=user as-value]->"data")
  $addKey $key
  # A 404 Not Found response means that there is no user-data.
  :local userdata ""
  :onerror missing,details in={
    :set userdata ([/tool/fetch url="$url/user-data" http-header-field=$header output=user as-value]->"data")
  } do={ :if (($details->"code") != 404) do={ :error $missing } }
  :if ([:len $userdata] > 0) do={ :local run [:parse $userdata]; $run }
  :log info "first boot: done"
} do={ :log error "first boot: $failure" }
