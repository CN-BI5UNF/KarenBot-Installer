# KarenBot Installer

[简体中文](README.md) | [English](README_EN.md)

The standalone installer for [KarenBot](https://github.com/CN-BI5UNF/KarenBot). It provides a Linux CLI, a Tkinter installation wizard, GitHub mirror selection, online and local installation, GTK4 appearance options, fonts, launchers, shortcuts, repair, and removal.

The installer downloads the application separately. Linux primarily uses the CLI; Windows native GUI installations use a prebuilt GTK4 portable package from the installer Release.

Current installer: **[v2.0.0-alpha (Latest)](https://github.com/CN-BI5UNF/KarenBot-Installer/releases/latest)**.
KarenBot and its installer have separate versions. The current application release is
[v2.1.0-alpha](https://github.com/CN-BI5UNF/KarenBot/releases/tag/v2.1.0-alpha).

## Downloads

These links always resolve to the installer **Latest** assets:

| Platform / purpose | Download |
|---|---|
| Windows 10/11 x64 installation wizard | [KarenBot-Installer.exe](https://github.com/CN-BI5UNF/KarenBot-Installer/releases/latest/download/KarenBot-Installer.exe) |
| Linux x64 installation wizard | [KarenBot-Installer-linux-x64](https://github.com/CN-BI5UNF/KarenBot-Installer/releases/latest/download/KarenBot-Installer-linux-x64) |
| Linux arm64 installation wizard | [KarenBot-Installer-linux-arm64](https://github.com/CN-BI5UNF/KarenBot-Installer/releases/latest/download/KarenBot-Installer-linux-arm64) |
| Linux CLI bootstrap | [install.sh](https://github.com/CN-BI5UNF/KarenBot-Installer/releases/latest/download/install.sh) |
| Windows GUI portable application | [KarenBot-Windows-x64.zip](https://github.com/CN-BI5UNF/KarenBot-Installer/releases/latest/download/KarenBot-Windows-x64.zip) |
| SHA256 checksums | [SHA256SUMS.txt](https://github.com/CN-BI5UNF/KarenBot-Installer/releases/latest/download/SHA256SUMS.txt) |

## Quick start

### Windows 10/11 (x64)

1. Download and run `KarenBot-Installer.exe` from the table above. A system Python installation is not required.
2. If SmartScreen reports an unknown publisher, check that the file came from the official Release, then choose **More info → Run anyway**.
3. Choose a short, writable ASCII path. The default is `C:\KarenBot`, or the equivalent path on your system drive.
4. Select **Online installation** and **GUI** or **WebUI**, then start installation.
5. Launch KarenBot from the desktop or Start menu. The installer can also launch, repair, or remove an existing installation.

Windows GUI downloads a portable package with GTK4 included. Its application version is the version embedded when that package was built. After installation, use **Settings → Updates** to select KarenBot `v2.1.0-alpha` if needed. Windows WebUI downloads portable Python when necessary.

### Linux CLI installation

Requires Bash, curl, Python 3.10+, and `sha256sum`. On Debian / Ubuntu:

```bash
sudo apt install curl python3 python3-venv
```

For a direct GitHub connection, run the following in the same terminal. Both the bootstrap and installer core are pinned to the current Latest, `v2.0.0-alpha`, with separate SHA256 checks:

```bash
export KAREN_INSTALLER_REF=v2.0.0-alpha
export KAREN_INSTALLER_SHA256=0c5fa39a5a4ac15726cd70708c2e34488703dfec067d07efdb8594b197821309
curl -fL https://github.com/CN-BI5UNF/KarenBot-Installer/releases/download/v2.0.0-alpha/install.sh -o install.sh &&
printf '%s  %s\n' '624538f2c167b091ee32e17cdc2c9322e3c857f94619723b1e1fd7375d759d01' install.sh | sha256sum -c - &&
bash install.sh
```

With no arguments, the CLI opens an interactive menu. Choose online installation, `GUI` or `WebUI`, and a destination, which defaults to `~/.local/opt/karenbot`. You can also pass arguments using the environment variables set above:

```bash
# Install WebUI and explicitly select the KarenBot application release
bash install.sh --online --frontend webui --tag v2.1.0-alpha \
  --dir ~/.local/opt/karenbot

# Install native GUI using the system GTK4 runtime
bash install.sh --online --frontend gui --tag v2.1.0-alpha \
  --dir ~/.local/opt/karenbot
```

Native GUI also requires GTK4, libadwaita, and PyGObject from the system:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

WebUI does not require GTK. The `KarenBot-Installer-linux-*` downloads are graphical installers: make the file executable with `chmod +x` and run it in a desktop session. Use the CLI on servers or over SSH.

### When GitHub access is unreliable

This Bash command tries the official Release and several mirrors. It downloads the bootstrap to a file and checks its complete bytes, including the final newline, before execution. Both the script and core are pinned to `v2.0.0-alpha`:

```bash
(
  set -e
  installer_ref=v2.0.0-alpha
  installer_script_sha=624538f2c167b091ee32e17cdc2c9322e3c857f94619723b1e1fd7375d759d01
  installer_core_sha=0c5fa39a5a4ac15726cd70708c2e34488703dfec067d07efdb8594b197821309
  installer_tmp="$(mktemp -d)"
  trap 'rm -rf -- "$installer_tmp"' EXIT
  installer_script="$installer_tmp/install.sh"
  for u in \
    "https://github.com/CN-BI5UNF/KarenBot-Installer/releases/download/$installer_ref/install.sh" \
    "https://gh-proxy.com/https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/$installer_ref/install.sh" \
    "https://ghproxy.net/https://raw.githubusercontent.com/CN-BI5UNF/KarenBot-Installer/$installer_ref/install.sh" \
    "https://fastly.jsdelivr.net/gh/CN-BI5UNF/KarenBot-Installer@$installer_ref/install.sh"; do
      if curl -fL --connect-timeout 8 --max-time 30 "$u" -o "$installer_script" &&
        printf '%s  %s\n' "$installer_script_sha" "$installer_script" | sha256sum -c -; then
        KAREN_INSTALLER_REF="$installer_ref" KAREN_INSTALLER_SHA256="$installer_core_sha" \
          bash "$installer_script"
        exit 0
      fi
  done
  echo "Unable to download or verify install.sh; check your network or proxy." >&2
  exit 1
)
```

The bootstrap also tries mirrors when downloading the installer core. Set `KAREN_INSTALLER_BASE` to override its source base URL. If every source fails, configure a proxy or use a local application package.

### Local installation and development

These CLI examples use the script and version environment variables from the quick start:

```bash
# Install an already assembled source tree
bash install.sh --frontend gui \
  --source-dir /path/to/KarenBot \
  --dir ~/.local/opt/karenbot --no-shortcut

# Install a local tar/zip package
bash install.sh --frontend gui --archive /path/to/karen.tar.gz

# Measure mirror performance without installing
bash install.sh --probe-url \
  https://github.com/CN-BI5UNF/KarenBot/archive/refs/heads/main.tar.gz
```

To run the graphical installer from an installer source checkout:

```bash
sudo apt install python3-tk
python3 gui_install.py
```

With CLI installation arguments, `install.sh` runs the Python installer core. GitHub's automatic archive of the KarenBot main repository does not contain its submodules. For local source installation, first follow the [assembly instructions](https://github.com/CN-BI5UNF/KarenBot#开发与构建).

### GUI or WebUI

- **Native GUI:** desktop windows, image previews, opening files with their default application, and revealing files in folders. Linux needs system GTK4 / libadwaita / PyGObject; Windows uses the portable package.
- **WebUI:** browser access without GTK. Files are downloaded or opened in the browser. Keep the service running and use its access token to sign in.

### WebUI on your LAN

Generated WebUI launchers bind to **127.0.0.1** by default. To allow devices on the same LAN to connect, use one of these methods:

1. Open **Settings → Network**, enable LAN access, save, and restart WebUI.
2. Set `webui.allow_lan: true` in `<install>/data/instances/default/config.yaml`, then restart the service.
3. Pass `--host 0.0.0.0` when starting the application, for example `python main.py --host 0.0.0.0 --port 8000`. Command-line arguments override configuration.

The access token is still required. Keep the port on a trusted network and use `--no-auth` only when you understand its consequences.

## What the installer does

1. **Mirror selection:** benchmarks the first 256 KB of assets and scores throughput and time to first byte. Downloads fall back to another mirror on failure. Results are reused within a five-minute window. Release metadata requests start directly; mirrors are raced after 1.5 seconds if needed, with the first valid response winning.
2. **Online releases:** reads `modules.json` from `CN-BI5UNF/KarenBot`, downloads the backend and selected frontend, and verifies SHA256. Windows GUI uses `KarenBot-Windows-x64.zip` from the installer Release.
3. **Default personas:** the Windows portable package includes personas from the main repository. Linux and WebUI installations fetch `KarenBot-Personas.zip`, falling back to the main repository archive if needed.
4. **Local sources:** accepts assembled source directories, tar/zip archives, and prebuilt packages.
5. **GUI appearance:** offers Karen, system Adwaita, or no appearance package. The first two use built-in GTK4 styles without a separate theme download.
6. **Fonts:** downloads Noto Sans SC / Noto Serif SC variable fonts from benchmarked sources and verifies pinned checksums. Font download failure does not block installation.
7. **Rendering fallback:** sets renderer options before GTK initialization and provides a Cairo compatibility launcher.
8. **Shortcuts:** creates Linux `.desktop` entries or Windows desktop / Start menu shortcuts. The installer remains available after installation for repairs and removal.
9. **Cancellation and live logs:** downloads, extraction, venv, and pip processes respond to cancellation; dependency installation output appears in the log view.
10. **Preflight checks:** checks write access, disk space (about 1.2 GB for GUI or 400 MB for WebUI), and running KarenBot processes before replacement.
11. **Removal with optional data retention:** `--keep-data` preserves `data/` and backs up `app/personas` to `data/personas`; a later installation restores custom personas.
12. **Environment recovery:** detects and rebuilds incomplete virtual environments left by cancellation or antivirus interruption.
13. **Download resumption:** assets with SHA256 can resume using `.part` files and a source marker. A different mirror URL or missing hash starts a fresh download; final checksum verification still applies.
14. **Diagnostics:** writes installation logs to the system temporary directory and provides copy / save / view log actions. The progress dialog locks installation settings while an operation runs.
15. **Existing installations:** enables open directory, launch, and compatibility launch actions without requiring reinstallation.
16. **Separate data and application directories:** launchers set `KAREN_DATA_ROOT` to `<install>/data/instances/default`; upgrades replace `app/` and migrate older `app/data` layouts when necessary.
17. **Windows removal entry:** registers a per-user uninstaller under HKCU and keeps its executable outside the application directory. Windows Settings can remove KarenBot even if the originally downloaded installer has been deleted. Removal offers keep data, delete data, or cancel; unattended removal supports `--non-interactive`.
18. **Small-screen layout:** advanced settings are collapsed by default so the installation action remains visible on smaller displays.

## Troubleshooting

See [known issues](docs/known-issues.md) for additional symptoms and solutions.

- **Windows paths:** choose a short ASCII installation, source, or archive path to avoid non-ASCII and MAX_PATH problems. The default is `C:\KarenBot` on the system drive.
- **Download errors:** mirror selection runs automatically. Check the progress stage and logs; if all mirrors fail, check your network, proxy, DNS, and firewall.
- **Permissions:** use a writable directory. A protected location such as `C:\Program Files` may require elevated permissions; the installer checks access before creating the environment.
- **Python:** checks Python 3.10+ for `venv`, `ensurepip`, `ssl`, and `sqlite3`. If needed, downloads portable CPython 3.12.16 into `<install>/python-runtime` without changing global Python. Linux native GUI still needs system PyGObject / GTK4.

## Common options

| Option | Description |
|---|---|
| `--frontend gui\|webui` | Select native GUI or WebUI |
| `--online` | Install from GitHub Release; default when no local source or archive is supplied |
| `--tag` | KarenBot application tag, for example `v2.1.0-alpha`; `auto` selects a recent Release with valid `modules.json`, including prereleases; Windows GUI uses its portable package's application version |
| `--dir` | Destination; defaults to `~/.local/opt/karenbot` on Linux or `C:\KarenBot` on the Windows system drive |
| `--source-dir` | Install a local assembled source tree; not supported for Windows native GUI |
| `--archive` | Install a local tar/zip package, including the Windows portable GUI zip |
| `--venv` | Reuse a virtual environment for testing |
| `--skip-deps` | Skip environment and dependency installation for testing |
| `--no-fonts` | Skip Noto font downloads |
| `--no-shortcut` | Skip shortcut creation |
| `--gui-skin karen\|system\|none` | GUI appearance; defaults to `karen` |
| `--renderer auto\|cairo\|gl` | Renderer; use `cairo` for software rendering when the GUI is blank or crashes |
| `--mirror <name>` | Select a GitHub mirror; see `--list-mirrors` |
| `--allow-unverified` | Explicitly permit unverified source downloads through mirrors |
| `--require-verified` | Accept direct checksum manifests or explicit `*_SHA256` values; reject mirror-only manifests |
| `--windows-gui-asset URL` | Override the Windows GUI portable package source |
| `--bundle-tag <tag>` | Installer Release containing the Windows GUI portable package; defaults to `auto` |
| `--uninstall` | Remove the installation at `--dir`, including its data unless `--keep-data` is set |
| `--keep-data` | Preserve runtime data and back up custom personas during removal |
| `--probe-url` | Benchmark mirrors and exit |
| `--list-mirrors` | List configured mirrors |
| `--dry-run` | Print the planned configuration without installing |

## Mirrors and verification

`mirrors.json` defines direct GitHub access and public mirrors. Third-party mirrors can see download requests and may modify content. Asset SHA256 checks detect a mismatch with the supplied manifest; a manifest obtained from the same mirror does not independently establish the publisher's identity.

`SHA256SUMS.txt` is requested directly first. If direct access fails, a mirror manifest may be used with an explicit warning. Strict verification accepts only direct manifests or explicit hashes. Windows GUI downloads stop when no checksum is available unless unverified downloads are explicitly allowed. Persona downloads may fall back to the main repository archive, which uses a direct connection when no hash is available. Unified component archives must match `modules.json`.

| Environment variable | Purpose |
|---|---|
| `KAREN_INSTALLER_REF` | Pin the bootstrap's installer source tag |
| `KAREN_INSTALLER_SHA256` | Verify the downloaded `karen_install.py` |
| `KAREN_PERSONAS_SHA256` | Verify the main repository archive used as the persona fallback |
| `KAREN_WINDOWS_GUI_SHA256` | Verify an overridden Windows GUI package |
| `KAREN_REQUIRE_VERIFIED=1` | Require direct manifests or explicit hashes |
| `KAREN_ALLOW_UNVERIFIED=1` | Explicitly allow unverified downloads for testing or trusted environments |

The quick-start commands pin the installer tag and both bootstrap/core hashes. Release builds publish `SHA256SUMS.txt` for installer binaries, the Windows portable package, personas, and installer sources; `install.sh` is also a Release asset.

## Windows native GUI package

Windows GUI installs use `KarenBot-Windows-x64.zip` rather than a source checkout. CI assembles it from KarenBot submodules using the application tag selected by `KARENBOT_TAG`. The installer and embedded application tags can differ. `--bundle-tag` selects the installer Release; `--tag` does not rebuild the portable package. Use the application's update page to update its code after installation.

The `windows-gui-package.json` contract checks:

- `runtime/python.exe`, with `pythonw.exe` preferred for normal GUI startup;
- `gtk-4-1.dll`, `adwaita-1-0.dll`, and `gobject-2.0-0.dll`;
- `Gtk-4.0.typelib` and `Adw-1.typelib`;
- the `backend/` and `gui/` packages.

See [Windows GUI package requirements](docs/windows-gui-package.md). If a Release lacks the package, the installer reports an error. For local or test packages, use `KAREN_GUI_WINDOWS_ASSET` or `--windows-gui-asset`.

## Fonts

Noto Sans SC / Noto Serif SC variable fonts are downloaded to:

- Linux: `$XDG_DATA_HOME/KarenBot/fonts`, normally `~/.local/share/KarenBot/fonts`;
- Windows: `%LOCALAPPDATA%\KarenBot\fonts`, or `<install>\fonts` when the normal path contains non-ASCII characters.

The installer generates `fonts.conf`, and launchers set `FONTCONFIG_FILE`. Font download failure does not prevent installation.

## Rendering fallback

Before importing `gi`, the GUI launcher can fall back through these stages:

- Stage 1: `GSK_RENDERER=cairo`, `GDK_DEBUG=gl-disable`, and `LIBGL_ALWAYS_SOFTWARE=1`.
- Stage 2: additionally selects `GDK_BACKEND=x11` on Linux with `DISPLAY`, or `win32` on Windows.

The standard launcher starts with automatic fallback. `launch-gui-safe` and the compatibility shortcut start with Cairo software rendering. Renderer options are handled by the launcher before GTK receives its arguments.

## Building with GitHub Actions

[The build workflow](.github/workflows/build-installer.yml) produces:

| Build environment | Output |
|---|---|
| `windows-2022` | `KarenBot-Installer.exe`, x64, windowed, at most 60 MiB |
| Windows with gvsbuild GTK4 and standalone CPython | `KarenBot-Windows-x64.zip`, published separately from the installer |
| `ubuntu-24.04` with an `ubuntu:22.04` container | `KarenBot-Installer-linux-x64` |
| `ubuntu-24.04-arm` with an `ubuntu:22.04` container | `KarenBot-Installer-linux-arm64` |

Pull requests matching the workflow paths run verification and online installation checks using the configured `release_tag` / `KARENBOT_TAG`. Tag pushes publish installer binaries, the portable GUI package, `KarenBot-Personas.zip`, `install.sh`, and `SHA256SUMS.txt`. Manual workflow runs are also supported.

`requirements-build.txt` pins PyInstaller. Windows checks cover PE x64, file version, SHA256, the 60 MiB installer limit, and installing / launching the portable bundle. The GTK4 bundle uses gvsbuild 2026.8.0 and CPython 3.14.10. Linux builds target glibc 2.35 or newer, such as Ubuntu 22.04 and Debian 12.

Use GitHub Actions for complete builds; local packaging is not needed to install KarenBot. Local checks:

```bash
python -m pytest tests/test_installer.py tests/test_windows_packaging.py -q
python -m ruff check karen_install.py gui_install.py tests/
bash -n install.sh
```

No deb/rpm or macOS packages are built. Windows binaries are unsigned and may trigger SmartScreen.

## Data and removal

- Archive extraction rejects traversal, absolute paths, symbolic links, and device files. Python 3.12+ uses the PEP 706 data filter; older versions use manual checks.
- Upgrades replace `<install>/app`, keeping runtime data under `<install>/data/instances/default`. Older `app/data` layouts are migrated when needed. Failed upgrades restore `app.old`.
- The installer remains available after installation for repair and removal.
- Removal requires an installation marker such as `.karen-installer.json` or installer metadata in `app/modules.json`.
- Removal deletes runtime data by default. Use `--keep-data` or the GUI's keep-data choice to retain `data/` and back up personas for a later installation.
- Windows registers the uninstaller under `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\KarenBot` and removes the entry on uninstall. Its running executable may remain outside the application directory until it can be deleted.

## Current limits

- Linux and Windows WebUI online installs select a recent usable unified KarenBot Release, which may be a prerelease. Use `--tag` to choose a specific application release.
- Windows GUI requires `KarenBot-Windows-x64.zip` in the selected installer Release and installs the version embedded in that package.
- Windows WebUI downloads portable Python as needed; system Python is optional.
- Linux CLI remains the primary Linux installation path. The graphical installer is a compact wizard rather than the KarenBot application UI.
