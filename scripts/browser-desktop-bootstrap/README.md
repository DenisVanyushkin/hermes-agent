# Browser Desktop Bootstrap

This script installs and runs a lightweight persistent browser desktop for VPS-based Playwright bootstrap and manual browser debugging.

## What it sets up

- XFCE desktop
- Chromium
- Xvfb display server
- x11vnc with password authentication
- noVNC/websockify for browser-based access
- persistent Chromium profiles for:
  - LinkedIn
  - HH

## Script

- `../browser-desktop-bootstrap.sh`

## Run on the VPS

```bash
sudo bash scripts/browser-desktop-bootstrap.sh --profile linkedin --url https://www.linkedin.com/
sudo bash scripts/browser-desktop-bootstrap.sh --profile hh --url https://hh.ru/
```

The first run may install packages and create:

- system user: `browser`
- base directory: `/var/lib/browser-desktop`
- persistent profiles:
  - `/var/lib/browser-desktop/profiles/linkedin`
  - `/var/lib/browser-desktop/profiles/hh`
- logs: `/var/lib/browser-desktop/logs/`
- VNC auth:
  - `/var/lib/browser-desktop/.vnc/passwd`
  - `/var/lib/browser-desktop/.vnc/password.txt`

## Secure access

Services bind to localhost only:

- noVNC: `127.0.0.1:6080`
- VNC: `127.0.0.1:5901`
- Chromium CDP: `127.0.0.1:9222`

Use SSH port forwarding:

```bash
ssh -L 6080:127.0.0.1:6080 -L 9222:127.0.0.1:9222 user@YOUR_VPS
```

Then open:

- noVNC: `http://127.0.0.1:6080/vnc.html`
- CDP health check: `http://127.0.0.1:9222/json/version`

## Persistent sessions

Chromium profile data is stored in:

- `/var/lib/browser-desktop/profiles/linkedin`
- `/var/lib/browser-desktop/profiles/hh`

After you log in once, the session stays on disk and can be reused by Hermes later.

## Verification

After startup, verify:

```bash
curl http://127.0.0.1:9222/json/version
curl http://127.0.0.1:6080/vnc.html
```

You can also inspect logs in:

- `/var/lib/browser-desktop/logs/xvfb.log`
- `/var/lib/browser-desktop/logs/xfce.log`
- `/var/lib/browser-desktop/logs/x11vnc.log`
- `/var/lib/browser-desktop/logs/websockify.log`
- `/var/lib/browser-desktop/logs/chromium-linkedin.log`
- `/var/lib/browser-desktop/logs/chromium-hh.log`

## Notes

- The desktop is intentionally lightweight and does not use systemd units.
- If a port or display is already in use, the script fails fast instead of taking over existing services.
- The script validates the profile name to prevent path traversal.
