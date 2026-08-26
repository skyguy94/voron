# Voron & OrcaSlicer Configurations

This repo contains my OrcaSlicer
Printer configuration for VT.1399 (Voron Trident 300) and dv_027 (Doron Velta),
plus the OrcaSlicer profiles that drive them.

## Klipper config

Symlink the `~/printer_data/config` folder to the appropriate subfolder.

Ex:

```bash
rm -fr ~/printer_data/config #Ensure this folder doesn't have anything you want to keep first.
git checkout <repo> ~/voron
ln -s ~/voron/vt_1399 ~/printer_data/config
```

### OrcaSlicer

Symlink the OrcaSlicer config directory to the github repo.

## TLS certificates

The scripts folder contains a `New-PrinterCert.ps1` helper for managing TLS certificates. It
can be used to issue a self-signed root ca certificate that gets installed in the Windows
certificate store for serving as the trust anchor. There aren't really any security
concerns (CIA) about this cert and running printers on my local lan,
so its checked into github with a passphrase.

## Root certificate

Use this to issue the root certificate and install it the windows machine store:

```powershell
.\scripts\New-PrinterCert.ps1 -InitCa
.\scripts\New-PrinterCert.ps1 -TrustCa -Machine
```

`-ProtectCaKey` adds a passphrase to the root key.

```powershell
.\scripts\New-PrinterCert.ps1 -ProtectCaKey
```

It can be removed from the certificate store with normal powershell cmdlets.

```powershell
Get-ChildItem Cert:\LocalMachine\Root |
  Where-Object { $_.Subject -eq 'CN=Voron Home CA' } | Remove-Item
```

### Printer certificate

Running the helper with the following options will created a properly configured leaf cert
with SAN fields for the DNS and IP. Note that all modern browsers ignore the CN attribute and
only read the SAN fields.

```powershell
.\scripts\New-PrinterCert.ps1 -PrinterName trident -ExtraDns trident.local -IPAddress 192.168.1.26
.\scripts\New-PrinterCert.ps1 -PrinterName delta   -ExtraDns delta.local
```

Leaf certificates default to 825 days (`-Days`) for no particular reason.

### Deploying the certificate

nginx reads the leaf from `/etc/nginx/`

```bash
scp trident.lan.crt trident.lan.key skyguy94@trident.lan:/tmp/
ssh skyguy94@trident.lan '
  sudo install -o root -g root -m 644 /tmp/trident.lan.crt /etc/nginx/trident.lan.crt
  sudo install -o root -g root -m 600 /tmp/trident.lan.key /etc/nginx/trident.lan.key
  rm -f /tmp/trident.lan.*
  sudo nginx -t && sudo systemctl reload nginx'
```

Verify from the client:

```bash
openssl s_client -connect trident.lan:443 -CAfile certs/voron-home-ca.crt -verify_hostname trident.lan
```
