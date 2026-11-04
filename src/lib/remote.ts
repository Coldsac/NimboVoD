import { createServerFn } from "@tanstack/react-start";
import { isPrivateAddress, mapEmbyRecord, type EmbyItem } from "@/lib/nimbovod";

const PLAYLIST_LIMIT = 12 * 1024 * 1024;
const EMBY_ITEM_LIMIT = 30_000;
const PAGE_SIZE = 400;

const BROWSER_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

const PRIVATE_MESSAGE =
  "That address is on a private network, so this site can't reach it. Download the file and drop it here, or use the local cleaner on the computer that can see Emby.";

function cleanError(error: unknown, fallback: string): Error {
  if (error instanceof Error && error.name === "TimeoutError") return new Error("The request timed out.");
  if (error instanceof Error && error.message) return new Error(error.message);
  return new Error(fallback);
}

async function assertSafeUrl(raw: string): Promise<string> {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("Enter a full http or https URL.");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Only http and https URLs are allowed.");
  }
  const host = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (
    host === "localhost" ||
    host.endsWith(".local") ||
    host.endsWith(".internal") ||
    host === "metadata.google.internal"
  ) {
    throw new Error(PRIVATE_MESSAGE);
  }
  const { isIP } = await import("node:net");
  if (isIP(host)) {
    if (isPrivateAddress(host)) throw new Error(PRIVATE_MESSAGE);
    return url.toString();
  }
  const { lookup } = await import("node:dns/promises");
  let records: { address: string }[];
  try {
    records = await lookup(host, { all: true });
  } catch {
    throw new Error("That hostname could not be resolved.");
  }
  if (records.length === 0 || records.some((record) => isPrivateAddress(record.address))) {
    throw new Error(PRIVATE_MESSAGE);
  }
  return url.toString();
}

async function readCapped(response: Response, limit: number, noun: string): Promise<string> {
  const declared = Number(response.headers.get("content-length") ?? "0");
  if (declared > limit) {
    throw new Error(`That ${noun} is too large to pull through this site. Save it and drop the file here.`);
  }
  const reader = response.body?.getReader();
  if (!reader) return "";
  const chunks: Uint8Array[] = [];
  let received = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    received += value.byteLength;
    if (received > limit) {
      await reader.cancel();
      throw new Error(`That ${noun} is too large to pull through this site. Save it and drop the file here.`);
    }
    chunks.push(value);
  }
  const merged = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8").decode(merged);
}

async function fetchChecked(raw: string, headers: Record<string, string>, limit: number, noun: string) {
  let current = raw;
  for (let hop = 0; hop < 5; hop += 1) {
    const safe = await assertSafeUrl(current);
    const response = await fetch(safe, {
      redirect: "manual",
      headers,
      signal: AbortSignal.timeout(25_000),
    });
    if (response.status >= 300 && response.status < 400) {
      const location = response.headers.get("location");
      if (!location) throw new Error("The redirect had no destination.");
      current = new URL(location, safe).toString();
      continue;
    }
    if (!response.ok) {
      throw new Error(`The server responded with ${response.status}.`);
    }
    const text = await readCapped(response, limit, noun);
    return text;
  }
  throw new Error("Too many redirects.");
}

function embyOrigin(raw: string): string {
  const url = new URL(raw);
  let path = url.pathname.replace(/\/+$/, "");
  path = path.replace(/\/web(\/index\.html?)?$/i, "");
  path = path.replace(/\/emby$/i, "");
  return `${url.origin}${path}`;
}

async function pullEmbyPage(endpoint: string, apiKey: string, start: number) {
  const url = new URL(endpoint);
  url.searchParams.set("Recursive", "true");
  url.searchParams.set("IncludeItemTypes", "Movie,Series,Episode");
  url.searchParams.set("Fields", "Path,ProductionYear");
  url.searchParams.set("StartIndex", String(start));
  url.searchParams.set("Limit", String(PAGE_SIZE));
  url.searchParams.set("EnableImages", "false");
  url.searchParams.set("EnableUserData", "false");
  const response = await fetch(url, {
    headers: {
      "X-Emby-Token": apiKey,
      Accept: "application/json",
    },
    signal: AbortSignal.timeout(25_000),
    redirect: "manual",
  });
  if (response.status >= 300 && response.status < 400) {
    throw new Error("Emby redirected. Use the server root, not the web page address.");
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error("Emby refused that API key.");
  }
  if (!response.ok) {
    throw new Error(`Emby responded with ${response.status}.`);
  }
  return (await response.json()) as { Items?: unknown[]; TotalRecordCount?: number };
}

