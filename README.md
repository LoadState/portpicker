# PortPicker

Pick your handheld → pick its custom firmware (CFW) → see which PortMaster ports run well on it → install them
straight from PortMaster onto your SD card. Windows app.

> **Status:** v1.0 beta. Looking for testers, see below.

## 🧪 Super Early Beta testers wanted

PortPicker v1.0 is in beta and I need people to try it on real devices.

**Why test v1 if it's limited?** Honestly, v1 won't be that useful to most people yet. It only installs free,
ready-to-run ports, which you can already get from PortMaster itself. v1 is about making sure the basics are solid on
real hardware: right folders, runtimes, box art, and 1- and 2-card setups.

Once there are enough test results, the plan is **Steam support**: log in with the Steam app (QR code) and PortPicker
downloads the game files a port needs from your own Steam library and puts them in the right place. No more hunting
for the right files and versions by hand. That's the part that should really help, if it can be made to work.
Your test results now are what get it there.

**Wanted most muOS Andromeda 2606.0+, KNULLI Scarab:**

- Anbernic RG35XX SP: tested by the developer (1 and 2 cards). More units welcome.
- TrimUI Brick Pro on muOS: tested by me.
- 1-card and 2-card setups. With 2 cards on muOS, ports go to SD2 and the app asks you to swap in SD1 for runtimes
  and box art.

**How to test:**

1. Back up your SD card first (this is a beta).
2. Run PortPicker and install 3–5 free ports.
3. On the device, open PortMaster once, then launch each port.
4. Report whether each port starts and whether the box art looks right. The **📝 Report test result** button in the
   app opens the results sheet. Results show up in the app for other users of the same device + CFW.

Bugs: open an issue here.

## Roadmap

| Version | What it does |
|---|---|
| **v1.0** | **Freeware only.** Installs free, ready-to-run ports, downloaded directly from PortMaster. No game files needed. |
| **v1.1** | **Wireless + updates.** Install over your home WiFi (SSH/SFTP) without taking the SD card out. PortPicker reads the device model, CFW version and both cards straight from the handheld. Also: update PortMaster and update installed games. |
| **v1.2** | **Steam.** Log in with the Steam app (QR code). PortPicker downloads the game files a port needs from your own Steam library. |
| later | Maybe other game stores. Not decided yet. |

PortPicker does **not** use game files that are already on your PC. Game files will only come from a store account
(v1.2+), so the files always match the game version the port expects. This avoids version-mismatch problems.

## Supported devices (v1.0)

| Device | Chip / RAM | CFW |
|---|---|---|
| TrimUI Brick Pro | Allwinner A133P / 1 GB | NextUI, muOS, KNULLI, spruceOS, CrossMix |
| Anbernic RG35XX SP | Allwinner H700 / 1 GB | muOS ✓ tested (1 and 2 cards), KNULLI, ROCKNIX, spruceOS |

Only **RG35XX SP + muOS** has been tested on real hardware so far. Every other device + CFW pair shows a
**"Beta — not tested on real hardware"** label. More devices are already defined in `profiles.py` but switched off
until they are tested.

## Where ports go on the card

The folder layout for each CFW follows PortMaster's own path table (`harbourmaster/config.py`). The card must be
readable by Windows (exFAT/FAT32).

| CFW | Scripts | Ports | Runtimes |
|---|---|---|---|
| NextUI | `Roms\Ports (PORTS)` | `Roms\Ports (PORTS)\.ports` | `Emus\<platform>\PORTS.pak\PortMaster\libs` (installs the [minui-portmaster](https://github.com/ben16w/minui-portmaster) pak if missing) |
| muOS (2606.0+) | `ROMS\Ports` | `ports` | `MUOS\PortMaster\libs` (card 1) |
| KNULLI | `roms\ports` | `roms\ports` | `system\.local\share\PortMaster\libs` |
| ROCKNIX | `roms\ports` | `roms\ports` | `roms\ports\PortMaster\libs` |
| spruceOS (Beta, unofficial) | `Roms\ports` | `Roms\ports` | `Persistent\portmaster\PortMaster\libs` |
| CrossMix (Beta, not official on Brick Pro) | `Roms\PORTS` | `Data\ports` | `Apps\PortMaster\PortMaster\libs` |

**Devices with two SD cards:** pick the card that holds your games (card 2). PortPicker follows the CFW:

- **KNULLI, ROCKNIX:** ports and runtimes all live on the games card. Nothing goes to card 1.
- **muOS:** ports go to card 2. Runtimes and box art go to card 1 (the card with `MUOS`). If only card 2 is in the PC,
  PortPicker installs the ports, then asks you to swap in card 1 to copy the runtimes and box art.

Free space is checked per card.

## How it picks ports

PortPicker downloads the latest `ports.json` from PortMaster and sorts the ports into groups:

- ⭐ **Must have:** a hand-picked list for the device.
- ✅ **Playable**
- ⚠️ **Heavy**
- ⛔ **Known not working:** reported or tested as not working on the device + CFW you selected.
- ✖ **Not supported:** too little RAM, OpenGL, too new glibc, not enough analog sticks, and so on.

## Beta test results

While in beta, the **📝 Report test result** button opens the shared test sheet. The Select Ports page shows the
results testers entered for the selected game on your device + CFW (runs or not, artwork OK, who tested, date).

## Install

1. Download `PortPicker.exe` from [Releases](../../releases).
2. Put the SD card in your PC and run the app.
3. After installing, put the card back in the device. Open **Ports → PortMaster** once so it can finish setting up
   the runtimes. The first launch of some games takes a few minutes.

## Run from source

- **Run:** double-click `run.bat`. It needs Python 3.10+ on Windows and installs PySide6 the first time.
- **Build an .exe:** double-click `build_exe.bat`. The result is `dist\PortPicker.exe`.
- **Tests:** `python -m pytest tests`
- **Cache:** `%LOCALAPPDATA%\PortPicker\cache` holds ports.json, images and downloaded zips. It's safe to delete.

## Files

| File | What it is |
|---|---|
| `app.py` | UI (PySide6) |
| `core.py` | Catalog, port picking, install (no Qt) |
| `profiles.py` | Devices, CFWs, recommended lists |
| `steam.py` | Steam support (hidden in v1.0, used from v1.2) |
| `tests/` | pytest |

**Add a device or CFW:** in `profiles.py`, add a `Device(...)` with its arch, sticks and must-have/heavy lists, and a
`CFW(...)` with its scripts/ports/libs folders and the marker files that identify the card. Then set `enabled=True`.

## Builds

Every push to `main` builds `PortPicker.exe` with GitHub Actions (Actions → latest run → Artifacts).
Pushing a tag like `v1.0.0` creates a Release with the .exe.

## Support

PortPicker only installs what [PortMaster](https://portmaster.games) publishes. Please support PortMaster:
[opencollective.com/portmaster](https://opencollective.com/portmaster).

## License

MIT
