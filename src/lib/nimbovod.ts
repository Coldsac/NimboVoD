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
