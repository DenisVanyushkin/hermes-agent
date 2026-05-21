#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/../agent" ] && [ -d "$SCRIPT_DIR/../gateway" ]; then
  REPO="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  REPO="$HOME/.hermes/hermes-agent"
fi
if [ -x "$REPO/venv/bin/python3" ] && [ -x "$REPO/venv/bin/hermes" ]; then
  HERMES_BIN="$REPO/venv/bin/hermes"
else
  HERMES_BIN="$(command -v hermes || true)"
fi
if [ -z "${HERMES_BIN:-}" ] || [ ! -x "$HERMES_BIN" ]; then
  echo "Hermes auto-update failed."
  echo "Repo: $REPO"
  echo "Failure: could not locate an executable hermes binary"
  exit 1
fi
TRACKED_FILES=(agent/account_usage.py gateway/runtime_footer.py hermes_cli/config.py)
STAMP="$(date +%Y%m%d-%H%M%S)"
TMP_PATCH="$(mktemp /tmp/quota-footer-codex-almaty.XXXXXX.patch)"
BACKUP_DIR="$(mktemp -d /tmp/quota-footer-backup.XXXXXX)"
cleanup() { rm -f "$TMP_PATCH"; rm -rf "$BACKUP_DIR"; }
trap cleanup EXIT

git config --global --add safe.directory "$REPO" >/dev/null 2>&1 || true

cat > "$TMP_PATCH" <<'PATCH'
diff --git a/agent/account_usage.py b/agent/account_usage.py
index 0e9562dcc..b8a9e7567 100644
--- a/agent/account_usage.py
+++ b/agent/account_usage.py
@@ -142,7 +142,7 @@ def _fetch_codex_account_usage() -> Optional[AccountUsageSnapshot]:
     payload = response.json() or {}
     rate_limit = payload.get("rate_limit") or {}
     windows: list[AccountUsageWindow] = []
-    for key, label in (("primary_window", "Session"), ("secondary_window", "Weekly")):
+    for key, label in (("primary_window", "5h"), ("secondary_window", "Week")):
         window = rate_limit.get(key) or {}
         used = window.get("used_percent")
         if used is None:
diff --git a/gateway/runtime_footer.py b/gateway/runtime_footer.py
index 9d3fea252..9ab0243f7 100644
--- a/gateway/runtime_footer.py
+++ b/gateway/runtime_footer.py
@@ -1,15 +1,16 @@
 """Gateway runtime-metadata footer.
 
 Renders a compact footer showing runtime state (model, context %, cwd) and
-appends it to the FINAL message of an agent turn when enabled.  Off by default
+appends it to the FINAL message of an agent turn when enabled. Off by default
 to keep replies minimal.
 
 Config (``~/.hermes/config.yaml``)::
 
     display:
       runtime_footer:
-        enabled: true                       # off by default
-        fields: [model, context_pct, cwd]   # order shown; drop any to hide
+        enabled: true                        # off by default
+        fields: [model, context_pct, cwd]    # order shown; drop any to hide
+        account_usage: true                  # optional provider quota line
 
 Per-platform overrides live under ``display.platforms.<platform>.runtime_footer``.
 Users can toggle the global setting with ``/footer on|off`` from both the CLI
@@ -18,7 +19,7 @@ and any gateway platform.
 The footer is appended to the final response text in ``gateway/run.py`` right
 before returning the response to the adapter send path — so it only lands on
 the final message a user sees, not on tool-progress updates or streaming
-partials.  When streaming is on and the final text has already been delivered
+partials. When streaming is on and the final text has already been delivered
 piecemeal, the footer is sent as a separate trailing message via
 ``send_trailing_footer()``.
 """
@@ -26,15 +27,21 @@ piecemeal, the footer is sent as a separate trailing message via
 from __future__ import annotations
 
 import os
-from pathlib import Path
+import time
+from datetime import datetime
 from typing import Any, Iterable, Optional
+from zoneinfo import ZoneInfo
+
+from agent.account_usage import AccountUsageSnapshot, fetch_account_usage
 
 _DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
 _SEP = " · "
+_USAGE_CACHE_TTL_SECONDS = 60.0
+_USAGE_CACHE: dict[tuple[str, str, str], tuple[float, Any]] = {}
 
 
 def _home_relative_cwd(cwd: str) -> str:
-    """Return *cwd* with ``$HOME`` collapsed to ``~``.  Empty string if unset."""
+    """Return *cwd* with ``$HOME`` collapsed to ``~``. Empty string if unset."""
     if not cwd:
         return ""
     try:
