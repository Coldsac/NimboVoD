export type StreamEntry = {
  id: string;
  title: string;
  group: string;
  url: string;
  tvgId?: string;
};

export type EmbyKind = "Movie" | "Series" | "Episode" | "Other";

export type EmbyItem = {
  name: string;
  year?: number;
  type: EmbyKind;
  path?: string;
  seriesName?: string;
  season?: number;
  episode?: number;
};

export type LayoutMode = "group" | "flat" | "library";

export type PlanOptions = {
  layout: LayoutMode;
  stripQuality: boolean;
  useYears: boolean;
  matchFilenames: boolean;
};

export type PlannedFile = {
  id: string;
  title: string;
  url: string;
  group: string;
  relativePath: string;
  disposition: "keep" | "drop";
  reason: string;
};

export type PlanResult = {
  files: PlannedFile[];
  compared: boolean;
  kept: number;
  dropped: number;
  duplicates: number;
};

export type ParseResult = {
  entries: StreamEntry[];
  skipped: number;
};

const QUALITY =
  /\b(?:2160p|1080p|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dv|dolby(?:\s*vision)?|web-?dl|web-?rip|bluray|blu-ray|bdrip|brrip|dvdrip|hdtv|hdrip|x264|x265|h\.?264|h\.?265|hevc|aac|dts(?:-hd)?|atmos|truehd|remux|proper|repack|extended|unrated|amzn|nf|hmax)\b/gi;

const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

export function stripQualityTokens(input: string): string {
  return input
    .replace(QUALITY, " ")
    .replace(/[._]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function normalizeTitle(input: string): { key: string; year?: number } {
  let s = input.normalize("NFKC");
  const years = [...s.matchAll(/\b(?:19|20)\d{2}\b/g)].map((match) => Number(match[0]));
  const year = years.length > 0 ? years[years.length - 1] : undefined;
  s = s.replace(/\[[^\]]*\]/g, " ");
  s = s.replace(/\([^)]*\)/g, " ");
  s = s.replace(QUALITY, " ");
  s = s.toLowerCase();
  s = s.replace(/&/g, " and ");
  s = s.replace(/['’`]/g, "");
  s = s.replace(/[^a-z0-9]+/g, " ");
  s = s.replace(/\b(?:19|20)\d{2}\b/g, " ");
  s = s.replace(/^(?:the|a|an)\s+/, "");
  s = s.replace(/\s+/g, " ").trim();
  if (!s && year) s = String(year);
  return { key: s, year };
}

export function parseEpisode(
  title: string,
): { show: string; season: number; episode: number; rest: string } | null {
  const cleaned = stripQualityTokens(title);
  const match = cleaned.match(
    /^(.*?)\s+(?:s(\d{1,2})\s*e(\d{1,3})|(\d{1,2})x(\d{2,3}))\b\s*(.*)$/i,
  );
  if (!match) return null;
  const show = (match[1] ?? "").trim();
  const season = Number(match[2] ?? match[4]);
  const episode = Number(match[3] ?? match[5]);
  const rest = (match[6] ?? "").replace(/^[-–—:\s]+/, "").trim();
  if (!show || !Number.isFinite(season) || !Number.isFinite(episode)) return null;
  return { show, season, episode, rest };
}

function parseAttributes(meta: string): Record<string, string> {
  const attrs: Record<string, string> = {};
  const patterns = [/([A-Za-z0-9-]+)\s*=\s*"([^"]*)"/g, /([A-Za-z0-9-]+)\s*=\s*'([^']*)'/g];
  for (const pattern of patterns) {
    for (const match of meta.matchAll(pattern)) {
      const key = match[1]?.toLowerCase();
      if (key && attrs[key] === undefined) attrs[key] = match[2] ?? "";
    }
  }
  return attrs;
}

function splitExtinf(line: string): { meta: string; title: string } {
  const body = line.replace(/^#EXTINF:/i, "");
  let quoted = false;
  for (let i = 0; i < body.length; i += 1) {
    const char = body[i];
    if (char === '"') quoted = !quoted;
    else if (char === "," && !quoted) {
      return { meta: body.slice(0, i), title: body.slice(i + 1).trim() };
    }
  }
  return { meta: body, title: "" };
}

export function parseM3u(source: string): ParseResult {
  const text = source.replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/);
  const entries: StreamEntry[] = [];
  let pending: { title: string; group: string; tvgId?: string } | null = null;
  let extGroup = "";
  let skipped = 0;

  const pushUrl = (url: string) => {
    if (!pending) {
      skipped += 1;
      return;
    }
    entries.push({
      id: `e-${entries.length}`,
      title: pending.title || "Untitled",
      group: pending.group || "Ungrouped",
      url,
      tvgId: pending.tvgId,
    });
    pending = null;
  };

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.startsWith("#")) {
      if (/^#EXTGRP:/i.test(line)) {
        extGroup = line.replace(/^#EXTGRP:/i, "").trim();
        continue;
      }
      if (/^#EXTINF:/i.test(line)) {
        const { meta, title } = splitExtinf(line);
        const attrs = parseAttributes(meta);
        const name = title || attrs["tvg-name"] || "Untitled";
        const group = attrs["group-title"] || extGroup || "Ungrouped";
        pending = {
          title: name.trim() || "Untitled",
          group: group.trim() || "Ungrouped",
          tvgId: attrs["tvg-id"] || undefined,
        };
        continue;
      }
      continue;
    }
    if (pending) pushUrl(line);
    else skipped += 1;
  }

  return { entries, skipped };
}

