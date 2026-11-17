#!/usr/bin/env python3
"""Create CPA-native release archive/checksum names. Run from any directory."""
import hashlib
import platform
from pathlib import Path
import re
import sys
import zipfile
root=Path(__file__).resolve().parents[1]
version=re.search(r'#define VERSION "([^"]+)"',(root/'src/plugin.c').read_text()).group(1)
arch=sys.argv[1] if len(sys.argv)>1 else {'x86_64':'amd64','aarch64':'arm64'}[platform.machine()]
name=f'pi-cpa-session-cache_{version}_linux_{arch}.zip'
libname=f'pi-cpa-session-cache-v{version}.so'
archive=root/'dist'/name
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    z.write(root/'dist/pi-cpa-session-cache.so',libname)
    z.write(root/'LICENSE','LICENSE')
checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
(root/'dist/checksums.txt').write_text(f'{checksum}  {name}\n')
print(archive)
