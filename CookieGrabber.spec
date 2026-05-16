# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir: dist/CookieGrabber/CookieGrabber.exe"""

from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas: list = []
binaries: list = []
hiddenimports = [
    "gala_9static_proxy",
    "gala_9static_proxy.flow",
    "gala_9static_proxy.parser",
    "gala_9static_proxy.pool",
    "gala_9static_proxy.validator",
    "gala_9static_proxy.session",
    "gala_9static_proxy.today_list_cache",
    "gala_9static_proxy.timeout_state",
    "gala_9static_proxy.today_list_activation",
    "playwright",
    "playwright.sync_api",
    "playwright._impl",
    "googleapiclient",
    "googleapiclient.discovery",
    "google.auth",
    "google_auth_httplib2",
    "httplib2",
    "customtkinter",
    "playwright_cookie_blocker",
    "tldextract",
    "tzdata",
    "cookie_grabber.updates.github_release",
]

for pkg in ("customtkinter", "playwright", "tldextract"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ["src/cookie_grabber/main.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CookieGrabber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="CookieGrabber",
)