export function looksLikePlaylist(text: string): boolean {
  const head = text.slice(0, 4000);
  return /#EXTM3U/i.test(head) || /#EXTINF:/i.test(head);
}

function safeSegment(name: string): string {
  const cleaned = name
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[. ]+$/g, "")
    .slice(0, 150);
  const segment = cleaned || "untitled";
  return RESERVED.test(segment) ? `${segment}_` : segment;
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function displayTitle(title: string, stripQuality: boolean): string {
  const next = stripQuality ? stripQualityTokens(title) : title;
  return next.replace(/\s+/g, " ").trim() || title.trim() || "Untitled";
}

function relativePath(entry: StreamEntry, options: PlanOptions): string {
  const title = displayTitle(entry.title, options.stripQuality);
  if (options.layout === "flat") return `${safeSegment(title)}.strm`;

  if (options.layout === "library") {
    const episode = parseEpisode(title);
    if (episode) {
      const show = safeSegment(episode.show);
      const season = `Season ${pad2(episode.season)}`;
      const label = episode.rest
        ? `${episode.show} - S${pad2(episode.season)}E${pad2(episode.episode)} - ${episode.rest}`
        : `${episode.show} - S${pad2(episode.season)}E${pad2(episode.episode)}`;
      return `TV/${show}/${season}/${safeSegment(label)}.strm`;
    }
    return `Movies/${safeSegment(title)}.strm`;
  }

  const group = safeSegment(entry.group || "Ungrouped");
  return `${group}/${safeSegment(title)}.strm`;
}

function uniquePath(path: string, used: Set<string>): string {
  const key = path.toLowerCase();
  if (!used.has(key)) {
    used.add(key);
    return path;
  }
  const dot = path.lastIndexOf(".");
  const stem = dot === -1 ? path : path.slice(0, dot);
  const ext = dot === -1 ? "" : path.slice(dot);
  let n = 2;
  let next = "";
  do {
    next = `${stem} (${n})${ext}`;
    n += 1;
  } while (used.has(next.toLowerCase()));
  used.add(next.toLowerCase());
  return next;
}

function yearsCompatible(left?: number, right?: number, useYears?: boolean): boolean {
  if (!useYears) return true;
  if (left == null || right == null) return true;
  return left === right;
}

function basenameOf(path: string): string {
  const base = path.split(/[/\\]/).pop() ?? path;
  return base.replace(/\.[a-z0-9]{2,5}$/i, "");
}

type Indexed = { label: string; year?: number };

function remember(map: Map<string, Indexed[]>, key: string, item: Indexed) {
  if (!key) return;
  const list = map.get(key);
  if (list) list.push(item);
  else map.set(key, [item]);
}

function findMatch(map: Map<string, Indexed[]>, key: string, year: number | undefined, useYears: boolean) {
  const list = map.get(key);
  if (!list) return undefined;
  return list.find((item) => yearsCompatible(year, item.year, useYears));
}

