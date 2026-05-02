import re, sys
from pathlib import Path

MAINTAINER_NAME = "ZaidanPrjkt"

TARGET_FILES = [
    "axion_sdk/ax_deviceinfo/src/com/android/axion/deviceinfo/DeviceInfoProvider.kt",
    "packages/apps/Personalizations/src/com/custom/settings/BannerPreferenceController.kt",
]

PATTERN = re.compile(
    r'(?:android\.os\.)?SystemProperties\.get\("persist\.sys\.axion_maintainer"[^)]*\)'
    r'\s*\n?\s*\.replace\("_",\s*" "\)',
    re.MULTILINE
)

for rel in TARGET_FILES:
    f = Path(rel)
    if not f.exists():
        print(f"[error] file not found: {f}")
        sys.exit(1)
    content = f.read_text(encoding="utf-8")
    found = PATTERN.findall(content)
    if len(found) == 0:
        print(f"[skip]  {rel}")
        continue
    patched = PATTERN.sub(f'"{MAINTAINER_NAME}"', content)
    f.write_text(patched, encoding="utf-8")
    print(f"[patch] {rel} -> \"{MAINTAINER_NAME}\"")
