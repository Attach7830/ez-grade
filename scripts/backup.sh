#!/usr/bin/env bash
# Cold application backup. Does not stop or modify the host Ollama service.
set -euo pipefail
umask 077
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"
backup_file="${1:-$project_dir/backups/spanish-grader-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}"
if [[ $# -gt 1 ]]; then
  echo 'Usage: bash scripts/backup.sh [NEW_BACKUP.tar.gz]' >&2
  exit 2
fi
if [[ ! -f .env || ! -d data || -L .env || -L data ]]; then
  echo 'Expected a regular .env file and data directory. Nothing was changed.' >&2
  exit 1
fi
if [[ -e "$backup_file" || -L "$backup_file" ]]; then
  echo 'Backup destination already exists. Choose a new filename.' >&2
  exit 1
fi
was_running=false
running_services="$(docker compose ps --status running --services)"
while IFS= read -r service; do
  if [[ "$service" == grader ]]; then was_running=true; fi
done <<< "$running_services"
restart_if_needed() {
  if [[ "$was_running" == true ]]; then
    docker compose start grader >&2 || echo 'Backup finished or failed, but app restart failed. Run docker compose start grader.' >&2
  fi
}
trap restart_if_needed EXIT
if [[ "$was_running" == true ]]; then docker compose stop grader; fi
python3 - "$project_dir" "$backup_file" <<'PY'
import datetime, hashlib, io, json, os, pathlib, sys, tarfile
project = pathlib.Path(sys.argv[1]).resolve()
dest = pathlib.Path(sys.argv[2]).expanduser().resolve()
data = project / 'data'
if dest == data or data in dest.parents:
    raise SystemExit('A backup cannot be stored inside data/')
for root, dirs, files in os.walk(data, followlinks=False):
    for name in dirs + files:
        entry = pathlib.Path(root) / name
        if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
            raise SystemExit(f'Unsupported data entry: {entry}. Nothing was archived.')
source = hashlib.sha256()
tracked = list((project / 'app').rglob('*.py')) + list((project / 'app').rglob('*.js')) + list((project / 'app').rglob('*.css')) + list((project / 'app').rglob('*.html'))
tracked += [project / name for name in ('requirements.txt', 'Dockerfile', 'compose.yaml')]
for path in sorted(set(tracked)):
    if path.is_file():
        source.update(str(path.relative_to(project)).encode() + b'\0' + path.read_bytes() + b'\0')
metadata = {
    'format_version': 1,
    'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'software_tree_sha256': source.hexdigest(),
    'uid': os.getuid(),
    'gid': os.getgid(),
    'contents': ['data/', '.env'],
    'method': 'application stopped; complete data directory including SQLite sidecars',
}
dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
created = False
try:
    with dest.open('xb') as handle:
        created = True
        os.chmod(dest, 0o600)
        with tarfile.open(fileobj=handle, mode='w:gz') as archive:
            archive.add(data, arcname='data', recursive=True)
            archive.add(project / '.env', arcname='.env', recursive=False)
            raw = json.dumps(metadata, indent=2).encode()
            info = tarfile.TarInfo('BACKUP_METADATA.json')
            info.size = len(raw)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(raw))
except BaseException:
    if created:
        dest.unlink(missing_ok=True)
    raise
print(f'Backup saved: {dest}')
print('Contains student work, grades, and secrets. Keep it private and retain the matching source ZIP.')
PY