@@ -65,15 +72,17 @@ def resolve_footer_config(
         2. ``display.runtime_footer``
         3. ``display.platforms.<platform_key>.runtime_footer``
     """
-    resolved = {"enabled": False, "fields": list(_DEFAULT_FIELDS)}
+    resolved = {"enabled": False, "fields": list(_DEFAULT_FIELDS), "account_usage": False}
     cfg = (user_config or {}).get("display") or {}
 
     global_cfg = cfg.get("runtime_footer")
     if isinstance(global_cfg, dict):
         if "enabled" in global_cfg:
             resolved["enabled"] = bool(global_cfg.get("enabled"))
-        if isinstance(global_cfg.get("fields"), list) and global_cfg["fields"]:
+        if isinstance(global_cfg.get("fields"), list):
             resolved["fields"] = [str(f) for f in global_cfg["fields"]]
+        if "account_usage" in global_cfg:
+            resolved["account_usage"] = bool(global_cfg.get("account_usage"))
 
     if platform_key:
         platforms = cfg.get("platforms") or {}
@@ -83,8 +92,10 @@ def resolve_footer_config(
             if isinstance(plat_footer, dict):
                 if "enabled" in plat_footer:
                     resolved["enabled"] = bool(plat_footer.get("enabled"))
-                if isinstance(plat_footer.get("fields"), list) and plat_footer["fields"]:
+                if isinstance(plat_footer.get("fields"), list):
                     resolved["fields"] = [str(f) for f in plat_footer["fields"]]
+                if "account_usage" in plat_footer:
+                    resolved["account_usage"] = bool(plat_footer.get("account_usage"))
 
     return resolved
 
@@ -123,6 +134,58 @@ def format_runtime_footer(
     return _SEP.join(parts)
 
 
+def _format_reset_timestamp(dt: Optional[datetime]) -> str:
+    if not dt:
+        return "unknown"
+    try:
+        return dt.astimezone(ZoneInfo("Asia/Almaty")).strftime("%Y-%m-%d %H:%M %Z")
+    except Exception:
+        return str(dt)
+
+
+def _usage_cache_key(provider: Optional[str], base_url: Optional[str], api_key: Optional[str]) -> tuple[str, str, str]:
+    return (
+        str(provider or "").strip().lower(),
+        str(base_url or "").strip(),
+        "present" if str(api_key or "").strip() else "",
+    )
+
+
+def _get_account_usage_snapshot(
+    *,
+    provider: Optional[str],
+    base_url: Optional[str],
+    api_key: Optional[str],
+) -> Optional[AccountUsageSnapshot]:
+    key = _usage_cache_key(provider, base_url, api_key)
+    now = time.time()
+    cached = _USAGE_CACHE.get(key)
+    if cached and (now - cached[0]) < _USAGE_CACHE_TTL_SECONDS:
+        return cached[1]
+    snapshot = fetch_account_usage(provider, base_url=base_url, api_key=api_key)
+    _USAGE_CACHE[key] = (now, snapshot)
+    return snapshot
+
+
+def format_account_usage_footer(snapshot: Optional[AccountUsageSnapshot]) -> str:
+    if not snapshot or not snapshot.available or not snapshot.windows:
+        return ""
+    parts: list[str] = []
+    for window in snapshot.windows:
+        if window.used_percent is None:
+            continue
+        remaining = max(0, round(100 - float(window.used_percent)))
+        piece = f"{window.label}: {remaining}% left"
+        if window.reset_at:
+            piece += f" until {_format_reset_timestamp(window.reset_at)}"
+        elif window.detail:
+            piece += f" ({window.detail})"
+        parts.append(piece)
+    if not parts:
+        return ""
+    return "Quota: " + _SEP.join(parts)
+
+
 def build_footer_line(
     *,
     user_config: dict[str, Any] | None,
@@ -134,17 +197,33 @@ def build_footer_line(
 ) -> str:
     """Top-level entry point used by gateway/run.py.
 
-    Returns the footer text (empty string when disabled or no data).  Callers
+    Returns the footer text (empty string when disabled or no data). Callers
     append this to the final response themselves, preserving a single blank
     line of separation.
     """
     cfg = resolve_footer_config(user_config, platform_key)
     if not cfg.get("enabled"):
         return ""
-    return format_runtime_footer(
+
+    runtime_footer = format_runtime_footer(
         model=model,
         context_tokens=context_tokens,
         context_length=context_length,
         cwd=cwd,
-        fields=cfg.get("fields") or _DEFAULT_FIELDS,
+        fields=cfg.get("fields", _DEFAULT_FIELDS),
     )
+
+    quota_footer = ""
+    if cfg.get("account_usage"):
+        model_cfg = user_config or {}
+        provider = (model_cfg.get("model") or {}).get("provider")
+        base_url = (model_cfg.get("model") or {}).get("base_url")
+        api_key = (model_cfg.get("model") or {}).get("api_key")
+        snapshot = _get_account_usage_snapshot(provider=provider, base_url=base_url, api_key=api_key)
+        quota_footer = format_account_usage_footer(snapshot)
+
+    if runtime_footer and quota_footer:
+        return f"{runtime_footer}\n{quota_footer}"
+    if quota_footer:
+        return quota_footer
+    return runtime_footer
diff --git a/hermes_cli/config.py b/hermes_cli/config.py
index 262b8f228..f85419689 100644
--- a/hermes_cli/config.py
+++ b/hermes_cli/config.py
@@ -871,6 +871,7 @@ DEFAULT_CONFIG = {
         "runtime_footer": {
             "enabled": False,
             "fields": ["model", "context_pct", "cwd"],  # Order shown; drop any to hide
+            "account_usage": False,
         },
         "copy_shortcut": "auto",  # "auto" (platform default) | "ctrl_c" | "ctrl_shift_c" | "disabled"
     },
PATCH

cd "$REPO"
CURRENT_BRANCH="$(git branch --show-current)"
BEFORE_SHA="$(git rev-parse --short HEAD)"
HAD_LOCAL_PATCH=0
if ! git diff --quiet -- "${TRACKED_FILES[@]}"; then
  HAD_LOCAL_PATCH=1
  for rel in "${TRACKED_FILES[@]}"; do
    mkdir -p "$BACKUP_DIR/$(dirname "$rel")"
    cp "$rel" "$BACKUP_DIR/$rel"
  done
  git checkout -- "${TRACKED_FILES[@]}"
fi

if ! "$HERMES_BIN" update >/tmp/hermes-update-$STAMP.log 2>&1; then
  if [ "$HAD_LOCAL_PATCH" -eq 1 ]; then
    for rel in "${TRACKED_FILES[@]}"; do
      cp "$BACKUP_DIR/$rel" "$rel"
    done
  fi
  echo "Hermes auto-update failed."
  echo "Repo: $REPO"
  echo "Branch: $CURRENT_BRANCH"
  echo "Before: $BEFORE_SHA"
  echo "Log: /tmp/hermes-update-$STAMP.log"
  tail -n 40 /tmp/hermes-update-$STAMP.log || true
  exit 1
fi

AFTER_UPDATE_SHA="$(git rev-parse --short HEAD)"
if [ "$AFTER_UPDATE_SHA" = "$BEFORE_SHA" ]; then
  if [ "$HAD_LOCAL_PATCH" -eq 1 ]; then
    for rel in "${TRACKED_FILES[@]}"; do
      cp "$BACKUP_DIR/$rel" "$rel"
    done
  fi
  echo "Hermes auto-update check: no upstream changes."
  echo "Repo: $REPO"
  echo "Branch: $CURRENT_BRANCH"
  echo "Current: $BEFORE_SHA"
  echo "Patch reapplied: no (not needed)"
  echo "Gateway restarted: no"
  exit 0
fi

if ! git apply --check "$TMP_PATCH" >/tmp/hermes-patch-check-$STAMP.log 2>&1; then
  echo "Hermes updated, but quota patch no longer applies cleanly."
  echo "Repo: $REPO"
  echo "Branch: $CURRENT_BRANCH"
  echo "Before: $BEFORE_SHA"
  echo "After update: $AFTER_UPDATE_SHA"
  echo "Patch file: $TMP_PATCH"
  echo "Check log: /tmp/hermes-patch-check-$STAMP.log"
  tail -n 60 /tmp/hermes-patch-check-$STAMP.log || true
  exit 2
fi

git apply "$TMP_PATCH"
AFTER_PATCH_SHA="$(git rev-parse --short HEAD)"

if ! "$HERMES_BIN" gateway restart >/tmp/hermes-restart-$STAMP.log 2>&1; then
  echo "Hermes updated and patch reapplied, but gateway restart failed."
  echo "Repo: $REPO"
  echo "Branch: $CURRENT_BRANCH"
  echo "Before: $BEFORE_SHA"
  echo "After update: $AFTER_UPDATE_SHA"
  echo "After patch: $AFTER_PATCH_SHA"
  echo "Restart log: /tmp/hermes-restart-$STAMP.log"
  tail -n 40 /tmp/hermes-restart-$STAMP.log || true
  exit 3
fi

echo "Hermes auto-update succeeded."
echo "Repo: $REPO"
echo "Branch: $CURRENT_BRANCH"
echo "Before: $BEFORE_SHA"
echo "After update: $AFTER_UPDATE_SHA"
echo "Patch reapplied: yes"
echo "Gateway restarted: yes"
