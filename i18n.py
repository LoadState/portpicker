"""Internationalization / string table for PortPicker.

English is the default language (v1). Thai translations are preserved in the table.
"""

from __future__ import annotations

import os

# "en" by default; can be overridden via PORTPICKER_LANG environment variable
CURRENT_LANG = os.environ.get("PORTPICKER_LANG", "en").lower()


def set_language(lang: str) -> None:
    global CURRENT_LANG
    CURRENT_LANG = lang.lower()


def get_language() -> str:
    return CURRENT_LANG


STRINGS: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------------
    # Tiers & steps
    # ------------------------------------------------------------------------
    "tier.must": {
        "en": "⭐ Must-Have",
        "th": "⭐ ต้องมี",
    },
    "tier.local": {
        "en": "📁 From PC",
        "th": "📁 จากเครื่อง",
    },
    "tier.ok": {
        "en": "✅ Playable",
        "th": "✅ เล่นได้",
    },
    "tier.heavy": {
        "en": "⚠️ Heavy",
        "th": "⚠️ หนัก",
    },
    "tier.broken": {
        "en": "⛔ Broken",
        "th": "⛔ รันไม่ได้",
    },
    "tier.incompatible": {
        "en": "✖ Unsupported",
        "th": "✖ ไม่รองรับ",
    },
    "step.device": {
        "en": "Device",
        "th": "เครื่อง",
    },
    "step.cfw": {
        "en": "CFW",
        "th": "CFW",
    },
    "step.ports": {
        "en": "Select Ports",
        "th": "เลือกพอร์ต",
    },
    "step.install": {
        "en": "Install",
        "th": "ติดตั้ง",
    },
    "head.game": {
        "en": "Game",
        "th": "เกม",
    },
    "head.tier": {
        "en": "Tier",
        "th": "ระดับ",
    },
    "head.genre": {
        "en": "Genre",
        "th": "แนว",
    },
    "head.files": {
        "en": "Files",
        "th": "ไฟล์เกม",
    },
    "head.size": {
        "en": "Size",
        "th": "ขนาด",
    },

    # ------------------------------------------------------------------------
    # Profiles & CFW
    # ------------------------------------------------------------------------
    "cfw.nextui.blurb": {
        "en": "Lightweight & fast · Uses ben16w PortMaster pak",
        "th": "เบา เร็ว ใช้ PortMaster pak (ben16w)",
    },
    "cfw.nextui.after_1": {
        "en": "Safely eject card → Insert into handheld → Open Ports → Launch PortMaster once (it will patch runtimes and download box art) → Ready to play",
        "th": "ถอดการ์ดอย่างปลอดภัย → ใส่เครื่อง → เข้า Ports → Portmaster หนึ่งครั้ง (pak จะ patch runtime และดึงรูปเกมให้) แล้วออกมาเล่นได้เลย",
    },
    "cfw.muos.blurb": {
        "en": "Andromeda 2606+ · Built-in PortMaster",
        "th": "Andromeda 2606+ · PortMaster มีในตัว",
    },
    "cfw.muos.after_1": {
        "en": "Safely eject card → Insert into handheld → Explore Content → Ports → Select game to play",
        "th": "ถอดการ์ดอย่างปลอดภัย → ใส่เครื่อง → Explore Content → Ports แล้วเลือกเกมได้เลย",
    },
    "cfw.muos.after_2": {
        "en": "If games do not appear, open PortMaster on the handheld once or restart",
        "th": "ถ้าเกมไม่ขึ้น ให้เปิด PortMaster บนเครื่องหนึ่งครั้ง หรือรีสตาร์ท",
    },
    "cfw.knulli.blurb": {
        "en": "Batocera fork for handhelds · Built-in PortMaster",
        "th": "Batocera สำหรับมือถือเกม · PortMaster มีในตัว",
    },
    "cfw.knulli.after_exfat": {
        "en": "If using exFAT: games requiring symlinks may not launch (see KNULLI exFAT guide)",
        "th": "หากใช้การ์ดแบบ exFAT: บางเกมที่ใช้ symlink อาจรันไม่ติด (ตามคู่มือ KNULLI exFAT guide)",
    },
    "cfw.arkos.blurb": {
        "en": "Popular CFW for R36S · Built-in PortMaster",
        "th": "ตัวยอดนิยมของ R36S · PortMaster มีในตัว",
    },
    "cfw.darkos.blurb": {
        "en": "Newer ArkOS (Debian) · Built-in PortMaster",
        "th": "ArkOS รุ่นใหม่ (Debian) · PortMaster มีในตัว",
    },
    "cfw.rocknix.blurb": {
        "en": "Successor to JELOS · Built-in PortMaster",
        "th": "ต่อจาก JELOS · PortMaster มีในตัว",
    },
    "cfw.spruce.blurb": {
        "en": "For Miyoo / TrimUI / Anbernic · Built-in PortMaster",
        "th": "สำหรับ Miyoo / TrimUI / Anbernic · PortMaster มีในตัว",
    },
    "cfw.spruce.after_unofficial": {
        "en": "PortMaster on spruceOS is unofficial; compatibility varies by game (hit or miss)",
        "th": "PortMaster บน spruceOS ไม่ได้รับการสนับสนุนอย่างเป็นทางการ (unofficial) การเล่นได้ขึ้นอยู่กับแต่ละเกม (hit or miss)",
    },
    "cfw.crossmix.blurb": {
        "en": "For TrimUI (unofficial on Brick Pro) · Built-in PortMaster",
        "th": "สำหรับ TrimUI (ไม่รองรับ Brick Pro อย่างเป็นทางการ) · PortMaster มีในตัว",
    },
    "cfw.crossmix.after_refresh": {
        "en": "Safely eject card → Insert into handheld → At main menu press MENU → Refresh Roms to update game list",
        "th": "ถอดการ์ดอย่างปลอดภัย → ใส่เครื่อง → ที่เมนูหลัก กด MENU → Refresh Roms เพื่ออัปเดตรายการเกม",
    },
    "cfw.crossmix.after_unofficial": {
        "en": "CrossMix does not officially support TrimUI Brick Pro",
        "th": "CrossMix ไม่ได้รองรับ TrimUI Brick Pro อย่างเป็นทางการ",
    },
    "cfw.common.eject_open_ports": {
        "en": "Safely eject card → Insert into handheld → Open Ports section",
        "th": "ถอดการ์ดอย่างปลอดภัย → ใส่เครื่อง → เปิดหมวด Ports",
    },
    "cfw.common.first_launch_slow": {
        "en": "First launch of some games may take several minutes — this is normal",
        "th": "ครั้งแรกบางเกมโหลดนานหลายนาที เป็นปกติ",
    },
    "cfw.common.es_refresh": {
        "en": "If games do not appear: START → Game Settings → Update Gamelists",
        "th": "ถ้าเกมไม่ขึ้น: START → Game Settings → Update Gamelists",
    },
    "cfw.common.es_restart": {
        "en": "If games do not appear: START → Quit → Restart EmulationStation",
        "th": "ถ้าเกมไม่ขึ้น: START → Quit → Restart EmulationStation",
    },

    # ------------------------------------------------------------------------
    # Core classification & warnings
    # ------------------------------------------------------------------------
    "reason.local_missing_pm": {
        "en": "No longer in PortMaster — copying from local folder as is",
        "th": "ไม่มีใน PortMaster แล้ว — ก๊อปจากเครื่องคุณตามเดิม",
    },
    "reason.cfw_broken": {
        "en": "Reported broken on {cfw}",
        "th": "รายงานว่ารันไม่ได้บน {cfw}",
    },
    "reason.heavy": {
        "en": "Demanding on this device — may stutter or take long to load",
        "th": "หนักสำหรับเครื่องนี้ อาจกระตุก/โหลดนาน",
    },
    "reason.armhf": {
        "en": "32-bit (armhf) version",
        "th": "เวอร์ชัน 32-bit (armhf)",
    },
    "reason.no_arch": {
        "en": "No {arch} build available",
        "th": "ไม่มีเวอร์ชัน {arch}",
    },
    "reason.needs_reqs": {
        "en": "Requires: {reqs}",
        "th": "ต้องการ: {reqs}",
    },
    "reason.min_glibc": {
        "en": "Requires glibc {glibc}",
        "th": "ต้องใช้ glibc {glibc}",
    },
    "reason.minor_issues": {
        "en": "Minor reported issues",
        "th": "มีปัญหาเล็กน้อยที่รายงานไว้",
    },
    "reason.weston": {
        "en": "Uses Weston (high resource usage)",
        "th": "ใช้ Weston (ต้องการทรัพยากรสูง)",
    },
    "reason.also_steam": {
        "en": "🎮 Available on Steam (will use files from selected local folder)",
        "th": "🎮 มีใน Steam ด้วย (จะใช้ไฟล์จากโฟลเดอร์ในเครื่องที่เลือกไว้)",
    },
    "reason.stardew_16": {
        "en": "Local files are Stardew Valley 1.6 — use with 'Stardew Valley' port, not Compatibility",
        "th": "ไฟล์ในเครื่องเป็น Stardew Valley 1.6 (เวอร์ชันปกติ) — ใช้กับพอร์ต 'Stardew Valley' ไม่ใช่ Compatibility",
    },
    "port.local_desc": {
        "en": "Port from local folder (not currently in PortMaster)",
        "th": "พอร์ตจากโฟลเดอร์ในเครื่อง (ไม่มีใน PortMaster ปัจจุบัน)",
    },
    "warn.card2_space": {
        "en": "Card 2 ({root}) may not have enough space: {free} free, ~{need} needed",
        "th": "พื้นที่ Card 2 ({root}) อาจไม่พอสำหรับพอร์ต: ว่าง {free}, ต้องการ ~{need}",
    },
    "warn.card1_space": {
        "en": "Card 1 ({root}) may not have enough space for runtime: {free} free, ~{need} needed",
        "th": "พื้นที่ Card 1 ({root}) อาจไม่พอสำหรับ runtime: ว่าง {free}, ต้องการ ~{need}",
    },
    "warn.card_space": {
        "en": "Card ({root}) may not have enough space: {free} free, ~{need} needed",
        "th": "พื้นที่การ์ด ({root}) อาจไม่พอ: ว่าง {free}, ต้องการ ~{need}",
    },
    "warn.no_game_files": {
        "en": "Local folder has no game files",
        "th": "โฟลเดอร์ในเครื่องไม่มีไฟล์เกม",
    },
    "warn.patched_droid": {
        "en": "Game files are patched for an older port (game.droid) — new port may require original files",
        "th": "ไฟล์เกมเป็นแบบที่ patch แล้วของพอร์ตรุ่นเก่า (game.droid) — พอร์ตใหม่อาจต้องใช้ไฟล์ต้นฉบับ",
    },
    "warn.fat32_port_size": {
        "en": "Port '{title}' archive ({size}) exceeds FAT32 4 GB limit",
        "th": "ไฟล์บีบอัดพอร์ต '{title}' ({size}) เกินขีดจำกัด 4 GB ของ FAT32",
    },
    "warn.fat32_local_file": {
        "en": "Game file '{name}' ({size}) in {title} exceeds FAT32 4 GB limit",
        "th": "ไฟล์เกม '{name}' ({size}) ใน {title} เกินขีดจำกัด 4 GB ของ FAT32",
    },
    "warn.fat32_runtime_size": {
        "en": "Runtime '{name}' ({size}) exceeds FAT32 4 GB limit",
        "th": "Runtime '{name}' ({size}) เกินขีดจำกัด 4 GB ของ FAT32",
    },
    "err.cfw_version_too_low": {
        "en": "Update your CFW first. {cfw} {installed} is below the minimum required version ({min_ver}).",
        "th": "กรุณาอัปเดต CFW ก่อน: {cfw} {installed} เก่ากว่ารุ่นขั้นต่ำ ({min_ver})",
    },
    "err.pak_update_space": {
        "en": "Not enough free space on card for PortMaster pak update ({free} free, ~{need} needed)",
        "th": "พื้นที่บนการ์ดไม่พอสำหรับการอัปเดต PortMaster pak (ว่าง {free}, ต้องการ ~{need})",
    },

    # ------------------------------------------------------------------------
    # Core install logs
    # ------------------------------------------------------------------------
    "log.clean_old": {
        "en": "  🧹 Cleaned old files in {name} ({count} items, saves preserved)",
        "th": "  🧹 ล้างของเดิมใน {name} ({count} รายการ, เก็บเซฟไว้)",
    },
    "log.copy_steam": {
        "en": "  🎮 Copying game files from Steam: {src}",
        "th": "  🎮 ก๊อปไฟล์เกมจาก Steam: {src}",
    },
    "log.missing_steam": {
        "en": "  ⚠ Not found in Steam folder: {files}",
        "th": "  ⚠ ไม่พบในโฟลเดอร์ Steam: {files}",
    },
    "log.card2_no_libs": {
        "en": "⚠ Card has no {dir} folder (likely Card 2) — runtimes will not be copied; launch game via on-device PortMaster to download runtime",
        "th": "⚠ การ์ดนี้ไม่มีโฟลเดอร์ {dir} (น่าจะเป็นการ์ด 2) — runtime จะไม่ถูกก๊อป ให้เปิดเกมผ่าน PortMaster บนเครื่องเพื่อโหลด runtime",
    },
    "log.install_pak": {
        "en": "Installing PortMaster pak for {name} ({tag}) ...",
        "th": "ติดตั้ง PortMaster pak สำหรับ {name} ({tag}) ...",
    },
    "log.pak_installed": {
        "en": "✔ PortMaster pak installed",
        "th": "✔ ติดตั้ง PortMaster pak แล้ว",
    },
    "log.pak_not_used": {
        "en": "⚠ {name} does not use PortMaster pak",
        "th": "⚠ {name} ไม่ได้ใช้ PortMaster pak",
    },
    "log.download_pak": {
        "en": "Downloading PortMaster pak ({tag}) ...",
        "th": "ดาวน์โหลด PortMaster pak ({tag}) ...",
    },
    "log.updating_pak": {
        "en": "Updating PortMaster pak...",
        "th": "กำลังอัปเดต PortMaster pak...",
    },
    "log.pak_updated": {
        "en": "✔ PortMaster pak updated successfully",
        "th": "✔ อัปเดต PortMaster pak สำเร็จ",
    },
    "log.pak_update_failed_restoring": {
        "en": "✖ Pak update failed ({err}) — restoring original version...",
        "th": "✖ อัปเดต pak ล้มเหลว ({err}) — กำลังกู้คืนเวอร์ชันเดิม...",
    },
    "log.libs_unknown_skip": {
        "en": "  ⚠ Libs location unknown — skipping runtime {name} (PortMaster on handheld will download it when launched)",
        "th": "  ⚠ ไม่รู้ตำแหน่ง libs — ข้าม runtime {name} (PortMaster บนเครื่องจะโหลดให้เองตอนเปิดเกม)",
    },
    "log.runtime_not_found": {
        "en": "  ⚠ Runtime {name} not found in catalog",
        "th": "  ⚠ ไม่พบ runtime {name} ในแคตตาล็อก",
    },
    "log.port_from_pc": {
        "en": "▶ {title} (from PC)",
        "th": "▶ {title} (จากเครื่อง)",
    },
    "log.copied_files": {
        "en": "  ✔ Copied {count} files (skipped {skipped})",
        "th": "  ✔ ก๊อป {count} ไฟล์ (ข้าม {skipped})",
    },
    "log.installed_pm": {
        "en": "  ✔ Installed port from PortMaster",
        "th": "  ✔ ติดตั้งพอร์ตจาก PortMaster",
    },
    "log.game_files_from": {
        "en": "  ⇢ Game files from {src} → {dst}{how}",
        "th": "  ⇢ ไฟล์เกมจาก {src} → {dst}{how}",
    },
    "log.copied_existing": {
        "en": "  ✔ Copied {count} files (skipped existing {skipped})",
        "th": "  ✔ ก๊อป {count} ไฟล์ (ข้ามที่มีอยู่แล้ว {skipped})",
    },
    "log.copied_steam": {
        "en": "  ✔ Copied {count} files from Steam (skipped existing {skipped})",
        "th": "  ✔ ก๊อป {count} ไฟล์จาก Steam (ข้ามไฟล์ที่มีอยู่แล้ว {skipped})",
    },
    "log.needs_game_files": {
        "en": "  ⚠ This game requires additional game files: {inst}",
        "th": "  ⚠ เกมนี้ต้องใส่ไฟล์เกมเพิ่ม: {inst}",
    },
    "err.not_muos_card1": {
        "en": "{path} is not muOS Card 1 (MUOS folder not found)",
        "th": "{path} ไม่ใช่ muOS Card 1 (ไม่พบโฟลเดอร์ MUOS)",
    },

    # ------------------------------------------------------------------------
    # App UI & Pages
    # ------------------------------------------------------------------------
    "app.cancelled": {
        "en": "Cancelled",
        "th": "ยกเลิกแล้ว",
    },
    "device.title": {
        "en": "Select Your Handheld",
        "th": "เลือกเครื่องของคุณ",
    },
    "device.subtitle": {
        "en": "PortPicker filters ports based on device specs, analog sticks, and CFW",
        "th": "PortPicker จะคัดพอร์ตตามสเปกเครื่อง จำนวนอนาล็อก และ CFW ที่ใช้",
    },
    "device.sticks": {
        "en": "sticks",
        "th": "อนาล็อก",
    },
    "device.coming_soon": {
        "en": "Coming Soon",
        "th": "เร็วๆ นี้",
    },
    "cfw.title": {
        "en": "Select CFW",
        "th": "เลือก CFW",
    },
    "cfw.subtitle": {
        "en": "Select CFW so PortPicker knows the ports folder layout, or choose Custom to specify your own folder",
        "th": "เลือก CFW แล้ว PortPicker จะรู้ตำแหน่งโฟลเดอร์ ports เอง หรือเลือก Custom เพื่อชี้โฟลเดอร์เอง",
    },
    "cfw.rescan": {
        "en": "🔄 Rescan Cards",
        "th": "🔄 หาการ์ดอีกครั้ง",
    },
    "cfw.browse": {
        "en": "📂 Browse Folder…",
        "th": "📂 เลือกโฟลเดอร์…",
    },
    "cfw.card1_label": {
        "en": "Card 1 (System):",
        "th": "Card 1 (ระบบ):",
    },
    "cfw.card1_browse": {
        "en": "📂 Browse Card 1…",
        "th": "📂 เลือก Card 1…",
    },
    "cfw.custom_desc": {
        "en": "Specify ports folder manually\n(Other CFW / Testing)",
        "th": "ชี้โฟลเดอร์ ports เอง\n(CFW อื่น / ทดสอบ)",
    },
    "cfw.panel_ports": {
        "en": "Your ports folder",
        "th": "โฟลเดอร์ ports ของคุณ",
    },
    "cfw.panel_sd": {
        "en": "SD card with {cfw}",
        "th": "SD card ที่ลง {cfw}",
    },
    "cfw.select_folder": {
        "en": "Select Folder",
        "th": "เลือกโฟลเดอร์",
    },
    "cfw.select_card1": {
        "en": "Select muOS Card 1",
        "th": "เลือก Card 1 ของ muOS",
    },
    "cfw.no_card": {
        "en": "No card detected — insert SD card and click 'Rescan Cards' or browse folder",
        "th": "ยังไม่พบการ์ด — เสียบ SD card แล้วกด 'หาการ์ดอีกครั้ง' หรือเลือกโฟลเดอร์เอง",
    },
    "cfw.note_rocknix": {
        "en": "\n💡 Note: Single-card ROCKNIX STORAGE partition is ext4 (not visible on Windows). Use games card (SD2) formatted as exFAT.",
        "th": "\n💡 หมายเหตุ: พาร์ติชัน STORAGE ของ ROCKNIX บนการ์ดเดียวเป็น ext4 ซึ่ง Windows มองไม่เห็น (ต้องใช้การ์ดเกม SD2 เป็น exFAT)",
    },
    "cfw.note_knulli": {
        "en": "\n💡 Note: Older KNULLI releases used ext4 for SHARE (not visible on Windows). Gladiator and newer use exFAT.",
        "th": "\n💡 หมายเหตุ: หากใช้ KNULLI รุ่นเก่า พาร์ติชัน SHARE อาจเป็น ext4 ซึ่ง Windows มองไม่เห็น (Gladiator ขึ้นไปเป็น exFAT แล้ว)",
    },
    "cfw.folder_not_found": {
        "en": "⚠ Folder not found",
        "th": "⚠ ไม่พบโฟลเดอร์นี้",
    },
    "cfw.will_install_to": {
        "en": "✔ Ports will be installed to {root}\n",
        "th": "✔ จะติดตั้งพอร์ตลง {root}\n",
    },
    "cfw.runtime_to": {
        "en": "runtime → {libs}",
        "th": "runtime → {libs}",
    },
    "cfw.no_pm_libs": {
        "en": "PortMaster/libs not found — runtimes will be downloaded by PortMaster on device",
        "th": "ไม่พบ PortMaster/libs — runtime จะให้ PortMaster บนเครื่องโหลดเอง",
    },
    "cfw.pak_checking": {
        "en": "PortMaster pak:  <b>{installed}</b>  (checking latest version...)",
        "th": "PortMaster pak:  <b>{installed}</b>  (กำลังเช็คเวอร์ชันล่าสุด...)",
    },
    "cfw.pak_latest": {
        "en": "PortMaster pak:  <b>{installed}</b>  (latest ✔)",
        "th": "PortMaster pak:  <b>{installed}</b>  (ล่าสุด ✔)",
    },
    "cfw.pak_update_avail": {
        "en": "PortMaster pak:  <b>{installed}</b>  (update available: <b>{latest}</b>)",
        "th": "PortMaster pak:  <b>{installed}</b>  (มีเวอร์ชันใหม่: <b>{latest}</b>)",
    },
    "cfw.pak_update_btn": {
        "en": "Update PortMaster pak",
        "th": "อัปเดต PortMaster pak",
    },
    "cfw.dual_sd_detected": {
        "en": "✔ Found muOS Dual-SD setup — cards matched automatically:\n  • SD2 (Ports/Games): {root} · {free_root} free\n  • SD1 (System/Runtimes/Box art): {sys_root} · {free_sys} free\nPorts will go to {ports}\nRuntimes will go to {libs}\nBox art will go to {media}",
        "th": "✔ พบ muOS แบบ 2 การ์ด (Dual-SD) — จับคู่การ์ดอัตโนมัติ:\n  • SD2 (พอร์ต/เกม): {root} · ว่าง {free_root}\n  • SD1 (ระบบ/runtimes/box art): {sys_root} · ว่าง {free_sys}\nพอร์ตจะไปที่ {ports}\nruntimes จะไปที่ {libs}\nbox art จะไปที่ {media}",
    },
    "cfw.dual_sd_card2_only": {
        "en": "✔ Found muOS Card 2 (SD2) at {root} · {free_root} free\n⚠ Card 1 (SD1) not yet detected — ports and scripts will install to SD2\n💡 You can select Card 1 above or swap in Card 1 after install (Swap flow)",
        "th": "✔ พบ Card 2 ของ muOS (SD2) ที่ {root} · ว่าง {free_root}\n⚠ ยังไม่พบ Card 1 (SD1) ในเครื่อง — พอร์ตและสคริปต์จะลง SD2\n💡 คุณสามารถเลือก Card 1 ด้านบน หรือสลับใส่ Card 1 หลังติดตั้ง (Swap flow)",
    },
    "cfw.pak_builtin": {
        "en": "Built into {cfw} ✔",
        "th": "มีในตัว {cfw} ✔",
    },
    "cfw.pak_card2_notice": {
        "en": "Card 2 (no MUOS/) — runtimes downloaded via on-device PortMaster",
        "th": "การ์ด 2 (ไม่มี MUOS/) — runtime ให้ PortMaster บนเครื่องโหลดเอง",
    },
    "cfw.pak_installed_status": {
        "en": "✔ Installed",
        "th": "✔ ติดตั้งแล้ว",
    },
    "cfw.pak_not_installed_status": {
        "en": "Not installed — PortPicker will install it",
        "th": "ยังไม่ติดตั้ง — PortPicker จะติดตั้งให้",
    },
    "cfw.card_match": {
        "en": "✔ Found {cfw} card ({root}) · {free} free",
        "th": "✔ พบการ์ด {cfw} ({root}) · ว่าง {free}",
    },
    "cfw.card_mismatch": {
        "en": "⚠ Does not look like a {cfw} card ({root})",
        "th": "⚠ ไม่เหมือนการ์ด {cfw} ({root})",
    },
    "cfw.card_no_cfw": {
        "en": "⛔ The card has no {cfw}. Install {cfw} and boot the device once first.",
        "th": "⛔ การ์ดนี้ไม่มี {cfw} กรุณาติดตั้ง {cfw} แล้วเปิดเครื่องเกมหนึ่งครั้งก่อน",
    },
    "cfw.pc_drive_blocked": {
        "en": "⛔ PC system drive or non-removable drive selected. Please select a removable SD card.",
        "th": "⛔ ไดรฟ์ระบบหรือไดรฟ์ของคอมพิวเตอร์ถูกบล็อก กรุณาเลือกการ์ด SD ที่ถอดเสียบได้",
    },
    "cfw.device_mismatch": {
        "en": "⛔ Device mismatch: {reason}. This card cannot be used for {dev}.",
        "th": "⛔ รุ่นอุปกรณ์ไม่ตรง: {reason} ไม่สามารถใช้การ์ดนี้กับ {dev}",
    },
    "cfw.confirm_device": {
        "en": "Could not verify device from card. Is this card for {dev}?",
        "th": "ไม่พบข้อมูลรุ่นอุปกรณ์บนการ์ด ยืนยันว่าการ์ดนี้สำหรับ {dev} หรือไม่?",
    },
    "cfw.version_too_low": {
        "en": "⛔ {msg}",
        "th": "⛔ {msg}",
    },
    "cfw.pm_old_warn": {
        "en": "⚠ Old PortMaster ({ver}) detected. Connect WiFi and open Ports → PortMaster once to update it.",
        "th": "⚠ ตรวจพบ PortMaster รุ่นเก่า ({ver}) แนะนำให้ต่อ WiFi แล้วเปิด Ports → PortMaster บนเครื่องเกมหนึ่งครั้งเพื่ออัปเดต",
    },

    # ------------------------------------------------------------------------
    # Ports Page
    # ------------------------------------------------------------------------
    "ports.title": {
        "en": "Recommended Ports",
        "th": "พอร์ตที่แนะนำ",
    },
    "ports.btn_refresh": {
        "en": "🔄 Refresh list",
        "th": "🔄 รีเฟรชรายการ",
    },
    "ports.refreshing": {
        "en": "Refreshing...",
        "th": "กำลังรีเฟรช...",
    },
    "ports.updated_at": {
        "en": "Updated {time}",
        "th": "อัปเดตเมื่อ {time}",
    },
    "ports.refresh_offline": {
        "en": "offline — showing list from {date}",
        "th": "ออฟไลน์ — แสดงรายการจาก {date}",
    },
    "ports.refresh_failed": {
        "en": "offline",
        "th": "ออฟไลน์",
    },
    "ports.local_label": {
        "en": "Local game files:",
        "th": "ไฟล์เกมในเครื่อง:",
    },
    "ports.local_placeholder": {
        "en": r"e.g. D:\port",
        "th": r"เช่น D:\port",
    },
    "ports.scan_btn": {
        "en": "Scan",
        "th": "สแกน",
    },
    "ports.loading_catalog": {
        "en": "Loading PortMaster catalog…",
        "th": "กำลังโหลดแคตตาล็อก PortMaster…",
    },
    "ports.search_placeholder": {
        "en": "🔍 Search game / genre",
        "th": "🔍 ค้นหาเกม / แนว",
    },
    "ports.ready_only": {
        "en": "Ready to play (no files needed)",
        "th": "เล่นได้เลย (ไม่ต้องหาไฟล์)",
    },
    "ports.detail_empty_title": {
        "en": "Select a game to view details",
        "th": "เลือกเกมเพื่อดูรายละเอียด",
    },
    "ports.fbox_title": {
        "en": "Files to install",
        "th": "ไฟล์เกมที่จะใส่",
    },
    "ports.btn_pick_files": {
        "en": "📂 Browse game files…",
        "th": "📂 เลือกโฟลเดอร์ไฟล์เกม…",
    },
    "ports.btn_clear_files": {
        "en": "Clear",
        "th": "ล้าง",
    },
    "ports.btn_store": {
        "en": "🛒 Store / Game page",
        "th": "🛒 ร้านค้า / หน้าเกม",
    },
    "ports.btn_select_rec": {
        "en": "⭐ Select Recommended",
        "th": "⭐ เลือกที่แนะนำ",
    },
    "ports.btn_clear_sel": {
        "en": "Clear Selection",
        "th": "ล้างที่เลือก",
    },
    "ports.btn_select_installed": {
        "en": "🔧 Select Installed Ports",
        "th": "🔧 เลือกเกมที่อยู่บนการ์ด",
    },
    "ports.tooltip_select_installed": {
        "en": "Select all ports already installed on the card — use with 'Clean & Reinstall' to repair",
        "th": "เลือกทุกเกมที่ติดตั้งไว้บนการ์ดแล้ว — ใช้คู่กับ 'ล้างแล้วติดตั้งใหม่' เพื่อซ่อม",
    },
    "ports.chk_clean": {
        "en": "Clean existing port folder and reinstall (repair)",
        "th": "ล้างโฟลเดอร์เกมเดิมแล้วติดตั้งใหม่ (ซ่อม)",
    },
    "ports.tooltip_clean": {
        "en": "Delete old files in selected game folders before installing (preserves save files)\nUsed to fix ports where files were placed incorrectly in earlier versions",
        "th": "ลบไฟล์เก่าในโฟลเดอร์ของเกมที่เลือกก่อนติดตั้ง (เก็บโฟลเดอร์เซฟไว้)\nใช้ซ่อมเกมที่ไฟล์ถูกวางผิดที่จากเวอร์ชันก่อน",
    },
    "ports.btn_install": {
        "en": "Install ▶",
        "th": "ติดตั้ง ▶",
    },
    "ports.summary_scanning": {
        "en": "Scanning and sorting ports…",
        "th": "กำลังสแกนและคัดพอร์ต…",
    },
    "ports.summary_all": {
        "en": "Total {total} ports · ⭐ {must} · ✅ {ok} · ⚠️ {heavy} · ⛔ {broken}",
        "th": "ทั้งหมด {total} พอร์ต · ⭐ {must} · ✅ {ok} · ⚠️ {heavy} · ⛔ {broken}",
    },
    "ports.summary_local_extra": {
        "en": " · 📁 Local game files {count}",
        "th": " · 📁 มีไฟล์เกมในเครื่อง {count}",
    },
    "ports.summary_steam_extra": {
        "en": " · 🎮 From Steam {count}",
        "th": " · 🎮 จาก Steam {count}",
    },
    "ports.footer_sel": {
        "en": "Selected {count} games · Total {size}",
        "th": "เลือก {count} เกม · รวม {size}",
    },
    "ports.files_ready": {
        "en": "Ready",
        "th": "ไม่ต้องใช้",
    },
    "ports.files_needed": {
        "en": "Needs files",
        "th": "ต้องหาไฟล์",
    },
    "ports.badge_free_rtr": {
        "en": "Free · Ready to play",
        "th": "ฟรี · เล่นได้เลย",
    },
    "ports.badge_needs_files": {
        "en": "Requires game files",
        "th": "ต้องใส่ไฟล์เกมเพิ่ม",
    },
    "ports.pick_files_dialog": {
        "en": "Select game files folder for {stem}",
        "th": "เลือกโฟลเดอร์ไฟล์เกมสำหรับ {stem}",
    },

    # ------------------------------------------------------------------------
    # Install Page
    # ------------------------------------------------------------------------
    "install.title_running": {
        "en": "Installing...",
        "th": "กำลังติดตั้ง...",
    },
    "install.prep": {
        "en": "Preparing installation...",
        "th": "กำลังเตรียมการติดตั้ง...",
    },
    "install.btn_cancel": {
        "en": "Cancel",
        "th": "ยกเลิก",
    },
    "install.btn_back": {
        "en": "◀ Back to Ports",
        "th": "◀ กลับไปเลือกเกม",
    },
    "install.btn_view_failed": {
        "en": "⚠ View {count} failed ports",
        "th": "⚠ ดูรายการที่ติดตั้งไม่สำเร็จ ({count} เกม)",
    },
    "install.btn_swap_card1": {
        "en": "💾 Swap in Card 1 to copy runtimes/box art",
        "th": "💾 สลับใส่ Card 1 เพื่อก๊อป runtimes/box art",
    },
    "install.complete_ok": {
        "en": "✔ Install complete! Installed {count} ports ({size}) in {time}s",
        "th": "✔ ติดตั้งเสร็จสมบูรณ์! ลงไป {count} เกม ({size}) ในเวลา {time} วินาที",
    },
    "install.complete_warn": {
        "en": "Install completed with warnings ({fail} failed) — installed {ok} ports ({size}) in {time}s",
        "th": "ติดตั้งเสร็จแล้วแบบมีคำเตือน (ไม่สำเร็จ {fail} เกม) — ลงสำเร็จ {ok} เกม ({size}) ในเวลา {time} วินาที",
    },
    "install.cancelled": {
        "en": "Installation cancelled",
        "th": "ยกเลิกการติดตั้งแล้ว",
    },
    "install.stopped": {
        "en": "Stopped",
        "th": "หยุดแล้ว",
    },

    # ------------------------------------------------------------------------
    # Dialogs & Prompts
    # ------------------------------------------------------------------------
    "dialog.pak_title": {
        "en": "Update PortMaster pak",
        "th": "อัปเดต PortMaster pak",
    },
    "dialog.pak_head": {
        "en": "Update PortMaster pak ({cfw})",
        "th": "อัปเดต PortMaster pak ({cfw})",
    },
    "dialog.pak_versions": {
        "en": "Current version: <b>{current}</b><br>Latest version: <b>{latest}</b>",
        "th": "เวอร์ชันปัจจุบัน: <b>{current}</b><br>เวอร์ชันล่าสุด: <b>{latest}</b>",
    },
    "dialog.pak_desc": {
        "en": "PortPicker will download the latest pak, safely preserve existing configs and runtimes, and update to the new version.",
        "th": "PortPicker จะดาวน์โหลด pak ตัวล่าสุด สำรองข้อมูลและ runtime เดิมไว้ แล้วอัปเดตให้โดยอัตโนมัติ",
    },
    "dialog.pak_btn_start": {
        "en": "Start Update",
        "th": "เริ่มอัปเดต",
    },
    "dialog.pak_btn_close": {
        "en": "Close",
        "th": "ปิด",
    },
    "dialog.pak_btn_cancel": {
        "en": "Cancel",
        "th": "ยกเลิก",
    },
    "dialog.pak_prep_download": {
        "en": "Preparing download...",
        "th": "กำลังเตรียมดาวน์โหลด...",
    },
    "dialog.pak_cancelling": {
        "en": "Cancelling...",
        "th": "กำลังยกเลิก...",
    },
    "dialog.pak_success": {
        "en": "✔ PortMaster pak updated successfully",
        "th": "✔ อัปเดต PortMaster pak สำเร็จ",
    },
    "dialog.pak_cancelled": {
        "en": "Pak update cancelled",
        "th": "ยกเลิกการอัปเดตแล้ว",
    },
    "dialog.pak_error": {
        "en": "✖ Error: {err}",
        "th": "✖ ผิดพลาด: {err}",
    },

    "dialog.swap_title": {
        "en": "Swap in Card 1 (muOS)",
        "th": "สลับใส่ Card 1 (muOS)",
    },
    "dialog.swap_head": {
        "en": "Swap in Card 1 (muOS Dual-SD)",
        "th": "สลับใส่ Card 1 (muOS Dual-SD)",
    },
    "dialog.swap_desc": {
        "en": "Remove Card 2 and insert Card 1 (the card with the MUOS folder) into your computer.\nPortPicker will copy runtimes and box art to Card 1.",
        "th": "ถอด Card 2 แล้วเสียบ Card 1 (การ์ดที่มีโฟลเดอร์ MUOS) เข้ากับคอมพิวเตอร์\nPortPicker จะก๊อป runtimes และ box art ไปยัง Card 1 ให้",
    },
    "dialog.swap_rescan": {
        "en": "🔄 Rescan",
        "th": "🔄 ตรวจหา",
    },
    "dialog.swap_browse": {
        "en": "📂 Browse Folder…",
        "th": "📂 เลือกโฟลเดอร์…",
    },
    "dialog.swap_skip": {
        "en": "Skip",
        "th": "ข้าม",
    },
    "dialog.swap_copy": {
        "en": "Copy to Card 1",
        "th": "ก๊อปปี้ไป Card 1",
    },
    "dialog.swap_copying": {
        "en": "Copying runtimes and box art to Card 1...",
        "th": "กำลังก๊อปปี้ runtimes และ box art ไปยัง Card 1...",
    },
    "dialog.swap_prompt": {
        "en": "Insert Card 1 and click Rescan, or browse to Card 1 folder",
        "th": "เสียบ Card 1 แล้วกดตรวจหา หรือเลือกโฟลเดอร์ Card 1",
    },
    "dialog.swap_found": {
        "en": "✔ Found muOS Card 1 at {path}",
        "th": "✔ พบ muOS Card 1 ที่ {path}",
    },
    "dialog.swap_invalid": {
        "en": "⚠ Folder {path} is not muOS Card 1 (MUOS/ folder not found)",
        "th": "⚠ โฟลเดอร์ {path} ไม่ใช่ muOS Card 1 (ไม่พบโฟลเดอร์ MUOS/)",
    },
    "dialog.swap_error": {
        "en": "Error during copy: {err}",
        "th": "เกิดข้อผิดพลาดในการก๊อปปี้: {err}",
    },

    "dialog.fail_title": {
        "en": "Failed to install {count} games",
        "th": "ติดตั้งไม่สำเร็จ {count} เกม",
    },
    "dialog.fail_reason": {
        "en": "Reason: {hint}",
        "th": "สาเหตุ: {hint}",
    },
    "dialog.fail_actual": {
        "en": "Actual error: {err}",
        "th": "ข้อความจริง: {err}",
    },
    "dialog.fail_btn_copy": {
        "en": "📋 Copy Text",
        "th": "📋 ก๊อปข้อความ",
    },
    "dialog.fail_btn_close": {
        "en": "Close",
        "th": "ปิด",
    },
    "dialog.fail_btn_retry": {
        "en": "↻ Retry installing these {count} games",
        "th": "↻ ลองติดตั้ง {count} เกมนี้ใหม่",
    },

    "dialog.quit_installing_text": {
        "en": "Installing in progress — if you exit now, currently installing games will be incomplete.",
        "th": "กำลังติดตั้งอยู่ — ถ้าปิดตอนนี้ เกมที่กำลังลงจะยังไม่เสร็จ",
    },
    "dialog.quit_installing_info": {
        "en": "You can reopen and reinstall; incomplete files will be copied again.",
        "th": "เปิดใหม่แล้วกดติดตั้งซ้ำได้ ไฟล์ที่ค้างจะถูกก๊อปใหม่ให้",
    },
    "dialog.quit_title": {
        "en": "Exit PortPicker?",
        "th": "ปิด PortPicker?",
    },
    "dialog.quit_btn_exit": {
        "en": "Exit Program",
        "th": "ปิดโปรแกรม",
    },
    "dialog.quit_btn_stay": {
        "en": "Stay",
        "th": "ไม่ปิด",
    },

    "dialog.broken_warning": {
        "en": "These games are reported as broken or unsupported. Do you want to proceed?\n\n{games}",
        "th": "เกมเหล่านี้มีรายงานว่ารันไม่ได้หรือไม่รองรับ จะติดตั้งต่อไหม?\n\n{games}",
    },
    "dialog.kofi_btn": {
        "en": "☕ Support",
        "th": "☕ สนับสนุน",
    },

    # ------------------------------------------------------------------------
    # Failure hints
    # ------------------------------------------------------------------------
    "hint.md5": {
        "en": "Download was incomplete or corrupted — click retry",
        "th": "ไฟล์โหลดมาไม่ครบ — กดลองใหม่",
    },
    "hint.404": {
        "en": "PortMaster has updated this file — click retry (app will refresh catalog)",
        "th": "PortMaster อัปเดตไฟล์นี้แล้ว — กดลองใหม่ (แอปจะโหลดรายการล่าสุดให้)",
    },
    "hint.timeout": {
        "en": "Network disconnected or slow — click retry",
        "th": "เน็ตหลุดหรือช้า — กดลองใหม่",
    },
    "hint.disk_full": {
        "en": "Card is full — free up space by removing unused games",
        "th": "การ์ดเต็ม — ลบเกมที่ไม่เล่นออกก่อน",
    },
    "hint.permission": {
        "en": "Card is write-protected or locked — check physical lock switch or close programs using files",
        "th": "การ์ดถูกล็อก/เขียนไม่ได้ — เช็กสวิตช์ lock หรือปิดโปรแกรมที่เปิดไฟล์อยู่",
    },
    "hint.in_use": {
        "en": "File is open in another program — close Explorer or that program and retry",
        "th": "ไฟล์ถูกโปรแกรมอื่นเปิดอยู่ — ปิด Explorer/โปรแกรมนั้นแล้วลองใหม่",
    },
    "hint.card_removed": {
        "en": "File or folder not found — card may have disconnected; re-insert and retry",
        "th": "หาไฟล์/โฟลเดอร์ไม่เจอ — การ์ดอาจหลุด เสียบใหม่แล้วลองอีกครั้ง",
    },
    "hint.bad_zip": {
        "en": "Corrupted zip file — clear download cache and retry",
        "th": "ไฟล์ zip เสีย — ลบแคชดาวน์โหลดแล้วลองใหม่",
    },
    "hint.path_too_long": {
        "en": "File name or path too long for Windows",
        "th": "ชื่อไฟล์ยาวเกินไปสำหรับ Windows",
    },
    "hint.unknown": {
        "en": "Unknown cause — copy error text below and report",
        "th": "ไม่ทราบสาเหตุ — ก๊อปข้อความด้านล่างส่งมาได้",
    },
}


def t(key: str, default: str | None = None, **kwargs) -> str:
    """Look up a localized string by key for the current language.

    Fallback order:
      1. Entry in STRINGS for CURRENT_LANG
      2. Entry in STRINGS for 'en'
      3. Entry in STRINGS for 'th'
      4. Provided default
      5. The key itself
    """
    entry = STRINGS.get(key)
    if entry is not None and isinstance(entry, dict):
        text = entry.get(CURRENT_LANG) or entry.get("en") or entry.get("th")
        if text is None:
            text = default if default is not None else key
    elif entry is not None:
        text = str(entry)
    else:
        text = default if default is not None else key

    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
