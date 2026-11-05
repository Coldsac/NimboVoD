# NimboVoD

NimboVoD is a Windows program that turns M3U playlists into a folder of `.strm` files and leaves out anything already in an Emby library.

Download [nimbovod.exe](https://github.com/Coldsac/NimboVoD/releases/tag/v1). It runs on your computer, so a home-network address such as `http://192.168.1.20:8096` works. The program is 64-bit and needs Windows 10 or newer. Windows may say it protected your PC because the file is not signed. Choose **More info**, then **Run anyway**.

The program is not stored in git. Settings, including the API key, are saved in plain text at `%LOCALAPPDATA%\NimboVoD\settings.json`.

## Use the window

**Settings**

- **Run NimboVoD when Windows starts** opens the program when you sign in. It uses your account and does not need administrator rights.
- **Automatically pull the M3U playlists** reloads the saved links and local playlist files.
- **Automatically pull the Emby library** reads Emby with the saved address and API key.
- **Automatically update the .strm folder** adds new `.strm` files and removes ones the plan leaves out.

These automatic steps are off until you turn them on. They run when the program opens. **Refresh every** runs the checked steps again after that many minutes. Leave it blank to refresh only when NimboVoD opens. Updating the folder does not ask again.

**Playlists**

Add M3U links or choose local `.m3u` files, then click **Load playlists**. The same stream URL is kept once.

**Folder layout**

- **By group** puts each group-title in its own folder.
- **Flat** puts every file in one folder.
- **Movies & TV** uses `Movies/` for movies and `TV/Show/Season 01/` for episodes.

**Strip quality tags from file names** is on by default. It removes tags such as `1080p`, `x265`, `[WEB]`, `[x264]`, `[TS]`, `Megusta`, and empty brackets. The full list is shown in the window. **Also strip these tags** removes extra words you type, both from file names and when matching Emby.

**Remove duplicate .strm files** is on by default. The first stream that cleans up to a file is kept. Later copies are left out, and numbered files already in the folder, such as `Name (2).strm`, are removed.

**Emby library**

Enter the server address and API key, then click **Pull library**. Click **Load libraries** and uncheck any library that should be left out of the comparison. Until you load that list, every library is read. User id is optional. You can also upload a library export or paste titles, one per line. A season already in Emby is left out. A later season that Emby does not have is added. Titles like `Show S01E02` are matched to that season. A subtitle after a colon, such as `Show: Other Name`, still matches `Show`.

**Where the .strm files go**

Choose the save folder.

- **View plan** opens the list of files that will be kept and left out.
- **Write .strm files** asks before it changes the folder. It writes new files and removes ones the plan leaves out. Other files in that folder stay.
- **Save report** writes that plan to a text file.

## Build

On 64-bit Windows, from this repository, with Python 3.12:

```
py -3.12 -m pip install pyinstaller
py -3.12 -m PyInstaller --noconfirm --clean --windowed --onefile --name nimbovod --distpath dist --add-data "src/assets/nimbovod-cleaner.py;." src/assets/nimbovod_gui.py
copy dist\nimbovod.exe nimbovod.exe
```

## Command line

`src/assets/nimbovod-cleaner.py` is the same cleaner the window uses. Without `--apply` it only prints the plan.

```
py -3.12 src/assets/nimbovod-cleaner.py --m3u https://example.com/a.m3u --out "D:\Media\strm" --emby http://192.168.1.20:8096 --api-key KEY --apply
```

`--layout` is `group`, `flat`, or `library`. `--keep-quality` leaves quality tags in file names. `--strip-tag YTS` removes an extra tag. `--keep-duplicate-strm` keeps a second file instead of leaving the duplicate out.