function kindLabel(type: EmbyKind): string {
  if (type === "Movie") return "Movie";
  if (type === "Series") return "Series";
  if (type === "Episode") return "Episode";
  return "Item";
}

export function planLibrary(
  entries: StreamEntry[],
  library: EmbyItem[] | null,
  options: PlanOptions,
): PlanResult {
  const movies = new Map<string, Indexed[]>();
  const series = new Map<string, Indexed[]>();
  const episodes = new Map<string, Indexed[]>();
  const filenames = new Map<string, Indexed[]>();

  if (library) {
    for (const item of library) {
      const named = normalizeTitle(item.name);
      const labelYear = item.year ?? named.year;
      const label = `${kindLabel(item.type)} “${item.name}”${labelYear ? ` (${labelYear})` : ""}`;
      const indexed = { label, year: item.year ?? named.year };
      if (item.type === "Movie") remember(movies, named.key, indexed);
      if (item.type === "Series") remember(series, named.key, indexed);
      if (item.type === "Episode") {
        const show = normalizeTitle(item.seriesName || "");
        if (show.key && item.season != null && item.episode != null) {
          remember(episodes, `${show.key}|${item.season}|${item.episode}`, {
            label,
            year: item.year ?? show.year,
          });
        }
      }
      if (options.matchFilenames && item.path) {
        const file = normalizeTitle(basenameOf(item.path));
        remember(filenames, file.key, { label, year: item.year ?? file.year });
      }
    }
  }

  const used = new Set<string>();
  const seenUrls = new Set<string>();
  const files: PlannedFile[] = [];
  let duplicates = 0;

  for (const entry of entries) {
    const path = uniquePath(relativePath(entry, options), used);
    const urlKey = entry.url.trim();
    if (seenUrls.has(urlKey)) {
      duplicates += 1;
      files.push({
        id: entry.id,
        title: entry.title,
        url: entry.url,
        group: entry.group,
        relativePath: path,
        disposition: "drop",
        reason: "Duplicate stream URL in the playlist",
      });
      continue;
    }
    seenUrls.add(urlKey);

    if (!library) {
      files.push({
        id: entry.id,
        title: entry.title,
        url: entry.url,
        group: entry.group,
        relativePath: path,
        disposition: "keep",
        reason: "",
      });
      continue;
    }

    const cleaned = options.stripQuality ? stripQualityTokens(entry.title) : entry.title;
    const episode = parseEpisode(cleaned);
    let reason = "";

    if (episode) {
      const show = normalizeTitle(episode.show);
      const hit = episodes.get(`${show.key}|${episode.season}|${episode.episode}`)?.[0];
      if (hit) reason = `Already in Emby · ${hit.label}`;
    } else {
      const titled = normalizeTitle(cleaned);
      const movie = findMatch(movies, titled.key, titled.year, options.useYears);
      const show = findMatch(series, titled.key, titled.year, options.useYears);
      const file = options.matchFilenames
        ? findMatch(filenames, titled.key, titled.year, options.useYears)
        : undefined;
      const hit = movie ?? show ?? file;
      if (hit) reason = `Already in Emby · ${hit.label}`;
    }

    if (!reason && options.matchFilenames && episode) {
      const titled = normalizeTitle(cleaned);
      const file = findMatch(filenames, titled.key, titled.year, options.useYears);
      if (file) reason = `Already in Emby · ${file.label}`;
    }

    files.push({
      id: entry.id,
      title: entry.title,
      url: entry.url,
      group: entry.group,
      relativePath: path,
      disposition: reason ? "drop" : "keep",
      reason,
    });
  }

  const dropped = files.filter((file) => file.disposition === "drop").length;
  return {
    files,
    compared: library != null,
    kept: files.length - dropped,
    dropped,
    duplicates,
  };
}

export function strmFileMap(files: PlannedFile[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const file of files) {
    if (file.disposition !== "keep") continue;
    out[file.relativePath] = `${file.url.trim()}\n`;
  }
  return out;
}

