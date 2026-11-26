"""H5-A2 work-root checks use only synthetic temporary directories."""
import os
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from backend.app import backup_v2_environment
from backend.app.backup_v2 import BackupError


@unittest.skipUnless(os.name == "nt", "Windows ACL target check")
class BackupEnvironmentTests(unittest.TestCase):
    def test_new_directory_acl_and_unknown_volume(self):
        with tempfile.TemporaryDirectory(prefix="h5a2-acl-") as temp:
            root = Path(temp) / "new-work"
            with patch.object(backup_v2_environment, "_check_windows_encryption"):
                self.assertEqual(backup_v2_environment.validate_work_root(root), root.resolve())
            with patch.object(backup_v2_environment, "_check_windows_encryption",
                              side_effect=BackupError("encrypted_volume_unverified")):
                with self.assertRaisesRegex(BackupError, "encrypted_volume_unverified"):
                    backup_v2_environment.validate_work_root(root)

    def test_preexisting_broad_acl_rejected(self):
        with tempfile.TemporaryDirectory(prefix="h5a2-acl-") as temp:
            root = Path(temp) / "preexisting"
            root.mkdir()  # Inherits test parent ACL rather than mode=0o700.
            with patch.object(backup_v2_environment, "_check_windows_encryption"):
                with self.assertRaisesRegex(BackupError, "unsafe_work_directory_acl"):
                    backup_v2_environment.validate_work_root(root)

    def test_restore_cli_refuses_plaintext_target_outside_protected_root(self):
        from scripts import restore_backup_isolated
        with tempfile.TemporaryDirectory(prefix="h5a2-restore-cli-") as temp:
            root = Path(temp) / "protected"
            root.mkdir()
            target = Path(temp) / "outside"
            argv = ["restore_backup_isolated.py", "--backup", str(Path(temp) / "synthetic.ybackup"),
                    "--target", str(target), "--work-dir", str(root)]
            with patch.object(sys, "argv", argv), \
                    patch.object(restore_backup_isolated, "validate_work_root", return_value=root.resolve()), \
                    patch.object(restore_backup_isolated, "restore_to_directory") as restore, \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(restore_backup_isolated.main(), 1)
                restore.assert_not_called()
