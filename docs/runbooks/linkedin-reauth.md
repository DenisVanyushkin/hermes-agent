# LinkedIn Re-auth Runbook

Symptom: `source_kpi_run` shows `linkedin` with `login_walls > 0` on consecutive daily
runs, `found_count = 0`, `skip_reason = login_wall`. The health report emits
"LinkedIn re-auth required". Root cause: the persistent Chromium profile at
`/var/lib/browser-desktop/profiles/linkedin` lost its authenticated session
(no `li_at` cookie).

## Procedure (operator, ~5 minutes)

1. From your workstation, open an SSH tunnel to noVNC:
   ```
   ssh -L 6080:127.0.0.1:6080 hermes
   ```
2. On the VPS, start the browser desktop with the LinkedIn profile:
   ```
   sudo bash /home/hermes/.hermes/hermes-agent/scripts/browser-desktop-linkedin.sh
   ```
3. Get the VNC password:
   ```
   sudo cat /var/lib/browser-desktop/.vnc/password.txt
   ```
4. In your local browser open `http://127.0.0.1:6080/vnc.html` and connect.
5. In the Chromium inside VNC: go to `https://www.linkedin.com/login`, sign in
   (credentials + 2FA). Wait for the feed to fully load, then open
   `https://www.linkedin.com/jobs/` and confirm job search works without an
   auth prompt.
6. Stop the desktop so the profile is not locked during the daily run:
   ```
   sudo bash /home/hermes/.hermes/hermes-agent/scripts/browser-desktop-stop.sh
   ```
7. Smoke-check the session:
   ```
   bash /home/hermes/.hermes/hermes-agent/scripts/job_intel_browser_health.sh
   ```
   Expect the linkedin probe to report ok with no login wall.

## Validation

- Cookie check: `li_at` present for `.linkedin.com` in the profile cookie DB.
- Next production daily run: `source_kpi_run.login_walls = 0`,
  `anti_bot_events = 0` for `linkedin`; `skip_reason` is `ok_non_empty` or
  `real_empty` (never `login_wall` / `auth_required`).
- Run metadata `source_statuses.linkedin.status` is `ok` or a clean `empty`.
