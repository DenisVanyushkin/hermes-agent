# Browser desktop bootstrap for VPS

This setup provides a lightweight persistent browser desktop for manual authentication and Playwright debugging.

## What it installs

- XFCE desktop
- Chromium
- Xvfb
- x11vnc
- noVNC / websockify

## What it provides

- Local-only access on the VPS
- Persistent Chromium profiles
- Separate profiles for LinkedIn and HH
- CDP endpoint for Playwright

## Install / start

Run on the VPS as root:

```bash
sudo bash scripts/browser-desktop-bootstrap.sh --profile linkedin --url https://www.linkedin.com/
sudo bash scripts/browser-desktop-bootstrap.sh --profile hh --url https://hh.ru/
```

The first run:
- installs required packages
- creates the `browser` user
- creates persistent directories under `/var/lib/browser-desktop`
- generates a random VNC password
- starts Chromium with remote debugging enabled

## Connect securely

From your workstation, create SSH tunnels:

```bash
ssh -L 6080:127.0.0.1:6080 -L 9222:127.0.0.1:9222 user@YOUR_VPS
```

Then open:

- noVNC: `http://127.0.0.1:6080/vnc.html`
- Playwright / CDP: `http://127.0.0.1:9222/json/version`

## Persistent profile locations

- LinkedIn profile: `/var/lib/browser-desktop/profiles/linkedin`
- HH profile: `/var/lib/browser-desktop/profiles/hh`

You can reuse those directories in Playwright by pointing Chromium at the matching `user-data-dir`.

## Playwright example

```ts
import { chromium } from 'playwright';

const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
const context = browser.contexts()[0] ?? await browser.newContext();
const page = context.pages()[0] ?? await context.newPage();
await page.goto('https://www.linkedin.com/');
```

## Stop / restart

The bootstrap script starts desktop services for the requested profile. If you need to stop them, use the matching stop helpers in `scripts/` if present, or kill the processes for the `browser` user.

## Security notes

- Services bind to `127.0.0.1` only.
- Access is expected through SSH tunneling.
- Do not expose 5901 / 6080 / 9222 directly to the internet.
- Keep the VNC password private.

## Operational notes

- Use one Chromium profile per site/account.
- Authenticate manually once.
- Reuse the same profile directory afterward.
- If Chromium gets stuck, restart only the browser desktop stack, not the whole VPS.
