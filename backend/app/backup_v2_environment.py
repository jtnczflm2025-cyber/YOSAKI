"""Fail-closed H5-A2 work-directory checks for the local Windows host.

The isolated backup codec can be tested on any platform. HTTP and scheduled
backup entry points must pass this check before creating plaintext snapshots.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
from pathlib import Path

from .backup_v2 import BackupError


_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:YOSAKI_H5A_ACL_TARGET
$rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) |
    ForEach-Object { [pscustomobject]@{ sid = $_.IdentityReference.Value; type = $_.AccessControlType.ToString() } })
[pscustomobject]@{
    current = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    protected = $acl.AreAccessRulesProtected
    rules = $rules
} | ConvertTo-Json -Compress -Depth 4
"""

_VOLUME_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$volume = Get-BitLockerVolume -MountPoint $env:YOSAKI_H5A_MOUNT_POINT
[pscustomobject]@{
    protected = [string]$volume.ProtectionStatus
    status = [string]$volume.VolumeStatus
} | ConvertTo-Json -Compress
"""


def _powershell_json(script: str, variable: str, value: str) -> dict:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    env = os.environ.copy()
    env[variable] = value
    # A parent launched from PowerShell 7 can omit the Windows PowerShell
    # module directory; restore the system module path for powershell.exe.
    modules = os.path.join(env.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "Modules")
    env["PSModulePath"] = modules + os.pathsep + env.get("PSModulePath", "")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                capture_output=True, text=True, timeout=10, env=env, check=False)
        if result.returncode != 0:
            raise ValueError("probe_failed")
        value = json.loads(result.stdout.strip())
        if not isinstance(value, dict):
            raise ValueError("invalid_probe")
        return value
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise BackupError("work_directory_protection_unverified") from exc


def _check_windows_acl(root: Path) -> None:
    info = _powershell_json(_ACL_SCRIPT, "YOSAKI_H5A_ACL_TARGET", str(root))
    if info.get("protected") is not True or not isinstance(info.get("current"), str):
        raise BackupError("unsafe_work_directory_acl")
    allowed = {info["current"], "S-1-3-4", "S-1-5-18", "S-1-5-32-544"}
    rules = info.get("rules")
    if not isinstance(rules, list):
        raise BackupError("unsafe_work_directory_acl")
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != "Allow" or rule.get("sid") not in allowed:
            raise BackupError("unsafe_work_directory_acl")


def _check_windows_encryption(root: Path) -> None:
    # EFS is directory-specific. Otherwise require a fully encrypted,
    # protected BitLocker volume; unreadable state is not an attestation.
    if getattr(root.stat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_ENCRYPTED", 0x4000):
        return
    try:
        info = _powershell_json(_VOLUME_SCRIPT, "YOSAKI_H5A_MOUNT_POINT", root.anchor.rstrip("\\"))
    except BackupError as exc:
        raise BackupError("encrypted_volume_unverified") from exc
    if info.get("protected", "").lower() != "on" or info.get("status", "").lower() != "fullyencrypted":
        raise BackupError("encrypted_volume_unverified")


def validate_work_root(root: Path) -> Path:
    root = Path(root).expanduser()
    if root.is_symlink():
        raise BackupError("unsafe_work_directory_acl")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = root.resolve()
    if os.name != "nt":
        # The Docker volume's host encryption cannot be inferred from inside
        # the container. A separately reviewed host attestation is required.
        raise BackupError("encrypted_volume_unverified")
    _check_windows_acl(root)
    _check_windows_encryption(root)
    return root
