"""``hermes role`` subcommand — role package lifecycle CLI.

Commands:
  hermes role install <path> [--force]
  hermes role list
  hermes role info <name>
  hermes role validate <path-or-name>
  hermes role remove <name>

Handler injected from main.py to avoid circular imports.
"""

from __future__ import annotations

import sys
from typing import Callable


def build_role_parser(subparsers, *, cmd_role: Callable) -> None:
    """Attach the ``role`` subcommand tree to ``subparsers``."""
    role_parser = subparsers.add_parser(
        "role",
        help="Manage installed role packages (advisory extensions to Hermes roles)",
        description=(
            "Install, list, inspect, validate, and remove role packages — "
            "self-contained extensions that add advisory roles to Hermes without "
            "modifying built-in routing."
        ),
    )
    role_parser.set_defaults(func=cmd_role)
    role_subs = role_parser.add_subparsers(dest="role_action")

    # install
    install_p = role_subs.add_parser(
        "install",
        help="Install a role package from a local directory",
        description="Copy a local role package directory into ~/.hermes/role-packages/ and register it.",
    )
    install_p.add_argument(
        "path",
        help="Path to the role package directory (must contain role-package.yaml)",
    )
    install_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing installation with the same package name",
    )

    # list
    role_subs.add_parser(
        "list",
        help="List all installed role packages",
    )

    # info
    info_p = role_subs.add_parser(
        "info",
        help="Show details for an installed role package",
    )
    info_p.add_argument("name", help="Package name (as shown by 'hermes role list')")

    # validate
    validate_p = role_subs.add_parser(
        "validate",
        help="Validate a role package manifest",
        description=(
            "Validate a local directory or an installed package by name. "
            "Exits non-zero on hard errors."
        ),
    )
    validate_p.add_argument(
        "path_or_name",
        help="Local directory path containing role-package.yaml, or installed package name",
    )

    # remove
    remove_p = role_subs.add_parser(
        "remove",
        help="Remove an installed role package",
    )
    remove_p.add_argument("name", help="Package name to remove")


def cmd_role(args) -> None:
    """Dispatch ``hermes role`` subcommands."""
    from hermes_cli.role_packages import (
        RolePackageError,
        default_hermes_home,
        get_package_info,
        get_role_packages_dir,
        install_package,
        list_packages,
        remove_package,
        validate_manifest_path,
    )
    from pathlib import Path

    action = getattr(args, "role_action", None)

    if action is None:
        # Bare `hermes role` — show brief help.
        print("Usage: hermes role <command>")
        print("")
        print("Commands:")
        print("  install <path> [--force]   Install a role package from a local directory")
        print("  list                       List installed role packages")
        print("  info <name>                Show details for an installed package")
        print("  validate <path-or-name>    Validate a manifest (local path or package name)")
        print("  remove <name>              Remove an installed package")
        return

    hermes_home = default_hermes_home()

    if action == "install":
        _cmd_install(args, hermes_home)
    elif action == "list":
        _cmd_list(hermes_home)
    elif action == "info":
        _cmd_info(args, hermes_home)
    elif action == "validate":
        _cmd_validate(args, hermes_home)
    elif action == "remove":
        _cmd_remove(args, hermes_home)
    else:
        print(f"Unknown role command: {action!r}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Sub-action handlers
# ---------------------------------------------------------------------------


def _cmd_install(args, hermes_home) -> None:
    from hermes_cli.role_packages import RolePackageError, install_package
    from pathlib import Path

    source = Path(args.path).resolve()
    force = getattr(args, "force", False)
    try:
        pkg = install_package(source, hermes_home, force=force)
    except RolePackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"installed: {pkg.name} v{pkg.version}  (role_id={pkg.role_id}, boundary_mode={pkg.boundary_mode})")
    print(f"  path: {pkg.source_dir}")
    print("")
    print("Note: package roles are advisory only and not yet active in routing.")


def _cmd_list(hermes_home) -> None:
    from hermes_cli.role_packages import list_packages

    packages = list_packages(hermes_home)
    if not packages:
        print("No role packages installed.")
        return

    header = f"{'NAME':<24} {'VERSION':<12} {'STATUS':<12} {'ROLE_ID':<24} {'BOUNDARY'}"
    print(header)
    print("-" * len(header))
    for p in packages:
        name = p.get("name", "?")
        version = p.get("version", "?")
        status = p.get("status", "?")
        role_id = p.get("role_id", "")
        boundary = p.get("boundary_mode", "")
        print(f"{name:<24} {version:<12} {status:<12} {role_id:<24} {boundary}")


def _cmd_info(args, hermes_home) -> None:
    from hermes_cli.role_packages import get_package_info

    info = get_package_info(args.name, hermes_home)
    if info is None:
        print(f"error: package {args.name!r} is not installed", file=sys.stderr)
        sys.exit(1)

    pairs = [
        ("name", info.get("name", args.name)),
        ("version", info.get("version", "?")),
        ("status", info.get("status", "?")),
        ("source_type", info.get("source_type", "?")),
        ("source_path", info.get("source_path", "?")),
        ("role_id", info.get("role_id", "")),
        ("canonical_id", info.get("canonical_id", "")),
        ("boundary_mode", info.get("boundary_mode", "")),
        ("install_path", info.get("install_path", "")),
        ("installed_at", info.get("installed_at", "")),
    ]
    for key, val in pairs:
        if val:
            print(f"  {key:<20} {val}")

    for err in info.get("validation_errors", []):
        print(f"  ERROR: {err}", file=sys.stderr)
    for warn in info.get("validation_warnings", []):
        print(f"  WARNING: {warn}")

    if info.get("_note"):
        print(f"  note: {info['_note']}")


def _cmd_validate(args, hermes_home) -> None:
    from hermes_cli.role_packages import (
        get_role_packages_dir,
        validate_manifest_path,
    )
    from pathlib import Path

    target = Path(args.path_or_name)
    if not target.is_dir():
        # Try as installed package name.
        installed_dir = get_role_packages_dir(hermes_home) / args.path_or_name
        if installed_dir.is_dir():
            target = installed_dir
        else:
            print(f"error: {args.path_or_name!r} is not a directory and not an installed package name", file=sys.stderr)
            sys.exit(1)

    manifest, errors, warnings = validate_manifest_path(target, check_overlap=True)

    for warn in warnings:
        print(f"warning: {warn}")
    for err in errors:
        print(f"error: {err}", file=sys.stderr)

    if errors:
        sys.exit(1)

    pkg = manifest["package"]  # type: ignore[index]
    role = manifest["role"]    # type: ignore[index]
    print(
        f"valid: {pkg['name']} v{pkg['version']}  "
        f"(role_id={role['id']}, boundary_mode={manifest.get('boundary_mode', 'advisory')})"
    )


def _cmd_remove(args, hermes_home) -> None:
    from hermes_cli.role_packages import RolePackageError, remove_package

    try:
        remove_package(args.name, hermes_home)
    except RolePackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"removed: {args.name}")