export const fetchPlaylist = createServerFn({ method: "POST" })
  .validator((input: { url: string }) => {
    if (!input || typeof input.url !== "string" || !input.url.trim()) {
      throw new Error("Enter a playlist URL.");
    }
    return { url: input.url.trim() };
  })
  .handler(async ({ data }) => {
    try {
      const text = await fetchChecked(
        data.url,
        { Accept: "application/vnd.apple.mpegurl, audio/x-mpegurl, */*", "User-Agent": BROWSER_UA },
        PLAYLIST_LIMIT,
        "playlist",
      );
      if (!text.trim()) throw new Error("The playlist was empty.");
      return { text };
    } catch (error) {
      throw cleanError(error, "The playlist could not be downloaded.");
    }
  });

export const fetchEmbyLibrary = createServerFn({ method: "POST" })
  .validator((input: { baseUrl: string; apiKey: string; userId?: string }) => {
    if (!input || typeof input.baseUrl !== "string" || !input.baseUrl.trim()) {
      throw new Error("Enter your Emby server address.");
    }
    if (typeof input.apiKey !== "string" || input.apiKey.trim().length < 8) {
      throw new Error("Enter the Emby API key from the dashboard.");
    }
    const userId = typeof input.userId === "string" ? input.userId.trim() : "";
    return { baseUrl: input.baseUrl.trim(), apiKey: input.apiKey.trim(), userId };
  })
  .handler(async ({ data }) => {
    try {
      await assertSafeUrl(data.baseUrl);
      const origin = embyOrigin(data.baseUrl);
      const userPath = data.userId
        ? `/Users/${encodeURIComponent(data.userId)}/Items`
        : "/Items";
      const candidates = [`${origin}/emby${userPath}`, `${origin}${userPath}`];
      let endpoint = candidates[0] ?? "";
      let firstError: Error | null = null;
      for (const candidate of candidates) {
        try {
          await assertSafeUrl(candidate);
          const probe = await pullEmbyPage(candidate, data.apiKey, 0);
          if (!probe || !Array.isArray(probe.Items)) {
            firstError = new Error("Emby did not return a library list.");
            continue;
          }
          endpoint = candidate;
          const items: EmbyItem[] = [];
          let total = probe.TotalRecordCount ?? Number.POSITIVE_INFINITY;
          let start = 0;
          let page = probe;
          while (true) {
            const batch = page.Items ?? [];
            for (const entry of batch) {
              if (!entry || typeof entry !== "object") continue;
              const mapped = mapEmbyRecord(entry as Record<string, unknown>);
              if (mapped) items.push(mapped);
            }
            start += batch.length;
            const shortPage = batch.length < PAGE_SIZE;
            if (shortPage || start >= total || items.length >= EMBY_ITEM_LIMIT) break;
            page = await pullEmbyPage(endpoint, data.apiKey, start);
            if (typeof page.TotalRecordCount === "number") total = page.TotalRecordCount;
          }
          const reported = typeof probe.TotalRecordCount === "number" ? probe.TotalRecordCount : items.length;
          return {
            items: items.slice(0, EMBY_ITEM_LIMIT),
            total: Math.max(reported, items.length),
            truncated: items.length >= EMBY_ITEM_LIMIT && reported > items.length,
          };
        } catch (error) {
          firstError = cleanError(error, "Emby could not be reached.");
          if (/refused that API key|private network|timed out/i.test(firstError.message)) throw firstError;
        }
      }
      throw firstError ?? new Error("Emby could not be reached.");
    } catch (error) {
      throw cleanError(error, "Emby could not be reached.");
    }
  });
