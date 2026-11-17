#!/usr/bin/env bash
# Restore only data and configuration into a NEW directory. Never execute archive content.
set -euo pipefail
umask 077
if [[ $# -ne 2 ]]; then
  echo 'Usage: bash scripts/restore.sh BACKUP.tar.gz NEW_RESTORE_DIRECTORY' >&2
  exit 2
fi
python3 - "$1" "$2" <<'PY'
import json, os, pathlib, shutil, sys, tarfile, tempfile
archive_path = pathlib.Path(sys.argv[1]).expanduser().resolve(strict=True)
target = pathlib.Path(sys.argv[2]).expanduser().absolute()
if target.exists() or target.is_symlink():
    raise SystemExit('Refusing to overwrite an existing restore directory')
target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
staging = pathlib.Path(tempfile.mkdtemp(prefix='.grader-restore-', dir=target.parent))
os.chmod(staging, 0o700)
try:
    with tarfile.open(archive_path, mode='r:gz') as archive:
        total = 0
        seen = set()
        members = []
        for member in archive:
            path = pathlib.PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name:
                raise ValueError('Unsafe archive path')
            if not path.parts or path.parts[0] not in ('data', '.env', 'BACKUP_METADATA.json'):
                raise ValueError('Unexpected archive content')
            if path.parts[0] != 'data' and (len(path.parts) != 1 or not member.isfile()):
                raise ValueError('Invalid configuration or metadata entry')
            if path.parts[0] != 'data' and member.size > 1024 * 1024:
                raise ValueError('Configuration or metadata exceeds 1 MiB')
            if not (member.isdir() or member.isfile()):
                raise ValueError('Links, devices, and special files are not accepted')
            canonical = str(path)
            if canonical in seen:
                raise ValueError('Duplicate archive path')
            seen.add(canonical)
            total += member.size
            if len(seen) > 100000 or total > 10 * 1024**3 or member.size < 0:
                raise ValueError('Archive exceeds restore limits (100,000 entries / 10 GiB)')
            members.append((member, path))
        if '.env' not in seen or 'BACKUP_METADATA.json' not in seen or 'data' not in seen:
            raise ValueError('Incomplete backup: expected data, .env, and metadata')
        for member, relative in members:
            destination = staging.joinpath(*relative.parts)
            if member.isdir():
                destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError('Unreadable archive member')
                with source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                os.chmod(destination, 0o600)
        metadata = json.loads((staging / 'BACKUP_METADATA.json').read_text())
        if metadata.get('format_version') != 1:
            raise ValueError('Unsupported backup version')
    if target.exists() or target.is_symlink():
        raise ValueError('Restore target appeared during extraction; refusing overwrite')
    staging.rename(target)
except BaseException:
    shutil.rmtree(staging, ignore_errors=True)
    raise
print(f'Restored into: {target}')
print('The running application was not changed. Follow docs/INSTALL.md to activate this copy.')
PY