export function reportText(result: PlanResult, sourceLabel: string): string {
  const lines = [
    "NimboVoD report",
    `Source: ${sourceLabel || "playlist"}`,
    `Compared with Emby: ${result.compared ? "yes" : "no"}`,
    `New .strm files: ${result.kept}`,
    `Left out: ${result.dropped}`,
    `Duplicate URLs: ${result.duplicates}`,
    "",
    "Left out",
  ];
  const dropped = result.files.filter((file) => file.disposition === "drop");
  if (dropped.length === 0) lines.push("(none)");
  for (const file of dropped) {
    lines.push(`${file.relativePath}\t${file.reason}\t${file.title}`);
  }
  lines.push("", "Kept");
  const kept = result.files.filter((file) => file.disposition === "keep");
  if (kept.length === 0) lines.push("(none)");
  for (const file of kept) lines.push(`${file.relativePath}\t${file.title}`);
  lines.push("");
  return lines.join("\n");
}

function asKind(value: unknown): EmbyKind {
  if (value === "Movie" || value === "Series" || value === "Episode") return value;
  return "Other";
}

function optionalNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return undefined;
}

export function mapEmbyRecord(raw: Record<string, unknown>): EmbyItem | null {
  const name = typeof raw.Name === "string" ? raw.Name : typeof raw.name === "string" ? raw.name : "";
  if (!name.trim()) return null;
  const type = asKind(raw.Type ?? raw.type);
  const year = optionalNumber(raw.ProductionYear ?? raw.year);
  const pathValue = raw.Path ?? raw.path;
  const series = raw.SeriesName ?? raw.seriesName;
  return {
    name: name.trim(),
    year,
    type,
    path: typeof pathValue === "string" && pathValue.trim() ? pathValue : undefined,
    seriesName: typeof series === "string" && series.trim() ? series.trim() : undefined,
    season: optionalNumber(raw.ParentIndexNumber ?? raw.season),
    episode: optionalNumber(raw.IndexNumber ?? raw.episode),
  };
}

export function parseLibraryText(text: string): EmbyItem[] {
  const trimmed = text.replace(/^\uFEFF/, "").trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      throw new Error("That file is not valid JSON.");
    }
    const records = extractRecords(parsed);
    const items = records.map(mapEmbyRecord).filter((item): item is EmbyItem => item != null);
    if (items.length === 0) throw new Error("No library titles were found in that JSON.");
    return items;
  }
  const items: EmbyItem[] = [];
  for (const line of trimmed.split(/\r?\n/)) {
    const name = line.trim();
    if (!name || name.startsWith("#")) continue;
    const episode = parseEpisode(name);
    if (episode) {
      items.push({
        name,
        year: normalizeTitle(episode.show).year,
        type: "Episode",
        seriesName: episode.show,
        season: episode.season,
        episode: episode.episode,
      });
      continue;
    }
    items.push({ name, year: normalizeTitle(name).year, type: "Movie" });
  }
  if (items.length === 0) throw new Error("No titles were found. Put one title on each line.");
  return items;
}

function extractRecords(parsed: unknown): Record<string, unknown>[] {
  if (Array.isArray(parsed)) {
    return parsed.map((entry) => {
      if (typeof entry === "string") return { name: entry, type: "Movie" };
      if (entry && typeof entry === "object") return entry as Record<string, unknown>;
      return {};
    });
  }
  if (!parsed || typeof parsed !== "object") return [];
  const record = parsed as Record<string, unknown>;
  const nested = record.Items ?? record.items ?? record.results;
  if (Array.isArray(nested)) return extractRecords(nested);
  if (typeof record.name === "string" || typeof record.Name === "string") return [record];
  return [];
}

export function isPrivateAddress(address: string): boolean {
  const raw = address.toLowerCase().replace(/^\[|\]$/g, "");
  if (raw === "::1" || raw === "::" || raw === "0.0.0.0") return true;
  const mapped = raw.startsWith("::ffff:") ? raw.slice(7) : raw;
  if (mapped.includes(":")) {
    return mapped.startsWith("fe80:") || mapped.startsWith("fc") || mapped.startsWith("fd") || mapped === "::1";
  }
  const parts = mapped.split(".").map((part) => Number(part));
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return false;
  }
  const a = parts[0] ?? 0;
  const b = parts[1] ?? 0;
  if (a === 10 || a === 127 || a === 0) return true;
  if (a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  if (a === 100 && b >= 64 && b <= 127) return true;
  return false;
}
