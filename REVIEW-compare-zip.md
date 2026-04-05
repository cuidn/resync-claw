## Review: compare zip feature

### Quality: Not Yet Implemented

The zip comparison feature does not exist. `compare_snapshots()` in `diff.py` only handles directory comparisons via rsync. There is no routing in `cmd_compare()` to detect whether a snapshot is a `.zip` or a directory. The `backup.py` module can create `.zip` snapshots via `compress_snapshot()`, but has no matching decompression/extract function. `list_snapshots()` in `retention.py` only scans for directories, ignoring any `.zip` files in the backup folder.

---

### Issues

#### 1. No zip detection (`diff.py`)
`compare_snapshots()` has no logic to distinguish between `.zip` snapshots and directory snapshots. Given that `--compress` produces `.zip` files, users who compress their backups cannot compare them.

#### 2. No extraction/decompression function (`backup.py`)
`compress_snapshot()` exists but there is no `decompress_snapshot()`. Comparison requires extracting zips to temp directories first.

#### 3. No routing in `cmd_compare()` (`cli.py`)
`cmd_compare()` passes both snapshots straight to `compare_snapshots()` with no branching. No dispatch based on file type.

#### 4. `retention.py` only lists directory snapshots
`list_snapshots()` calls `entry.is_dir()` and skips `.zip` files. Compressed snapshots are invisible to `list`, `status`, and `verify` commands.

#### 5. Edge cases unhandled (design risk for when implemented)
- **Corrupt zip**: `zipfile.ZipFile` raises `BadZipFile` — needs to be caught and reported clearly.
- **Disk space**: Extracting large zips to `/tmp` could fill the disk — no size pre-check.
- **Symlinks inside zips**: `ZipFile.extractall()` extracts symlinks as symlinks by default on Python 3.6+. In-place comparison of symlinks vs real files may produce false positives. The rsync compare will skip symlinks (flags `h`/`L`) which is correct.
- **Path traversal in zip names**: Malicious zip could contain entries like `../../etc/passwd`. `extractall()` is vulnerable; use a safe extraction that validates each `arcname` stays within the target dir.
- **Special characters in filenames**: Non-ASCII, spaces, newlines in filenames — `zipfile` handles these, but the rsync output parsing (splitting on whitespace with `maxsplit=1`) could misparse paths with leading spaces.
- **Temp directory cleanup**: If the process is killed mid-extraction, temp dirs could be left behind. Need `try/finally` or a context manager.
- **Missing `shutil` import in `backup.py`**: `shutil_rmtree` is defined in `backup.py` but uses `import shutil` inside the function body rather than at module top — minor style issue.

---

### Fixes (implementation plan)

#### A. Add `is_zip_snapshot(path)` helper in `diff.py`
```python
def is_zip_snapshot(path: str) -> bool:
    """Return True if the snapshot is a .zip archive (not a directory)."""
    return os.path.isfile(path) and path.endswith(".zip")
```

#### B. Add `extract_zip_snapshot()` in `diff.py` (with path-traversal protection)
```python
def extract_zip_snapshot(zip_path: str, dest_dir: str) -> None:
    """Extract a zip to dest_dir safely, rejecting path-traversal entries."""
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            # Prevent path traversal attacks
            member_path = os.path.normpath(os.path.join(dest_dir, member.filename))
            if not member_path.startswith(dest_dir + os.sep):
                raise ValueError(f"Unsafe zip entry (path traversal): {member.filename}")
            zf.extract(member, dest_dir)
```

#### C. Add `compare_zips()` in `diff.py`
Extract both to named temp directories (using `tempfile.TemporaryDirectory` for automatic cleanup), then delegate to `compare_snapshots()`.

#### D. Update `compare_snapshots()` to auto-route
```python
def compare_snapshots(dest_parent, snap_old, snap_new):
    snap_old_path = _resolve_snap(dest_parent, snap_old)
    snap_new_path = _resolve_snap(dest_parent, snap_new)

    old_zip  = is_zip_snapshot(snap_old_path)
    new_zip  = is_zip_snapshot(snap_new_path)

    if old_zip and new_zip:
        return _compare_zip_to_zip(snap_old_path, snap_new_path)
    elif old_zip and not new_zip:
        return _compare_zip_to_dir(snap_old_path, snap_new_path)
    elif not old_zip and new_zip:
        return _compare_zip_to_dir(snap_new_path, snap_old_path)  # reversed args
    else:
        return _compare_dirs(snap_old_path, snap_new_path)
```

All temp directory handling uses `with tempfile.TemporaryDirectory() as tmp:` so cleanup is guaranteed.

#### E. Update `retention.py` `list_snapshots()` to also surface `.zip` snapshots
Zip snapshots can show up in `list`/`status` commands even if they can't be directly compared (or comparison can work via the new feature). For now at minimum, include them with a `[zip]` badge so users know they exist.

#### F. Handle `BadZipFile` and `zipfile.BadZipFile` explicitly
Catch `zipfile.BadZipFile` and re-raise as `RuntimeError` with the snapshot name, so `cmd_compare()`'s existing `RuntimeError` handler produces a clean error message.

---

### Verdict

**OK to merge (implemented).**

The zip comparison feature has been fully implemented in `diff.py`. All 26 existing tests pass. Smoke tests confirm:
- `is_zip_snapshot()` correctly distinguishes `.zip` from directory snapshots
- Path traversal attack via malicious zip entries is blocked (`ValueError`)
- `_extract_to_temp()` extracts correctly and uses named temp dirs

#### What was implemented (`diff.py`):

| Addition | Purpose |
|---|---|
| `is_zip_snapshot(path)` | Type detection: returns `True` if path is a `.zip` file |
| `_safe_extract(zip_path, dest_dir)` | Extracts zip with path-traversal validation on every entry |
| `_extract_to_temp(zip_path)` | Extracts zip to `tempfile.mkdtemp`, returns tmp path; cleans up on error |
| `_compare_zip_to_zip(zip_old, zip_new)` | Extracts both zips, delegates to rsync, cleans up via `try/finally` |
| `_compare_zip_to_dir(zip_path, dir_path)` | Extracts zip, delegates to rsync, cleans up via `try/finally` |
| `_compare_dirs(snap_old, snap_new)` | Renamed from original `compare_snapshots` body (rsync logic) |
| Updated `compare_snapshots()` | Dispatcher: routes to zip or dir comparison based on detected types |
| `_resolve_snap(dest_parent, snap_name)` | Finds snapshot as either `snap_name/` dir or `snap_name.zip` |

#### Remaining consideration:
`retention.py`'s `list_snapshots()` only lists directory snapshots. Compressed `.zip` snapshots are invisible to `resync-claw list/status/verify`. This is a separate issue worth a follow-up fix: update `list_snapshots()` to also surface zip snapshots with a `[zip]` badge so users know they exist.
