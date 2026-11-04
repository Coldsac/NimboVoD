use std::env;
use std::fs;
use std::io::{self, Cursor, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{self, Command};
use zip::ZipArchive;

const EMBED: &[u8] = include_bytes!("../python-embed.zip");
const SCRIPT: &[u8] = include_bytes!("../../src/assets/nimbovod-cleaner.py");

fn main() {
    match run() {
        Ok(code) => process::exit(code),
        Err(error) => {
            eprintln!("nimbovod: {error}");
            if env::args().len() == 1 {
                let _ = pause();
            }
            process::exit(1);
        }
    }
}

fn run() -> io::Result<i32> {
    let runtime = ensure_runtime()?;
    let python = runtime.join("python.exe");
    let script = runtime.join("nimbovod-cleaner.py");
    if !python.is_file() || !script.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::Other,
            "The cleaner runtime is incomplete. Delete %LOCALAPPDATA%\\NimboVoD and run this program again.",
        ));
    }

    let mut command = Command::new(&python);
    command.arg(&script);
    command.args(env::args().skip(1));
    command.env("PYTHONUTF8", "1");
    command.env("PYTHONIOENCODING", "utf-8");
    command.env("PYTHONDONTWRITEBYTECODE", "1");
    if let Ok(home) = exe_dir() {
        command.env("NIMBOVOD_HOME", home);
    }
    if env::args().len() == 1 {
        command.env("NIMBOVOD_PAUSE", "1");
    }

    let status = command.status().map_err(|error| {
        io::Error::new(io::ErrorKind::Other, format!("Could not start the cleaner ({error})."))
    })?;
    Ok(status.code().unwrap_or(1))
}

fn ensure_runtime() -> io::Result<PathBuf> {
    let dir = runtime_dir();
    let stamp_path = dir.join(".nimbovod-stamp");
    let stamp = stamp_value();
    if stamp_path.is_file() && fs::read_to_string(&stamp_path).ok().as_deref() == Some(stamp.as_str()) {
        fs::write(dir.join("nimbovod-cleaner.py"), SCRIPT)?;
        return Ok(dir);
    }
    println!("Preparing NimboVoD. This only happens once.");
    let _ = io::stdout().flush();
    if dir.exists() {
        fs::remove_dir_all(&dir)?;
    }
    fs::create_dir_all(&dir)?;
    unpack(EMBED, &dir)?;
    fs::write(dir.join("nimbovod-cleaner.py"), SCRIPT)?;
    fs::write(&stamp_path, stamp)?;
    Ok(dir)
}

fn stamp_value() -> String {
    format!("{:016x}-{}-{}", fnv(EMBED), EMBED.len(), fnv(SCRIPT))
}

fn runtime_dir() -> PathBuf {
    if let Some(local) = env::var_os("LOCALAPPDATA") {
        return PathBuf::from(local).join("NimboVoD").join("runtime");
    }
    if let Some(home) = env::var_os("USERPROFILE") {
        return PathBuf::from(home).join("AppData").join("Local").join("NimboVoD").join("runtime");
    }
    env::temp_dir().join("NimboVoD").join("runtime")
}

fn exe_dir() -> io::Result<PathBuf> {
    let exe = env::current_exe()?;
    exe.parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| io::Error::new(io::ErrorKind::Other, "Could not find the program folder."))
}

fn unpack(bytes: &[u8], dest: &Path) -> io::Result<()> {
    let mut archive = ZipArchive::new(Cursor::new(bytes))
        .map_err(|error| io::Error::new(io::ErrorKind::Other, format!("Could not unpack the cleaner ({error}).")))?;
    for index in 0..archive.len() {
        let mut file = archive
            .by_index(index)
            .map_err(|error| io::Error::new(io::ErrorKind::Other, format!("Could not unpack the cleaner ({error}).")))?;
        let name = file.name().replace('\\', "/");
        if name.ends_with('/') || !safe_relative(&name) {
            continue;
        }
        let target = dest.join(&name);
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut data = Vec::with_capacity(file.size() as usize);
        file.read_to_end(&mut data)?;
        fs::write(target, data)?;
    }
    Ok(())
}

fn safe_relative(path: &str) -> bool {
    !path.is_empty()
        && !path.starts_with('/')
        && !path.starts_with('\\')
        && !path.contains(':')
        && !path.split(['/', '\\']).any(|part| part == ".." || part.is_empty())
}

fn fnv(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf29ce484222325u64;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

fn pause() -> io::Result<()> {
    eprint!("\nPress Enter to close...");
    let _ = io::stderr().flush();
    let mut line = String::new();
    io::stdin().read_line(&mut line)?;
    Ok(())
}
