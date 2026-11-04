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
