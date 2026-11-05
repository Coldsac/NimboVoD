# NimboVoD

Turn one or more M3U playlists into a folder of `.strm` files, then leave out anything already in an Emby library.

Download `nimbovod.exe` from the [v1 release](https://github.com/Coldsac/NimboVoD/releases/tag/v1). A home-network Emby address such as `http://192.168.x.x:8096` works because the program runs on your computer.

## Windows program

1. Add playlist links or choose `.m3u` files, then click **Load playlists**.
2. Enter the Emby address and API key, then click **Pull library**.
3. Choose the save folder.
4. Click **View plan** to see what will be kept and left out.
5. Click **Write .strm files** when the plan looks right. Nothing is written or deleted before that, unless automatic updates are turned on in Settings.

Settings can open NimboVoD when you sign in to Windows, and can reload playlists, read Emby, and update the `.strm` folder when the program opens.

Windows may say it protected your PC because the file is not signed. Choose **More info**, then **Run anyway**. The program is 64-bit and needs Windows 10 or newer.

The built program is not stored in git. Settings, including the API key, are saved in `%LOCALAPPDATA%\NimboVoD\settings.json`.

## Build the Windows program

On 64-bit Windows, from this repository, with Python 3.12:

```
py -3.12 -m pip install pyinstaller
py -3.12 -m PyInstaller --noconfirm --clean --windowed --onefile --name nimbovod --distpath dist --add-data "src/assets/nimbovod-cleaner.py;." src/assets/nimbovod_gui.py
copy dist\nimbovod.exe nimbovod.exe
```

## Command line

The same cleaner is in `src/assets/nimbovod-cleaner.py`. It only needs Python 3.

```
py -3.12 src/assets/nimbovod-cleaner.py --m3u https://example.com/a.m3u --out "D:\Media\strm" --emby http://192.168.1.20:8096 --api-key KEY --apply
```
