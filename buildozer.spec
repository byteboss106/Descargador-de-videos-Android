[app]
title = YT Downloader
package.name = ytdownloader
package.domain = org.example
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.1
requirements = python3,kivy,yt-dlp,certifi,openssl
orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 36
android.minapi = 24
android.ndk = 29
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True

# Python 3.14 en el dispositivo requiere la rama develop de python-for-android
p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 1
