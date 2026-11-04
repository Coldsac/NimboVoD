import { useEffect, useMemo, useRef, useState, type ButtonHTMLAttributes, type DragEvent, type ReactNode } from "react";
import { strToU8, zipSync } from "fflate";
import {
  Download,
  FileArchive,
  Filter,
  FolderOpen,
  Library,
  Link2,
  Loader2,
  Plus,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import cleanerSource from "../assets/nimbovod-cleaner.py?raw";
import { fetchEmbyLibrary, fetchPlaylist } from "@/lib/remote";
import { previewPathPrefix } from "@/lib/preview-path-prefix";
import { SAMPLE_ENTRIES_NOTE, SAMPLE_LIBRARY, SAMPLE_PLAYLIST } from "@/lib/sample";
import {
  looksLikePlaylist,
  parseLibraryText,
  parseM3u,
  planLibrary,
  reportText,
  strmFileMap,
  type EmbyItem,
  type LayoutMode,
  type PlannedFile,
  type StreamEntry,
} from "@/lib/nimbovod";

const STORAGE_KEY = "nimbovod-settings";

const inputClass =
  "min-h-11 w-full rounded-md border border-line bg-canvas px-3 text-sm text-fg placeholder:text-muted";

type FilterMode = "all" | "keep" | "drop";
type PlaylistLink = { id: string; url: string };
type LoadedSource = {
  id: string;
  kind: "url" | "file" | "sample";
  label: string;
  entries: StreamEntry[];
  skipped: number;
};
type DirectoryHandle = {
  name: string;
  getDirectoryHandle: (name: string, options?: { create?: boolean }) => Promise<DirectoryHandle>;
  getFileHandle: (
    name: string,
    options?: { create?: boolean },
  ) => Promise<{ createWritable: () => Promise<{ write: (data: string) => Promise<void>; close: () => Promise<void> }> }>;
  removeEntry: (name: string) => Promise<void>;
};

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function messageOf(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  if (typeof error === "object" && error && "message" in error) {
    const message = (error as { message: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return "Something went wrong. Try again.";
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function stamp(): string {
  return new Date().toISOString().slice(0, 10);
}

function newId(): string {
  return crypto.randomUUID();
}

function playlistLabel(url: string): string {
  try {
    const parsed = new URL(url);
    const last = parsed.pathname.split("/").filter(Boolean).pop();
    return last || parsed.hostname;
  } catch {
    return url;
  }
}

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function windowsCleanerUrl(): string {
  const path = "/nimbovod-cleaner.exe";
  if (typeof window === "undefined") return path;
  const prefix = previewPathPrefix(window.location.hostname, window.location.pathname);
  return prefix ? `${prefix}${path}` : path;
}

function zipBytes(files: Record<string, string>): Uint8Array {
  const packed: Record<string, Uint8Array> = {};
  for (const [path, body] of Object.entries(files)) packed[path] = strToU8(body);
  return zipSync(packed, { level: 0 });
}

function blobFromBytes(bytes: Uint8Array, type: string): Blob {
  const copy = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(copy).set(bytes);
  return new Blob([copy], { type });
}

function isLayout(value: unknown): value is LayoutMode {
  return value === "group" || value === "flat" || value === "library";
}

function readSavedLinks(saved: Record<string, unknown>): PlaylistLink[] | null {
  if (Array.isArray(saved.links)) {
    const next = saved.links.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const record = item as { id?: unknown; url?: unknown };
      if (typeof record.url !== "string") return [];
      return [{ id: typeof record.id === "string" ? record.id : newId(), url: record.url }];
    });
    if (next.length > 0) return next;
  }
  if (typeof saved.m3uUrl === "string") return [{ id: "link-1", url: saved.m3uUrl }];
  return null;
}

async function pickDirectory(): Promise<DirectoryHandle> {
  const picker = (
    window as Window & {
      showDirectoryPicker?: (options?: { mode?: "readwrite"; id?: string }) => Promise<DirectoryHandle>;
    }
  ).showDirectoryPicker;
  if (!picker) {
    throw new Error(
      "This browser can't write straight to a folder. Download the zip, or run the cleaner with the save folder.",
    );
  }
  return picker({ mode: "readwrite", id: "nimbovod-strm" });
}

async function ensureDir(root: DirectoryHandle, parts: string[]): Promise<DirectoryHandle> {
  let dir = root;
  for (const part of parts) dir = await dir.getDirectoryHandle(part, { create: true });
  return dir;
}

async function writeStrmTree(
  root: DirectoryHandle,
  files: PlannedFile[],
  onProgress: (done: number, total: number) => void,
): Promise<{ written: number; removed: number }> {
  let written = 0;
  let removed = 0;
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    if (!file) continue;
    const parts = file.relativePath.split("/").filter(Boolean);
    const name = parts.pop();
    if (!name) continue;
    const dir = await ensureDir(root, parts);
    if (file.disposition === "keep") {
      const handle = await dir.getFileHandle(name, { create: true });
      const writable = await handle.createWritable();
      await writable.write(`${file.url.trim()}\n`);
      await writable.close();
      written += 1;
    } else {
      try {
        await dir.removeEntry(name);
        removed += 1;
      } catch (caught) {
        if (!(caught instanceof DOMException) || caught.name !== "NotFoundError") throw caught;
      }
    }
    if (index % 20 === 0 || index === files.length - 1) onProgress(index + 1, files.length);
  }
  return { written, removed };
}

export function NimboVoDApp() {
  const [ready, setReady] = useState(false);
  const [links, setLinks] = useState<PlaylistLink[]>([{ id: "link-1", url: "" }]);
  const [sources, setSources] = useState<LoadedSource[]>([]);
  const [outDir, setOutDir] = useState("./strm");
  const [folderName, setFolderName] = useState("");
  const folderHandle = useRef<DirectoryHandle | null>(null);
  const [embyUrl, setEmbyUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [userId, setUserId] = useState("");
  const [layout, setLayout] = useState<LayoutMode>("group");
  const [stripQuality, setStripQuality] = useState(true);
  const [useYears, setUseYears] = useState(true);
  const [matchFilenames, setMatchFilenames] = useState(true);
  const [includeKey, setIncludeKey] = useState(false);
  const [library, setLibrary] = useState<EmbyItem[] | null>(null);
  const [libraryLabel, setLibraryLabel] = useState("");
  const [pasted, setPasted] = useState("");
  const [filter, setFilter] = useState<FilterMode>("all");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [hot, setHot] = useState<"playlist" | "library" | null>(null);
  const playlistInput = useRef<HTMLInputElement>(null);
  const libraryInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const saved = JSON.parse(raw) as Record<string, unknown>;
        const restored = readSavedLinks(saved);
        if (restored) setLinks(restored);
        if (typeof saved.outDir === "string" && saved.outDir.trim()) setOutDir(saved.outDir);
        if (typeof saved.embyUrl === "string") setEmbyUrl(saved.embyUrl);
        if (typeof saved.userId === "string") setUserId(saved.userId);
        if (isLayout(saved.layout)) setLayout(saved.layout);
        if (typeof saved.stripQuality === "boolean") setStripQuality(saved.stripQuality);
        if (typeof saved.useYears === "boolean") setUseYears(saved.useYears);
        if (typeof saved.matchFilenames === "boolean") setMatchFilenames(saved.matchFilenames);
      }
    } catch {
      /* ignore broken local settings */
    }
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ links, outDir, embyUrl, userId, layout, stripQuality, useYears, matchFilenames }),
    );
  }, [ready, links, outDir, embyUrl, userId, layout, stripQuality, useYears, matchFilenames]);

  const entries = useMemo(() => {
    if (sources.length === 0) return null;
    return sources.flatMap((source) => source.entries).map((entry, index) => ({ ...entry, id: `e-${index}` }));
  }, [sources]);
  const skipped = sources.reduce((total, source) => total + source.skipped, 0);
  const sourceLabel =
    sources.length === 0
      ? ""
      : sources.length <= 2
        ? sources.map((source) => source.label).join(" · ")
        : `${sources.length} playlists`;
  const playlistUrls = links.map((link) => link.url.trim()).filter(Boolean);
  const savePath = outDir.trim() || "./strm";

  const result = useMemo(() => {
    if (!entries) return null;
    return planLibrary(entries, library, { layout, stripQuality, useYears, matchFilenames });
  }, [entries, library, layout, stripQuality, useYears, matchFilenames]);

  const visible = useMemo(() => {
    if (!result) return [];
    const needle = query.trim().toLowerCase();
    return result.files.filter((file) => {
      if (filter === "keep" && file.disposition !== "keep") return false;
      if (filter === "drop" && file.disposition !== "drop") return false;
      if (!needle) return true;
      return [file.title, file.relativePath, file.reason, file.group].some((value) =>
        value.toLowerCase().includes(needle),
      );
    });
  }, [result, filter, query]);

  function clearStatus() {
    setError(null);
    setNote(null);
  }

  function adoptLibrary(items: EmbyItem[], label: string) {
    setLibrary(items);
    setLibraryLabel(label);
  }

  async function loadPlaylistUrls(event?: { preventDefault: () => void }) {
    event?.preventDefault();
    if (playlistUrls.length === 0) return;
    clearStatus();
    const loaded: LoadedSource[] = [];
    const failures: string[] = [];
    try {
      for (let index = 0; index < playlistUrls.length; index += 1) {
        const url = playlistUrls[index] ?? "";
        setBusy(`Downloading playlist ${index + 1} of ${playlistUrls.length}…`);
        try {
          const { text } = await fetchPlaylist({ data: { url } });
          if (!looksLikePlaylist(text)) throw new Error("not an M3U playlist");
          const parsed = parseM3u(text);
          if (parsed.entries.length === 0) throw new Error("no streams found");
          loaded.push({
            id: newId(),
            kind: "url",
            label: playlistLabel(url),
            entries: parsed.entries,
            skipped: parsed.skipped,
          });
        } catch (caught) {
          failures.push(`${playlistLabel(url)}: ${messageOf(caught)}`);
        }
      }
      if (loaded.length === 0) {
        setError(failures.join(" ") || "No playlists could be loaded.");
        return;
      }
      setSources((current) => [...current.filter((source) => source.kind === "file"), ...loaded]);
      setFilter("all");
      setQuery("");
      if (failures.length > 0) setError(`Loaded ${loaded.length} of ${playlistUrls.length}. ${failures.join(" ")}`);
    } finally {
      setBusy(null);
    }
  }

  async function onPlaylistFiles(fileList: FileList | File[] | undefined) {
    const files = [...(fileList ?? [])];
    if (files.length === 0) return;
    clearStatus();
    setBusy(files.length === 1 ? "Reading playlist…" : `Reading ${files.length} playlists…`);
    const loaded: LoadedSource[] = [];
    const failures: string[] = [];
    try {
      await wait(20);
      for (const file of files) {
        if (file.size > 80 * 1024 * 1024) {
          failures.push(`${file.name} is over 80 MB.`);
          continue;
        }
        try {
          const text = await file.text();
          if (!looksLikePlaylist(text)) throw new Error("not an M3U playlist");
          const parsed = parseM3u(text);
          if (parsed.entries.length === 0) throw new Error("no streams found");
          loaded.push({
            id: newId(),
            kind: "file",
            label: file.name,
            entries: parsed.entries,
            skipped: parsed.skipped,
          });
        } catch (caught) {
          failures.push(`${file.name}: ${messageOf(caught)}`);
        }
      }
      if (loaded.length > 0) {
        setSources((current) => [...current.filter((source) => source.kind !== "sample"), ...loaded]);
        setFilter("all");
        setQuery("");
      }
      if (failures.length > 0) setError(failures.join(" "));
    } finally {
      setBusy(null);
    }
  }

  async function pullEmby() {
    clearStatus();
    setBusy("Reading Emby library…");
    try {
      const payload = await fetchEmbyLibrary({ data: { baseUrl: embyUrl, apiKey, userId } });
      if (payload.items.length === 0) throw new Error("Emby returned no movies, shows, or episodes.");
      const suffix = payload.truncated ? ` of ${formatCount(payload.total)}` : "";
      adoptLibrary(payload.items, `Emby · ${formatCount(payload.items.length)}${suffix} items`);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(null);
    }
  }

  async function onLibraryFile(file: File | undefined) {
    if (!file) return;
    clearStatus();
    setBusy("Reading library export…");
    try {
      await wait(20);
      const items = parseLibraryText(await file.text());
      adoptLibrary(items, `${file.name} · ${formatCount(items.length)} titles`);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(null);
    }
  }

  function usePastedTitles() {
    clearStatus();
    try {
      const items = parseLibraryText(pasted);
      adoptLibrary(items, `Pasted titles · ${formatCount(items.length)}`);
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  function loadSample() {
    clearStatus();
    const parsed = parseM3u(SAMPLE_PLAYLIST);
    setSources([
      { id: newId(), kind: "sample", label: "Sample playlist", entries: parsed.entries, skipped: parsed.skipped },
    ]);
    setFilter("all");
    setQuery("");
    adoptLibrary(SAMPLE_LIBRARY, "Sample Emby library");
  }

  async function downloadArchive() {
    if (!result) return;
    const files = strmFileMap(result.files);
    if (Object.keys(files).length === 0) return;
    clearStatus();
    setBusy("Packing .strm archive…");
    try {
      await wait(20);
      saveBlob(blobFromBytes(zipBytes(files), "application/zip"), `nimbovod-strm-${stamp()}.zip`);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(null);
    }
  }

  function downloadReport() {
    if (!result) return;
    saveBlob(new Blob([reportText(result, sourceLabel)], { type: "text/plain;charset=utf-8" }), `nimbovod-report-${stamp()}.txt`);
  }

  function downloadCleaner() {
    saveBlob(new Blob([cleanerSource], { type: "text/x-python;charset=utf-8" }), "nimbovod-cleaner.py");
  }

  async function downloadWindows() {
    clearStatus();
    setBusy("Preparing the Windows program…");
    try {
      const response = await fetch(windowsCleanerUrl());
      if (!response.ok) throw new Error("The Windows program is not available from this page.");
      saveBlob(await response.blob(), "nimbovod-cleaner.exe");
      setNote("Downloaded nimbovod-cleaner.exe. Put it in the same folder as nimbovod.config.json on the Emby computer.");
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(null);
    }
  }

  function downloadConfig() {
    const config = {
      m3u: playlistUrls.length === 1 ? playlistUrls[0] : playlistUrls,
      out: savePath,
      emby: embyUrl,
      apiKey: includeKey ? apiKey : "",
      userId,
      layout,
      stripQuality,
      useYears,
      matchFilenames,
    };
    saveBlob(new Blob([JSON.stringify(config, null, 2)], { type: "application/json" }), "nimbovod.config.json");
  }

  async function saveToFolder(forcePick = false) {
    if (!result) return;
    clearStatus();
    try {
      let handle = forcePick ? null : folderHandle.current;
      if (!handle) {
        handle = await pickDirectory();
        folderHandle.current = handle;
        setFolderName(handle.name);
      }
      setBusy("Saving .strm files…");
      const summary = await writeStrmTree(handle, result.files, (done, total) => {
        setBusy(`Saving ${formatCount(done)} of ${formatCount(total)}…`);
      });
      const removed =
        summary.removed > 0 ? ` Removed ${formatCount(summary.removed)} already in that folder.` : "";
      setNote(`Saved ${formatCount(summary.written)} .strm files to ${handle.name}.${removed}`);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setError(messageOf(caught));
    } finally {
      setBusy(null);
    }
  }

  const usingSample = sources.some((source) => source.kind === "sample") && libraryLabel.startsWith("Sample");
  const saving = busy?.startsWith("Saving") === true;

  return (
    <main className="min-h-screen bg-canvas text-fg">
      <div className="h-1 bg-accent" />
      <div className="mx-auto grid max-w-6xl gap-6 px-4 py-6 md:px-6 md:py-8">
        <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="grid size-11 shrink-0 place-items-center rounded-lg bg-accent text-ink">
              <Filter className="size-5" aria-hidden="true" />
            </span>
            <div>
              <p className="font-mono text-xs tracking-widest text-accent uppercase">Emby strm</p>
              <h1 className="text-3xl font-semibold tracking-tight">NimboVoD</h1>
              <p className="max-w-xl text-sm text-muted">
                Turn one or more M3U links into a folder of .strm files, then leave out anything your Emby
                library already has.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={loadSample}
            className="inline-flex min-h-11 items-center justify-center rounded-md border border-line bg-raised px-3 text-sm font-medium hover:border-accent"
          >
            Preview with sample data
          </button>
        </header>

        {error ? (
          <p role="alert" className="flex gap-2 rounded-md border border-line bg-raised px-3 py-3 text-sm">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
            <span className="min-w-0 break-words">{error}</span>
          </p>
        ) : null}
        {note ? <p className="rounded-md border border-line bg-raised px-3 py-3 text-sm">{note}</p> : null}

        <div className="grid min-w-0 items-start gap-6 lg:grid-cols-3">
          <div className="grid min-w-0 gap-6">
            <Card
              step="01"
              title="Playlists"
              hot={hot === "playlist"}
              onDragOver={(event) => {
                event.preventDefault();
                setHot("playlist");
              }}
              onDragLeave={() => setHot((current) => (current === "playlist" ? null : current))}
              onDrop={(event) => {
                event.preventDefault();
                setHot(null);
                void onPlaylistFiles(event.dataTransfer.files);
              }}
            >
              <form className="grid gap-3" onSubmit={(event) => void loadPlaylistUrls(event)}>
                <div className="grid gap-2">
                  <p className="text-sm font-medium">M3U links</p>
                  {links.map((link, index) => (
                    <div key={link.id} className="flex gap-2">
                      <input
                        className={inputClass}
                        value={link.url}
                        aria-label={`M3U link ${index + 1}`}
                        onChange={(event) =>
                          setLinks((current) =>
                            current.map((item) => (item.id === link.id ? { ...item, url: event.target.value } : item)),
                          )
                        }
                        placeholder="https://example.com/playlist.m3u"
                        inputMode="url"
                        autoComplete="off"
                        spellCheck={false}
                      />
                      {links.length > 1 ? (
                        <button
                          type="button"
                          aria-label={`Remove link ${index + 1}`}
                          className="grid size-11 shrink-0 place-items-center rounded-md border border-line text-muted hover:text-fg"
                          onClick={() => setLinks((current) => current.filter((item) => item.id !== link.id))}
                        >
                          <X className="size-4" />
                        </button>
                      ) : null}
                    </div>
                  ))}
                  <button
                    type="button"
                    className="inline-flex min-h-11 items-center gap-2 text-sm text-accent"
                    onClick={() => setLinks((current) => [...current, { id: newId(), url: "" }])}
                  >
                    <Plus className="size-4" />
                    Add another link
                  </button>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Button type="submit" variant="primary" disabled={busy != null || playlistUrls.length === 0}>
                    {busy?.startsWith("Downloading playlist") ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Link2 className="size-4" />
                    )}
                    {playlistUrls.length > 1 ? `Load ${playlistUrls.length} links` : "Load links"}
                  </Button>
                  <Button type="button" onClick={() => playlistInput.current?.click()} disabled={busy != null}>
                    <Upload className="size-4" />
                    Upload files
                  </Button>
                  <input
                    ref={playlistInput}
                    className="sr-only"
                    type="file"
                    multiple
                    accept=".m3u,.m3u8,.txt,audio/x-mpegurl,application/vnd.apple.mpegurl"
                    onChange={(event) => {
                      void onPlaylistFiles(event.target.files ?? undefined);
                      event.target.value = "";
                    }}
                  />
                </div>
              </form>
              {sources.length > 0 ? (
                <ul className="grid gap-2">
                  {sources.map((source) => (
                    <li
                      key={source.id}
                      className="flex min-h-11 items-center gap-2 rounded-md border border-line bg-canvas px-3"
                    >
                      <span className="min-w-0 flex-1 truncate text-sm">{source.label}</span>
                      <span className="shrink-0 font-mono text-xs text-muted">{formatCount(source.entries.length)}</span>
                      <button
                        type="button"
                        aria-label={`Remove ${source.label}`}
                        className="grid size-11 shrink-0 place-items-center text-muted hover:text-fg"
                        onClick={() => setSources((current) => current.filter((item) => item.id !== source.id))}
                      >
                        <X className="size-4" />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
              <div className="grid gap-2">
                <p className="text-sm font-medium">Folder layout</p>
                <div className="grid grid-cols-3 gap-2" role="group" aria-label="Folder layout">
                  {(
                    [
                      ["group", "By group"],
                      ["flat", "Flat"],
                      ["library", "Movies & TV"],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={layout === value}
                      onClick={() => setLayout(value)}
                      className={`min-h-11 rounded-md border px-2 text-sm ${
                        layout === value
                          ? "border-accent bg-accent text-ink"
                          : "border-line bg-canvas text-fg hover:border-accent"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-muted">
                  {layout === "group"
                    ? "One folder per group-title. Same stream URL in two playlists is kept once."
                    : layout === "flat"
                      ? "Every .strm file in a single folder."
                      : "Movies in Movies/, episodes under TV/Show/Season 01/."}
                </p>
              </div>
              <CheckRow checked={stripQuality} onChange={setStripQuality} label="Strip quality tags from file names" />
            </Card>

            <Card
              step="02"
              title="Emby library"
              hot={hot === "library"}
              onDragOver={(event) => {
                event.preventDefault();
                setHot("library");
              }}
              onDragLeave={() => setHot((current) => (current === "library" ? null : current))}
              onDrop={(event) => {
                event.preventDefault();
                setHot(null);
                const dropped = [...event.dataTransfer.files];
                const playlist = dropped.find((file) => /\.m3u8?$/i.test(file.name));
                if (playlist) void onPlaylistFiles(dropped.filter((file) => /\.m3u8?$/i.test(file.name)));
                else void onLibraryFile(dropped[0]);
              }}
            >
              <Field label="Server address" hint="A public address works from this page. A 192.168 address does not.">
                <input
                  className={inputClass}
                  value={embyUrl}
                  onChange={(event) => setEmbyUrl(event.target.value)}
                  placeholder="https://emby.example.com"
                  inputMode="url"
                  autoComplete="off"
                  spellCheck={false}
                />
              </Field>
              <Field label="API key" hint="Dashboard, then API keys. It stays in this page.">
                <input
                  className={inputClass}
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder="Emby API key"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                />
              </Field>
              <Field label="User id" hint="Optional. Leave blank to read the whole server.">
                <input
                  className={inputClass}
                  value={userId}
                  onChange={(event) => setUserId(event.target.value)}
                  placeholder="Optional"
                  autoComplete="off"
                  spellCheck={false}
                />
              </Field>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Button type="button" variant="primary" onClick={() => void pullEmby()} disabled={busy != null}>
                  {busy === "Reading Emby library…" ? <Loader2 className="size-4 animate-spin" /> : <Library className="size-4" />}
                  Pull library
                </Button>
                <Button type="button" onClick={() => libraryInput.current?.click()} disabled={busy != null}>
                  <Upload className="size-4" />
                  Upload export
                </Button>
                <input
                  ref={libraryInput}
                  className="sr-only"
                  type="file"
                  accept=".json,.txt,application/json,text/plain"
                  onChange={(event) => {
                    void onLibraryFile(event.target.files?.[0]);
                    event.target.value = "";
                  }}
                />
              </div>
              <Field label="Or paste titles" hint="One per line. Episodes like Show S01E02 are matched as episodes.">
                <textarea
                  className={`${inputClass} min-h-24 py-2`}
                  value={pasted}
                  onChange={(event) => setPasted(event.target.value)}
                  placeholder={"The Matrix (1999)\nThe Office S01E01"}
                />
              </Field>
              <Button type="button" onClick={usePastedTitles} disabled={pasted.trim() === ""}>
                Match pasted titles
              </Button>
              <CheckRow checked={useYears} onChange={setUseYears} label="Don't treat a different year as the same movie" />
              <CheckRow checked={matchFilenames} onChange={setMatchFilenames} label="Also match Emby file names" />
              {library ? (
                <p className="flex items-center justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate text-muted">{libraryLabel}</span>
                  <button
                    type="button"
                    className="shrink-0 text-accent"
                    onClick={() => {
                      setLibrary(null);
                      setLibraryLabel("");
                    }}
                  >
                    Clear
                  </button>
                </p>
              ) : null}
              <div className="grid gap-3 border-t border-line pt-4">
                <h3 className="text-sm font-semibold">On the Emby computer</h3>
                <p className="text-sm text-muted">
                  Download the Windows program and the config into the same folder, then double-click the program.
                  It can reach a 192.168 Emby server. The first run only prints the plan. Windows may say it protected
                  your PC because the file is not signed. Choose More info, then Run anyway.
                </p>
                <pre className="overflow-x-auto rounded-md border border-line bg-canvas p-3 font-mono text-xs text-muted">
                  {`nimbovod-cleaner.exe --apply\n# writes .strm files to ${savePath}`}
                </pre>
                <CheckRow checked={includeKey} onChange={setIncludeKey} label="Put the API key in the downloaded config" />
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Button type="button" variant="primary" onClick={() => void downloadWindows()} disabled={busy != null}>
                    <Download className="size-4" />
                    Windows program
                  </Button>
                  <Button type="button" onClick={downloadConfig} disabled={busy != null}>
                    <Download className="size-4" />
                    Config
                  </Button>
                  <Button type="button" onClick={downloadCleaner} disabled={busy != null}>
                    <Download className="size-4" />
                    Python script
                  </Button>
                </div>
              </div>
            </Card>
          </div>

          <section className="grid min-w-0 gap-4 rounded-lg border border-line bg-surface p-4 lg:col-span-2">
            <header>
              <p className="font-mono text-xs tracking-widest text-accent uppercase">03 Save</p>
              <h2 className="text-base font-semibold">Where the .strm files go</h2>
              {sourceLabel ? (
                <p className="text-sm text-muted">
                  {sourceLabel}
                  {skipped > 0 ? ` · ${formatCount(skipped)} skipped lines` : ""}
                  {libraryLabel ? ` · ${libraryLabel}` : ""}
                </p>
              ) : (
                <p className="text-sm text-muted">Load playlists to build the .strm list.</p>
              )}
            </header>

            <div className="grid gap-3">
              <label className="grid gap-1.5 text-sm">
                <span className="font-medium">Save folder</span>
                <input
                  className={inputClass}
                  value={outDir}
                  aria-label="Save folder"
                  onChange={(event) => setOutDir(event.target.value)}
                  placeholder="D:\Media\strm"
                  spellCheck={false}
                  autoComplete="off"
                />
                <span className="text-xs text-muted">
                  Used by the cleaner on your computer. Choose folder writes the new .strm files in this browser.
                </span>
              </label>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Button
                  type="button"
                  variant="primary"
                  disabled={!result || busy != null}
                  onClick={() => void saveToFolder(false)}
                >
                  {saving ? <Loader2 className="size-4 animate-spin" /> : <FolderOpen className="size-4" />}
                  {folderName ? `Save to ${folderName}` : "Choose folder and save"}
                </Button>
                <Button
                  type="button"
                  disabled={!result || result.kept === 0 || busy != null}
                  onClick={() => void downloadArchive()}
                >
                  {busy === "Packing .strm archive…" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <FileArchive className="size-4" />
                  )}
                  {result?.compared ? "Download new .strm files" : "Download zip"}
                </Button>
                <Button type="button" disabled={!result} onClick={downloadReport}>
                  <Download className="size-4" />
                  Report
                </Button>
              </div>
              {folderName ? (
                <button type="button" className="min-h-11 text-left text-sm text-accent" onClick={() => void saveToFolder(true)}>
                  Choose a different folder
                </button>
              ) : null}
            </div>

            <div className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line bg-line">
              <Stat label="Streams" value={result ? formatCount(result.files.length) : "—"} />
              <Stat label={result?.compared ? "Left out" : "Not checked"} value={result?.compared ? formatCount(result.dropped) : "—"} />
              <Stat label="New .strm" value={result ? formatCount(result.kept) : "—"} />
            </div>

            {!result ? (
              <div className="grid gap-3 rounded-md border border-dashed border-line px-4 py-6 text-sm text-muted">
                <p>1. Add every M3U link, or drop the files on the playlist card.</p>
                <p>2. Pull Emby, paste titles, or upload a library export.</p>
                <p>3. Set the save folder, then write the files there or download a zip.</p>
              </div>
            ) : (
              <>
                {!result.compared ? (
                  <p className="text-sm text-muted">
                    No library is loaded, so nothing has been removed. Every stream will be saved.
                  </p>
                ) : null}
                {usingSample ? <p className="text-sm text-muted">{SAMPLE_ENTRIES_NOTE}</p> : null}
                <div className="flex flex-col gap-2 sm:flex-row">
                  <div className="grid grid-cols-3 gap-2 sm:w-72" role="group" aria-label="Result filter">
                    {(
                      [
                        ["all", "All"],
                        ["keep", "New"],
                        ["drop", "Left out"],
                      ] as const
                    ).map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={filter === value}
                        onClick={() => setFilter(value)}
                        className={`min-h-11 rounded-md border px-2 text-sm ${
                          filter === value ? "border-accent bg-accent text-ink" : "border-line bg-canvas text-fg"
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <input
                    className={inputClass}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Filter titles or folders"
                    aria-label="Filter results"
                  />
                </div>
                <ResultList files={visible} listKey={`${filter}:${query}:${layout}:${result.kept}:${sources.length}`} />
              </>
            )}
          </section>
        </div>
        <p className="text-xs text-muted">
          Playlist links and the Emby key are sent only when you load them. The key is not saved in this
          browser. The API key is never written into the config file unless you check that option.
        </p>
      </div>
    </main>
  );
}

function Card({
  step,
  title,
  children,
  hot,
  onDragOver,
  onDragLeave,
  onDrop,
}: {
  step: string;
  title: string;
  children: ReactNode;
  hot: boolean;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
}) {
  return (
    <section
      className={`grid gap-4 rounded-lg border bg-surface p-4 ${hot ? "border-accent" : "border-line"}`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <header className="flex items-baseline gap-3">
        <span className="font-mono text-xs text-accent">{step}</span>
        <h2 className="text-base font-semibold">{title}</h2>
      </header>
      {children}
    </section>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {hint ? <span className="text-xs text-muted">{hint}</span> : null}
    </label>
  );
}

function Button({
  variant = "quiet",
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "quiet" }) {
  const tone =
    variant === "primary"
      ? "bg-accent text-ink hover:opacity-90"
      : "border border-line bg-raised text-fg hover:border-accent";
  return (
    <button
      {...props}
      className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${tone} ${className}`}
    >
      {children}
    </button>
  );
}

function CheckRow({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex min-h-11 items-center gap-3 text-sm">
      <input type="checkbox" className="size-4" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface px-3 py-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="font-mono text-xl tabular-nums sm:text-2xl">{value}</p>
    </div>
  );
}

function ResultList({ files, listKey }: { files: PlannedFile[]; listKey: string }) {
  const scroller = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(384);
  const rowHeight = 68;

  useEffect(() => {
    setScrollTop(0);
  }, [listKey]);

  useEffect(() => {
    const node = scroller.current;
    if (!node || files.length <= 40) return;
    const observer = new ResizeObserver(() => setHeight(node.clientHeight || 384));
    observer.observe(node);
    return () => observer.disconnect();
  }, [files.length]);

  if (files.length === 0) {
    return <p className="px-1 py-6 text-sm text-muted">Nothing matches this filter.</p>;
  }

  if (files.length <= 40) {
    return (
      <ul className="max-h-128 overflow-auto rounded-md border border-line" key={listKey}>
        {files.map((file) => (
          <li key={file.id}>
            <ResultRow file={file} />
          </li>
        ))}
      </ul>
    );
  }

  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 6);
  const count = Math.ceil(height / rowHeight) + 12;
  const slice = files.slice(start, start + count);

  return (
    <div
      key={listKey}
      ref={scroller}
      onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      className="h-96 overflow-auto rounded-md border border-line lg:h-128"
    >
      <ul className="relative" style={{ height: files.length * rowHeight }}>
        {slice.map((file, index) => (
          <li key={file.id} className="absolute inset-x-0" style={{ top: (start + index) * rowHeight }}>
            <ResultRow file={file} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function ResultRow({ file }: { file: PlannedFile }) {
  const dropped = file.disposition === "drop";
  return (
    <div
      className="flex min-h-16 items-center gap-3 border-b border-line px-3 py-2"
      title={`${file.relativePath}${file.reason ? `\n${file.reason}` : ""}`}
    >
      <span className={`w-16 shrink-0 font-mono text-xs tracking-wide uppercase ${dropped ? "text-muted" : "text-accent"}`}>
        {dropped ? "Skip" : "New"}
      </span>
      <div className="min-w-0 flex-1">
        <p className={`truncate text-sm ${dropped ? "text-muted" : "text-fg"}`}>{file.title}</p>
        <p className="truncate font-mono text-xs text-muted">{dropped ? file.reason : file.relativePath}</p>
      </div>
    </div>
  );
}
