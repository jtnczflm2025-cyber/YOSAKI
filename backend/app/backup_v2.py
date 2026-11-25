"""Bounded-memory, authenticated YOSAKI SQLite backup files.

V2 is one AES-GCM message, not an independently encrypted chunk format.  The
plaintext is never opened as SQLite until the final authentication tag passes.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import binascii
import hashlib
import io
import json
import os
import shutil
import sqlite3
import struct
import tempfile
import time
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


MAGIC = b"YOSAKI-ENCRYPTED-BACKUP-V2\n"
CHUNK = 1024 * 1024
TAG_BYTES = 16
MAX_HEADER = 2048
MAX_MANIFEST = 1024 * 1024
MAX_DATABASE = 16 * 1024**3
MAX_BACKUP = 16 * 1024**3
KDF_ITERATIONS = 600_000
REQUIRED = {"users", "characters", "conversations", "messages", "model_profiles", "mcp_servers", "plugins"}


class BackupError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _guard(check: Callable[[], None] | None) -> None:
    if check is not None:
        check()


def _json(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode(value: str, length: int) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise BackupError("invalid_header") from exc
    if len(raw) != length:
        raise BackupError("invalid_header")
    return raw


def _key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    if len(passphrase) < 12:
        raise BackupError("passphrase_too_short")
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=iterations).derive(passphrase.encode("utf-8"))


def _tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _inventory(db: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for name in sorted(_tables(db) - {"sqlite_sequence"}):
        # Names come from SQLite's own schema, but still quote identifiers.
        result[name] = db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
    return result


def _schema_hash(db: sqlite3.Connection) -> str:
    statements = [row[0] for row in db.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")]
    return hashlib.sha256(_json({"sql": statements})).hexdigest()


def _read_source_audit(path: Path) -> dict[str, int]:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        return _source_audit(db, _tables(db))
    finally:
        db.close()


def _source_audit(db: sqlite3.Connection, tables: set[str]) -> dict[str, int]:
    """Validate ownership and count old derived references needing revalidation."""
    from . import memory_v2_sources

    checked = stale = 0
    queries = []
    if {"mv3_source_refs", "mv3_versions", "mv3_derivations"}.issubset(tables):
        queries.append("""SELECT r.source_kind,r.source_id,r.character_id,r.content_hash,r.state_hash,
            d.character_id AS parent_character FROM mv3_source_refs r
            JOIN mv3_versions v ON v.id=r.version_id JOIN mv3_derivations d ON d.id=v.derivation_id""")
    if {"mv3_entry_sources", "mv3_entry_versions", "mv3_entries"}.issubset(tables):
        queries.append("""SELECT r.source_kind,r.source_id,r.character_id,r.content_hash,r.state_hash,
            e.character_id AS parent_character FROM mv3_entry_sources r
            JOIN mv3_entry_versions v ON v.id=r.version_id JOIN mv3_entries e ON e.id=v.entry_id""")
    if "mv2_sources" in tables:
        queries.append("""SELECT source_kind,source_id,character_id,content_hash,state_hash,
            character_id AS parent_character FROM mv2_sources""")
    for query in queries:
        for ref in db.execute(query):
            checked += 1
            if ref["character_id"] != ref["parent_character"]:
                raise BackupError("cross_character_reference")
            source = memory_v2_sources.source(db, ref["source_kind"], ref["source_id"])
            if source is None:
                stale += 1
                continue
            if source.character_id != ref["character_id"]:
                raise BackupError("cross_character_reference")
            if source.content_hash != ref["content_hash"] or source.state_hash != ref["state_hash"]:
                stale += 1
    return {"checked": checked, "stale_or_inaccessible": stale}


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _check_database(path: Path, expected: dict | None = None,
                    expected_schema: str | None = None) -> dict:
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("sqlite_integrity_failed")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise BackupError("sqlite_foreign_key_failed")
        tables = _tables(db)
        if not REQUIRED.issubset(tables):
            raise BackupError("missing_required_tables")
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] != 1:
            raise BackupError("invalid_owner_count")
        if db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] != 0:
            raise BackupError("credentials_present")
        if db.execute("SELECT COUNT(*) FROM auth_login_attempts").fetchone()[0] != 0:
            raise BackupError("credentials_present")
        if db.execute("SELECT COUNT(*) FROM model_profiles WHERE api_key_encrypted IS NOT NULL").fetchone()[0]:
            raise BackupError("credentials_present")
        if db.execute("SELECT COUNT(*) FROM mcp_servers WHERE auth_secret_encrypted IS NOT NULL OR session_id IS NOT NULL").fetchone()[0]:
            raise BackupError("credentials_present")
        if "mv2_import_receipts" in tables:
            rows = db.execute("""SELECT r.content_sha256,m.content,r.character_id,c.character_id,f.character_id,
                f.conversation_id,m.conversation_id FROM mv2_import_receipts r
                JOIN messages m ON m.id=r.message_id JOIN conversations c ON c.id=m.conversation_id
                JOIN mv2_import_files f ON f.id=r.file_id""")
            for content_hash, content, receipt_char, conv_char, file_char, file_conv, message_conv in rows:
                if (hashlib.sha256(content.encode("utf-8")).hexdigest() != content_hash
                        or receipt_char != conv_char or file_char != conv_char or file_conv != message_conv):
                    raise BackupError("import_receipt_mismatch")
        inventory = _inventory(db)
        if expected is not None and inventory != expected:
            raise BackupError("table_inventory_mismatch")
        if expected_schema is not None and _schema_hash(db) != expected_schema:
            raise BackupError("schema_mismatch")
        _source_audit(db, tables)
        return inventory
    except sqlite3.DatabaseError as exc:
        raise BackupError("sqlite_validation_failed") from exc
    finally:
        db.close()


def _snapshot(source_path: Path, snapshot_path: Path, check: Callable[[], None] | None = None) -> None:
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(snapshot_path)
    try:
        source.backup(target, pages=256, sleep=0.05,
                      progress=(lambda *_: _guard(check)) if check else None)
        _guard(check)
        target.execute("PRAGMA secure_delete=ON")
        names = _tables(target)
        if "model_profiles" in names:
            target.execute("UPDATE model_profiles SET api_key_encrypted=NULL")
        if "mcp_servers" in names:
            target.execute("UPDATE mcp_servers SET auth_secret_encrypted=NULL,session_id=NULL,status='disconnected',last_error=NULL")
        if "auth_sessions" in names:
            target.execute("DELETE FROM auth_sessions")
        if "auth_login_attempts" in names:
            target.execute("DELETE FROM auth_login_attempts")
        from .voice.backup_policy import sanitize_voice_snapshot
        sanitize_voice_snapshot(target)
        target.commit()
        # Rebuild the file so deleted secrets are absent from final free pages.
        target.execute("VACUUM")
    finally:
        target.close()
        source.close()


def _check_space(path: Path, need: int) -> None:
    if shutil.disk_usage(path).free < need:
        raise BackupError("insufficient_space")


def _work_dir(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(root, 0o700)
    # Crash leftovers may contain plaintext. Restrict cleanup to this exact
    # dedicated directory and our known temporary prefixes.
    resolved = root.resolve()
    cutoff = time.time() - 24 * 3600
    for child in root.iterdir():
        if (not child.name.startswith(("backup-", "restore-", "upload-"))
                or child.is_symlink() or not child.is_dir()):
            continue
        try:
            marker = child / ".yosaki-h5a-temporary"
            if (child.resolve().parent == resolved and child.stat().st_mtime < cutoff
                    and marker.is_file() and not marker.is_symlink()
                    and marker.read_bytes() == b"YOSAKI-H5A-TEMP\n"):
                shutil.rmtree(child)
        except OSError:
            pass


def mark_temporary_directory(path: Path) -> None:
    (Path(path) / ".yosaki-h5a-temporary").write_bytes(b"YOSAKI-H5A-TEMP\n")


@contextmanager
def temporary_directory(root: Path, prefix: str):
    with tempfile.TemporaryDirectory(prefix=prefix, dir=root) as scratch:
        mark_temporary_directory(Path(scratch))
        yield scratch


def _encode(snapshot: Path, manifest: dict, destination: Path, passphrase: str,
            check: Callable[[], None] | None = None) -> None:
    salt, nonce = os.urandom(16), os.urandom(12)
    header = _json({"cipher": "AES-256-GCM", "compression": "zlib", "kdf": "PBKDF2-HMAC-SHA256",
                    "iterations": KDF_ITERATIONS, "salt": _b64(salt), "nonce": _b64(nonce), "version": 2})
    if len(header) > MAX_HEADER:
        raise BackupError("invalid_header")
    prefix = MAGIC + struct.pack(">H", len(header)) + header
    encryptor = Cipher(algorithms.AES(_key(passphrase, salt, KDF_ITERATIONS)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(prefix)
    compressor = zlib.compressobj(level=6)
    manifest_bytes = _json(manifest)
    if len(manifest_bytes) > MAX_MANIFEST:
        raise BackupError("manifest_too_large")
    with destination.open("xb") as output:
        output.write(prefix)
        output.write(encryptor.update(compressor.compress(struct.pack(">I", len(manifest_bytes)) + manifest_bytes)))
        with snapshot.open("rb") as source:
            while block := source.read(CHUNK):
                _guard(check)
                output.write(encryptor.update(compressor.compress(block)))
        _guard(check)
        output.write(encryptor.update(compressor.flush()) + encryptor.finalize())
        output.write(encryptor.tag)
        output.flush()
        os.fsync(output.fileno())


def _decode_file(backup: Path, destination: Path, passphrase: str,
                 check: Callable[[], None] | None = None,
                 max_database_bytes: int | None = None) -> dict:
    size = backup.stat().st_size
    if size > MAX_BACKUP or size < len(MAGIC) + 2 + TAG_BYTES + 1:
        raise BackupError("invalid_length")
    with backup.open("rb") as source:
        prefix = source.read(len(MAGIC))
        if prefix != MAGIC:
            raise BackupError("unsupported_format")
        length_bytes = source.read(2)
        if len(length_bytes) != 2:
            raise BackupError("truncated")
        length = struct.unpack(">H", length_bytes)[0]
        if not (0 < length <= MAX_HEADER):
            raise BackupError("invalid_header")
        header_bytes = source.read(length)
        if len(header_bytes) != length:
            raise BackupError("truncated")
        try:
            header = json.loads(header_bytes)
            if (set(header) != {"cipher", "compression", "kdf", "iterations", "salt", "nonce", "version"}
                    or header["cipher"] != "AES-256-GCM" or header["compression"] != "zlib"
                    or header["kdf"] != "PBKDF2-HMAC-SHA256" or header["version"] != 2
                    or type(header["iterations"]) is not int or header["iterations"] != KDF_ITERATIONS):
                raise BackupError("invalid_header")
            salt, nonce = _decode(header["salt"], 16), _decode(header["nonce"], 12)
        except (UnicodeError, ValueError, TypeError, KeyError) as exc:
            raise BackupError("invalid_header") from exc
        aad = prefix + length_bytes + header_bytes
        source.seek(size - TAG_BYTES)
        tag = source.read(TAG_BYTES)
        source.seek(len(aad))
        remaining = size - len(aad) - TAG_BYTES
        if remaining <= 0:
            raise BackupError("truncated")
        decryptor = Cipher(algorithms.AES(_key(passphrase, salt, KDF_ITERATIONS)), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(aad)
        compressed = destination.with_name(destination.name + ".compressed")
        try:
            with compressed.open("xb") as output:
                while remaining:
                    _guard(check)
                    block = source.read(min(CHUNK, remaining))
                    if not block:
                        raise BackupError("truncated")
                    remaining -= len(block)
                    output.write(decryptor.update(block))
                decryptor.finalize()
                output.flush()
                os.fsync(output.fileno())
        except InvalidTag as exc:
            compressed.unlink(missing_ok=True)
            raise BackupError("authentication_or_content_failed") from exc
        except BaseException:
            compressed.unlink(missing_ok=True)
            raise
        try:
            decompressor = zlib.decompressobj()
            buffer = bytearray()
            manifest = None
            written = 0

            def consume(clear: bytes, output) -> None:
                nonlocal manifest, written
                if manifest is None:
                    buffer.extend(clear)
                    if len(buffer) < 4:
                        return
                    mlen = struct.unpack(">I", buffer[:4])[0]
                    if mlen > MAX_MANIFEST:
                        raise BackupError("invalid_manifest")
                    if len(buffer) < 4 + mlen:
                        return
                    try:
                        manifest = json.loads(bytes(buffer[4:4 + mlen]))
                    except (UnicodeError, ValueError, TypeError) as exc:
                        raise BackupError("invalid_manifest") from exc
                    if (not isinstance(manifest, dict) or type(manifest.get("database_bytes")) is not int
                            or not (0 < manifest["database_bytes"] <= MAX_DATABASE)):
                        raise BackupError("invalid_manifest")
                    if max_database_bytes is not None and manifest["database_bytes"] > max_database_bytes:
                        raise BackupError("operational_capacity_exceeded")
                    _check_space(destination.parent, 2 * manifest["database_bytes"] + 64 * 1024**2)
                    clear = bytes(buffer[4 + mlen:])
                    buffer.clear()
                written += len(clear)
                if written > MAX_DATABASE or written > manifest["database_bytes"]:
                    raise BackupError("database_too_large")
                output.write(clear)

            with compressed.open("rb") as input_file, destination.open("xb") as output:
                while block := input_file.read(CHUNK):
                    _guard(check)
                    data = block
                    while data:
                        _guard(check)
                        clear = decompressor.decompress(data, CHUNK)
                        consume(clear, output)
                        data = decompressor.unconsumed_tail
                        if not data:
                            break
                    if decompressor.unused_data:
                        raise BackupError("extra_content")
                while clear := decompressor.flush(CHUNK):
                    consume(clear, output)
                output.flush()
                os.fsync(output.fileno())
            if manifest is None or buffer or not decompressor.eof or decompressor.unused_data:
                raise BackupError("truncated_or_extra_content")
        except zlib.error as exc:
            raise BackupError("invalid_compression") from exc
        finally:
            compressed.unlink(missing_ok=True)
    if (not isinstance(manifest, dict) or manifest.get("format") != "yosaki-full-backup" or manifest.get("version") != 2
            or manifest.get("database_bytes") != written or manifest.get("database_sha256") != _sha_file(destination)):
        raise BackupError("manifest_mismatch")
    return manifest


def decode_v1(encrypted: bytes, passphrase: str) -> tuple[bytes, dict]:
    """Read legacy Fernet backups without importing the live application."""
    magic = b"YOSAKI-ENCRYPTED-BACKUP-V1\n"
    if len(encrypted) > 128 * 1024**2 or not encrypted.startswith(magic):
        raise BackupError("unsupported_format")
    salt = encrypted[len(magic):len(magic) + 16]
    if len(salt) != 16:
        raise BackupError("truncated")
    key = base64.urlsafe_b64encode(_key(passphrase, salt, KDF_ITERATIONS))
    try:
        plain = Fernet(key).decrypt(encrypted[len(magic) + 16:])
    except InvalidToken as exc:
        raise BackupError("authentication_or_content_failed") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(plain)) as archive:
            if set(archive.namelist()) != {"database.sqlite3", "manifest.json"}:
                raise BackupError("invalid_legacy_members")
            if archive.getinfo("database.sqlite3").file_size > 64 * 1024**2:
                raise BackupError("legacy_database_too_large")
            manifest = json.loads(archive.read("manifest.json"))
            raw = archive.read("database.sqlite3")
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise BackupError("invalid_legacy_archive") from exc
    if (not isinstance(manifest, dict) or manifest.get("format") != "yosaki-full-backup"
            or manifest.get("version") != 1 or not raw.startswith(b"SQLite format 3\x00")):
        raise BackupError("invalid_legacy_archive")
    return raw, manifest


def restore_to_directory(backup: Path, passphrase: str, target: Path, work_root: Path,
                         check: Callable[[], None] | None = None,
                         max_database_bytes: int | None = None) -> dict:
    """Restore into a *new empty directory*; never replace an active database."""
    backup, target, work_root = Path(backup), Path(target), Path(work_root)
    if target.exists():
        raise BackupError("target_exists")
    _work_dir(work_root)
    with temporary_directory(work_root, "restore-") as scratch:
        candidate = Path(scratch) / "candidate.sqlite3"
        with backup.open("rb") as source:
            magic = source.read(len(MAGIC))
        if magic == MAGIC:
            manifest = _decode_file(backup, candidate, passphrase, check, max_database_bytes)
        else:
            if backup.stat().st_size > 128 * 1024 * 1024:
                raise BackupError("legacy_file_too_large")
            raw, manifest = decode_v1(backup.read_bytes(), passphrase)
            if max_database_bytes is not None and len(raw) > max_database_bytes:
                raise BackupError("operational_capacity_exceeded")
            with candidate.open("xb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        counts = _check_database(candidate, manifest.get("tables") if manifest.get("version") == 2 else None,
                                 manifest.get("schema_sha256") if manifest.get("version") == 2 else None)
        _check_space(target.parent, candidate.stat().st_size + 64 * 1024**2)
        target.mkdir(mode=0o700)
        try:
            with candidate.open("rb") as input_file, (target / "yosaki.sqlite3").open("xb") as restored:
                if check is None:
                    shutil.copyfileobj(input_file, restored, CHUNK)
                else:
                    while block := input_file.read(CHUNK):
                        _guard(check)
                        restored.write(block)
                restored.flush()
                os.fsync(restored.fileno())
            _check_database(target / "yosaki.sqlite3", counts)
            source_audit = _read_source_audit(target / "yosaki.sqlite3")
            if manifest.get("version") == 2 and "source_audit" in manifest and source_audit != manifest["source_audit"]:
                raise BackupError("restored_source_audit_mismatch")
        except BaseException:
            shutil.rmtree(target)
            raise
    return {"format_version": manifest.get("version"), "exported_at": manifest.get("exported_at"),
            "database_sha256": _sha_file(target / "yosaki.sqlite3"), "tables": counts,
            "source_audit": source_audit,
            "credentials_excluded": True}


def create_backup(source: Path, destination: Path, passphrase: str, work_root: Path,
                  check: Callable[[], None] | None = None,
                  max_database_bytes: int | None = None) -> dict:
    """Create and publish a V2 file only after independent restore verification."""
    source, destination, work_root = Path(source), Path(destination), Path(work_root)
    if destination.exists():
        raise BackupError("destination_exists")
    _work_dir(work_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = source.stat().st_size
    if size > MAX_DATABASE:
        raise BackupError("database_too_large")
    if max_database_bytes is not None and size > max_database_bytes:
        raise BackupError("operational_capacity_exceeded")
    _check_space(work_root, size * 3 + 64 * 1024**2)
    _check_space(destination.parent, size + 64 * 1024**2)
    with temporary_directory(work_root, "backup-") as scratch:
        snapshot = Path(scratch) / "snapshot.sqlite3"
        if check is None:
            _snapshot(source, snapshot)
        else:
            _snapshot(source, snapshot, check)
        counts = _check_database(snapshot)
        if snapshot.stat().st_size > MAX_DATABASE:
            raise BackupError("database_too_large")
        if max_database_bytes is not None and snapshot.stat().st_size > max_database_bytes:
            raise BackupError("operational_capacity_exceeded")
        _check_space(work_root, snapshot.stat().st_size * 2 + 64 * 1024**2)
        _check_space(destination.parent, snapshot.stat().st_size + 64 * 1024**2)
        schema = sqlite3.connect(snapshot)
        try:
            schema_hash = _schema_hash(schema)
        finally:
            schema.close()
        manifest = {"format": "yosaki-full-backup", "version": 2,
                    "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "database_sha256": _sha_file(snapshot), "database_bytes": snapshot.stat().st_size,
                    "schema_sha256": schema_hash, "tables": counts,
                    "source_audit": _read_source_audit(snapshot), "secrets_included": False}
        partial = destination.with_name(destination.name + "." + os.urandom(8).hex() + ".partial")
        try:
            if check is None:
                _encode(snapshot, manifest, partial, passphrase)
            else:
                _encode(snapshot, manifest, partial, passphrase, check)
            restored_dir = Path(scratch) / "verified"
            result = restore_to_directory(partial, passphrase, restored_dir, work_root,
                                          check, max_database_bytes)
            _guard(check)
            if result["database_sha256"] != manifest["database_sha256"]:
                raise BackupError("restored_hash_mismatch")
            if result["source_audit"] != manifest["source_audit"]:
                raise BackupError("restored_source_audit_mismatch")
            os.link(partial, destination)
            return manifest
        finally:
            partial.unlink(missing_ok=True)
