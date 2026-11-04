# NimboVoD

Turn one or more M3U playlists into a folder of `.strm` files, then leave out anything already in an Emby library.

A home-network Emby address such as `http://192.168.x.x:8096` works in the Windows program, because that program runs on your computer. The website cannot reach a private address.

## Windows program

1. On the NimboVoD page, enter the playlist links, the Emby address, and the save folder.
2. Turn on **Put the API key in the downloaded config**.
3. Download **Windows program** and **Config**.
4. Put `nimbovod-cleaner.exe` and `nimbovod.config.json` in the same folder.
5. Double-click `nimbovod-cleaner.exe` for a dry run.
6. When the plan looks right:

```
nimbovod-cleaner.exe --apply
```

Windows may say it protected your PC because the file is not signed. Choose **More info**, then **Run anyway**. The program is 64-bit and needs Windows 10 or newer.

## Python

The same cleaner is in `src/assets/nimbovod-cleaner.py`. It only needs Python 3.

```
python3 src/assets/nimbovod-cleaner.py --config nimbovod.config.json --apply
```

## Develop

```
npm install
npm run dev
```

## Build the Windows program

On a 64-bit Windows machine, from this repository:

```
curl -L -o cleaner-win/python-embed.zip https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
cd cleaner-win
cargo build --release
```

The program is `cleaner-win/target/release/nimbovod-cleaner.exe`. The website button serves that same program. It is not stored in git.
