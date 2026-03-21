# SSL Certificate Setup

PixlStash uses a self-signed certificate for HTTPS. Since it's self-signed, it needs to be
manually trusted on each client device, particularly iOS which enforces strict certificate
requirements.

## Requirements

- The cert must have a **Subject Alternative Name (SAN)** — iOS rejects certs without one
- The SAN must include both the hostname and IP address you'll use to access the server
- Port 443 binding requires special capability set on the Python binary (Linux only)

## Generating the Certificate

Replace `<your-server-ip>` with your server's LAN IP (e.g. `192.168.68.123`).

```bash
cd ~/Projects/pixlstash
openssl req -x509 -newkey rsa:4096 -keyout ssl/key.pem -out ssl/cert.pem -days 365 -nodes \
  -subj "/CN=pixlstash.local" \
  -addext "subjectAltName=DNS:pixlstash.local,IP:<your-server-ip>"
```

> **Note:** If your server's IP changes (e.g. after network reconfiguration), regenerate the
> cert with the new IP and reinstall it on all client devices.

## Port 443 Binding (Linux)

Linux prevents non-root processes from binding to ports below 1024. Grant the venv Python
binary the capability to do so:

```bash
sudo setcap 'cap_net_bind_service=+ep' $(readlink -f .venv/bin/python)
```

> **Note:** This must be re-run after upgrading Python in the venv.

## Server Configuration

In `~/.config/pixlstash/server-config.json`:

```json
{
  "port": 443,
  "require_ssl": true,
  "ssl_keyfile": "ssl/key.pem",
  "ssl_certfile": "ssl/cert.pem"
}
```

> **Warning:** Use JSON boolean `true`/`false`, not strings like `"yes"`/`"no"` — Python
> treats any non-empty string as truthy.

## Installing the Certificate on iPhone (iOS)

iOS requires the cert to be explicitly trusted as a root CA. Do this once per cert (redo after regenerating).

1. **Transfer the cert** — email `ssl/cert.pem` to yourself or host it temporarily:
   ```bash
   # Serve it temporarily over HTTP (port 8080, no auth)
   cd ~/Projects/pixlstash/ssl
   python -m http.server 8080
   ```
   Then open `http://<server-ip>:8080/cert.pem` in Safari on the iPhone.

2. **Install the profile** — Safari will prompt "This website is trying to download a
   configuration profile". Tap **Allow**, then go to:
   **Settings → General → VPN & Device Management** → find the profile → tap **Install**

3. **Enable full trust** — go to:
   **Settings → General → About → Certificate Trust Settings** → enable the toggle for
   the PixlStash certificate

After these steps, `https://pixlstash.local` will work in Safari without warnings.

## Renewal

Certs generated with the command above expire after 365 days. To renew:

1. Regenerate the cert (same command as above)
2. Restart the server
3. Reinstall the cert on all iOS devices (repeat the iPhone steps above)

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied` binding port 443 | `setcap` not set | Re-run the `setcap` command |
| `Empty reply from server` via curl | SSL enabled but connecting with HTTP | Use `https://` |
| Safari shows "Cannot connect to server" (no cert warning) | Cert missing SAN | Regenerate cert with `-addext subjectAltName=...` |
| Safari shows cert warning but no bypass option | Cert not installed as trusted root | Follow iPhone installation steps above |
| Cert works on PC but not iPhone | SAN doesn't include the hostname/IP used | Regenerate cert including all hostnames/IPs |
