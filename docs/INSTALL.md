# Install on ianchat

These commands are for you to run. Nothing in this delivery has been deployed or tested on your server. Your previously supplied setup is Linux, an RTX 2080 Ti with 11 GB VRAM, and an existing host Ollama system service. The app reuses that service.

## 1. Get the source onto the server

### Preferred: clone the GitHub repository

Source repository: [Attach7830/ez-grade](https://github.com/Attach7830/ez-grade). It is public, so cloning over HTTPS requires no GitHub account or access token.

In **Windows PowerShell**, connect to your AI server:

```powershell
$LinuxUser = Read-Host 'Your Linux username on ianchat'
ssh "${LinuxUser}@ianchat"
```

In the resulting **Linux shell**, with Git installed:

```bash
mkdir -p ~/apps
cd ~/apps
git clone https://github.com/Attach7830/ez-grade.git
cd ez-grade
```

Do not clone into an existing application directory. If `~/apps/ez-grade` already contains an installation, use the update section instead.

If the server already has a GitHub SSH key configured, you can use this clone command **instead** of HTTPS:

```bash
git clone git@github.com:Attach7830/ez-grade.git
```

HTTPS is sufficient for this public repository; SSH is optional. See GitHub's [cloning instructions](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository). The ZIP route below remains available if Git is unavailable on the server.

GitHub hosts the source code. The grading application uses a local teacher password and does not contact GitHub while grading. `.env`, `data/`, backups, and private pilot files are ignored by Git; never add them with `git add -f`. Only application code and labeled synthetic examples belong in the repository.

Continue at **step 2** after cloning.

### Alternative: download a source ZIP from GitHub

In your Windows browser, open [the repository](https://github.com/Attach7830/ez-grade), choose **Code → Download ZIP**, and save `ez-grade-main.zip` in Downloads. This downloads the current `main` branch; retain the ZIP you used for installation. In **Windows PowerShell**:

```powershell
$LinuxUser = Read-Host 'Your Linux username on ianchat'
$GraderZip = Join-Path $env:USERPROFILE 'Downloads\ez-grade-main.zip'
scp $GraderZip "${LinuxUser}@ianchat:~/ez-grade-main.zip"
ssh "${LinuxUser}@ianchat"
```

The following commands run in the resulting **Linux shell**. The GitHub ZIP for `main` contains a top-level `ez-grade-main/` folder. Use a new release directory so an update cannot overwrite existing data:

```bash
mkdir -p ~/apps/ez-grade-releases
cd ~/apps/ez-grade-releases
grader_release="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$grader_release"
python3 -m zipfile -e ~/ez-grade-main.zip "$grader_release"
cd "$grader_release/ez-grade-main"
```

Keep this directory path. Run future app commands from this directory unless the update section says otherwise.

## 2. Check existing services

```bash
hostname
systemctl is-active ollama
ollama --version
curl --fail --silent --show-error http://127.0.0.1:11434/api/version
nvidia-smi
docker --version
docker compose version
```

The commands inspect local state. Do not start a second `ollama serve`; the system service already owns its port. If the Ollama endpoint differs, set the app's `OLLAMA_URL` to that local/private-network address in step 4.

If Docker/Compose is absent, use the [official Docker Engine installation instructions](https://docs.docker.com/engine/install/) for the distribution reported by `cat /etc/os-release`, or use the Python alternative below. Docker access should already work for your normal Linux user. This project does not change Docker permissions or install GPU drivers.

## 3. Configure local-only Ollama and download the initial model

The current official model listing identifies `qwen3-vl:4b-instruct-q4_K_M` as a **3.3 GB Q4_K_M** vision-capable model and requires **Ollama 0.12.7 or later**. Use a current stable Ollama runtime that also supports the cloud-disable setting. The current GPU documentation lists the RTX 2080 Ti under compute capability 7.5 and requires NVIDIA driver 550 or later for that GPU class. Sources: [model](https://ollama.com/library/qwen3-vl:4b-instruct-q4_K_M), [GPU support](https://docs.ollama.com/gpu), [local-only configuration](https://docs.ollama.com/faq).

The model's download size is not its total runtime VRAM use. The 4B quantization and short context are a conservative starting candidate for 11 GB VRAM, not a guarantee of speed, fit, or handwriting accuracy. Pause GPU-heavy ComfyUI work and avoid simultaneous Open WebUI inference while checking it. Do not unload another user's active model without coordinating.

If Ollama needs updating, follow its [official Linux update instructions](https://docs.ollama.com/linux). If your driver is older than the supported version, use your Linux distribution's NVIDIA driver procedure; this source does not modify it.

Add a separate systemd drop-in, preserving existing `OLLAMA_HOST` and other settings. **Restarting Ollama interrupts requests from Open WebUI or other clients.** Run this when those requests are idle:

```bash
sudo install -d -m 755 /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/90-grader-local-only.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_NO_CLOUD=1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
sudo journalctl -u ollama -n 80 --no-pager
```

The log should include `Ollama cloud disabled: true`. If it does not, verify your installed Ollama version supports that setting before importing real submissions. This setting applies to the shared Ollama service, so cloud models and web search through it are disabled for its other clients too. It does not change the listener address or replace existing model storage.

Download the local model (internet access is needed for this installation step):

```bash
ollama pull qwen3-vl:4b-instruct-q4_K_M
ollama show qwen3-vl:4b-instruct-q4_K_M
curl --fail --silent --show-error http://127.0.0.1:11434/api/tags
```

Do not choose a `:cloud`/`-cloud` tag. The app has no cloud fallback and does not automatically download models. Both the reading and grading stages initially use the same model in separate requests, avoiding a second concurrently loaded model.

## 4. Create local configuration and start the app

From the cloned `ez-grade` directory (or extracted `ez-grade-main` ZIP directory), as your normal Linux user:

```bash
python3 scripts/init_config.py
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 grader
curl --fail --silent --show-error http://127.0.0.1:8766/api/health
```

`init_config.py` creates `.env` with mode 600 and `data/` with mode 700. It prints your randomly generated teacher password locally once. Save it in your password manager. It refuses to overwrite existing `.env`, to run as root, or to take over an existing data directory owned by someone else.

Open **http://ianchat:8766** from your Windows laptop. If that hostname does not resolve there, use the server's private LAN IP. `hostname -I` lists addresses on Linux. The application uses a local password; it does not require Google or another identity provider.

The default generated listener `0.0.0.0` makes the app reachable on the server's network interfaces. Keep access on your private LAN or existing VPN and do not add a public tunnel or router port forward. To bind only one private address instead, create the configuration with `python3 scripts/init_config.py --bind 192.168.1.50`, replacing that example with the server's actual private IP. For an existing configuration, edit only `GRADER_HOST` in `.env` and run `docker compose up -d`.

HTTP does not encrypt the password or student content between laptop and server. Use your existing private VPN, or the SSH tunnel below if the network is shared/untrusted. An existing local HTTPS reverse proxy also works; set `GRADER_COOKIE_SECURE=true` only when browser access is through HTTPS. Do not enable that flag for plain HTTP, because the browser will not send the login cookie.

### Optional: SSH-encrypted browser access

Set `GRADER_HOST=127.0.0.1` in `.env`, then apply it with `docker compose up -d`. In a separate Windows PowerShell window:

```powershell
$LinuxUser = Read-Host 'Your Linux username on ianchat'
ssh -N -L 8766:127.0.0.1:8766 "${LinuxUser}@ianchat"
```

Leave that window open and browse to **http://localhost:8766**. The laptop-to-server traffic travels through SSH. Ollama stays on its existing listener.

## 5. Run the first local checks

Sign in and use the app's Ollama connection/model check. It validates the configured local model and makes a small synthetic structured-output request. This checks protocol compatibility, not grading quality. Load the labeled synthetic example, inspect and approve its key, import/map sample answers, draft scores, review them, and approve them. Test CSV export only after all answers are approved.

For the first automatic key import, use a single-page PDF/image or key text up to 6,000 characters. The app also checks the combined prompt and content against a conservative context budget; a request below the text limit can still be rejected with a 4096-token context. The estimate is a protective guard, not a guarantee of exact model tokenization. Enter a longer key manually or import its structured JSON, then inspect and approve it. Student submissions are mapped separately by page and answer region.

During a local model request, inspect actual resource use:

```bash
ollama ps
nvidia-smi
```

The `PROCESSOR` column in `ollama ps` shows whether the model is fully on GPU, CPU, or split; see the [official FAQ](https://docs.ollama.com/faq). A successful model pull does not establish GPU offload or speed. If memory is tight, stop competing GPU work, keep one job at a time, and retain the 4096 context. Changing the model or context requires a new assessment after the key has been approved.

## Configuration reference

| Variable | Generated/default value | Purpose |
| --- | --- | --- |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Existing local Ollama endpoint |
| `OLLAMA_MODEL` | `qwen3-vl:4b-instruct-q4_K_M` | Initial local model tag |
| `OLLAMA_NUM_CTX` | `4096` | Per-request context limit; copied into each assessment |
| `GRADER_HOST` | `0.0.0.0` from setup; app default is loopback | Browser listener; use a private IP or loopback if desired |
| `GRADER_PORT` | `8766` | Browser port |
| `GRADER_PASSWORD` | Generated random value | Local teacher login, minimum 12 characters |
| `GRADER_SESSION_SECRET` | Generated random value | Cookie signing, minimum 32 characters |
| `GRADER_COOKIE_SECURE` | `false` | Set true only for HTTPS browser access |
| `GRADER_DATA_DIR` | `./data` native, `/data` container | SQLite, uploads, pages, crops |
| `GRADER_MAX_UPLOAD_MB` | `20` | Per-upload limit |
| `GRADER_UID`, `GRADER_GID` | Your Linux UID/GID | Nonroot container ownership matches the bind-mounted directory |

Existing `.env` files keep their configured port after a source update. To use the new default on an existing installation, set `GRADER_PORT=8766` in `.env` and run `docker compose up -d` to recreate the container with that setting.

Stop the app before manually moving its data directory. Never commit `.env`, `data/`, or backups. Do not paste secrets or student submissions into support chats. Source ZIPs contain no real student data.

## Backup

Finish any active processing job before backing up. From the active project directory:

```bash
bash scripts/backup.sh
```

The script stops **only the grader container**, archives the entire data directory (including any SQLite sidecars), `.env`, and source-version metadata, and restarts the app if it was running. It does not stop Ollama. The archive is written with mode 600 under `backups/` by default. Give a new destination filename to store it on your own backup disk:

```bash
bash scripts/backup.sh /path/on/your/backup-disk/spanish-grader-2026-11-16.tar.gz
```

The backup is not encrypted by this script. It contains student work and the teacher secret, so protect it as you protect the original server. Keep the matching source commit ID or the source ZIP used for installation with your backup. The audit JSON export is useful for review and pilot analysis; it is not a replacement for a complete backup of source images and the database.

## Restore

Restore into a **new directory** first:

```bash
bash scripts/restore.sh /path/to/backup.tar.gz "$HOME/spanish-grader-restore"
```

The script rejects traversal paths, symlinks, hard links, special files, duplicate entries, unexpected top-level files, and oversized archives. It extracts only `data/`, `.env`, and backup metadata. It does not run anything from the archive, start a service, or overwrite the active data.

To activate that restored copy on the same server and Linux account, from the active source directory:

```bash
(
set -eu
grader_restore="$HOME/spanish-grader-restore"
test -d "$grader_restore/data"
test ! -L "$grader_restore/data"
test -f "$grader_restore/.env"
test ! -L "$grader_restore/.env"
test -d data
test ! -L data
test -f .env
test ! -L .env
grader_before_restore="$(date -u +%Y%m%dT%H%M%SZ)"
grader_saved_data="data-before-restore-$grader_before_restore"
grader_saved_env=".env-before-restore-$grader_before_restore"
test ! -e "$grader_saved_data"
test ! -L "$grader_saved_data"
test ! -e "$grader_saved_env"
test ! -L "$grader_saved_env"
docker compose stop grader
mv -- data "$grader_saved_data"
mv -- .env "$grader_saved_env"
mv -- "$grader_restore/data" ./data
mv -- "$grader_restore/.env" ./.env
docker compose up -d
docker compose ps
)
```

This block validates the incoming data/configuration and checks that the saved-copy paths are unused before stopping the app. A failed stop or move halts the block; it does not continue into later steps. If a move fails after the app stops, leave it stopped while inspecting the retained original and restored paths. Keep the previous data/configuration until you have inspected the restored assessments and images. If restoring under a different Linux account, update `GRADER_UID`/`GRADER_GID` in the restored `.env` to `id -u`/`id -g`; the restore script creates files owned by its current user. Use the source version matching the backup when recovering an older database.

## Update without overwriting data

### A GitHub checkout

Finish and export any active assessment before updating application prompts or scoring code. Finish processing jobs, and keep a backup made before the update. An approved assessment freezes its prompt/rubric/model settings; an application update can deliberately refuse to continue a batch whose prompt version has changed.

From the existing Git checkout, the following guarded block checks for local source changes, creates a backup, records the current commit, and performs only a fast-forward source update:

```bash
(
set -eu
test -d .git
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  echo 'Local source changes found. Keep them and review or commit them before updating.' >&2
  exit 1
fi
bash scripts/backup.sh
grader_previous_revision="$(git rev-parse HEAD)"
echo "Previous source revision: $grader_previous_revision"
git pull --ff-only
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 grader
)
```

Run this from the checkout, for example after `cd ~/apps/ez-grade`. The block stops on a failed backup, failed pull, or failed build. `git pull --ff-only` will not manufacture a merge or discard local commits. If it reports divergence or the clean-tree check reports your own source edits, preserve that work and resolve it deliberately; do not use a force update or reset. The ignored `.env` and `data/` remain in place. The Compose rebuild uses the updated source and reuses that data mount.

Keep the printed previous commit and the data backup together. If an update fails, inspect the error before continuing; rebuilding older code may also require restoring the matching backup if a future release migrates the database. The ZIP release-directory route below remains available for retaining separate source copies and testing an update before switching.

### A ZIP installation

1. Run the backup command and keep that archive plus the source ZIP used for the existing installation.
2. Download a fresh `ez-grade-main.zip` through GitHub **Code → Download ZIP**, copy it to `ianchat`, and extract it into a **new** release directory using the ZIP instructions in step 1 above. Do not run `init_config.py` there.
3. Stop the old app. Copy its private configuration and complete data into the new release, keeping the old release available for rollback.

For example, substitute the two actual release directories when prompted:

```bash
(
set -eu
read -r -p 'Current application directory: ' grader_old
read -r -p 'New application directory: ' grader_new
test -f "$grader_old/.env"
test -f "$grader_new/compose.yaml"
test ! -e "$grader_new/.env"
test ! -e "$grader_new/data"
(cd "$grader_old" && docker compose stop grader)
cp -p "$grader_old/.env" "$grader_new/.env"
cp -a "$grader_old/data" "$grader_new/data"
cd "$grader_new"
docker compose up -d --build
docker compose ps
)
```

For a failed update, stop the new app, return to the old source directory, and run `docker compose up -d --build` to rebuild the previous code. Both releases use the same local image tag, so merely starting an old container is not a reliable code rollback. The old data copy represents the pre-update state; if you have entered new grades in the new release, back it up before rolling back and reconcile those changes. Never run two copies against the same data directory. Review release migration notes before future schema upgrades.

## Native Python alternative

If you prefer no container and already have Python 3.12 installed:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
python3 scripts/init_config.py
.venv/bin/python -m app
```

The app reads `.env` directly. Run from the project directory. Ctrl+C stops it. The provided backup script is specifically for Compose; for a native run, stop the process and archive `.env` plus the complete `data/` directory while it is stopped. You can run software tests without Ollama using `.venv/bin/python -m unittest discover -s tests -v`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Cannot reach Ollama | `systemctl is-active ollama`, `/api/version`, and the configured `OLLAMA_URL`; host networking is intended for Linux |
| Model missing | Run the explicit `ollama pull` command; the app will not download a replacement |
| Image call rejected | Verify the installed model has vision capability and Ollama meets its minimum version |
| Model changed during assessment | Restore the original pinned model or create a new assessment; do not mix model versions in one pilot |
| Blank extraction or model failure | Review the source, retry if appropriate, or enter a literal manual transcription; a failure remains ungraded |
| CSV export blocked | Inspect every pending/needs-review answer and approve explicitly |
| Data permission error | Verify the project and `data/` belong to the Linux user whose UID/GID is in `.env` |
| Login does not persist on HTTP | Leave `GRADER_COOKIE_SECURE=false`; use true only behind HTTPS |
| Changes from another tab rejected | Reload the answer; stale edits are rejected instead of overwriting newer changes |

No automatic cloud substitution occurs for any failure.
