[app]
title = Private Vault
package.name = privatevault
package.domain = org.privatevault
source.dir = .
source.include_exts = py
version = 1.0
requirements = python3,kivy==2.3.0,pillow
orientation = portrait
fullscreen = 0
android.permissions = READ_MEDIA_IMAGES,READ_MEDIA_VIDEO,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = False
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
