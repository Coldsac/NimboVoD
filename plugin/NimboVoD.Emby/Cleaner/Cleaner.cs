using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace NimboVoD.Cleaner
{
    public sealed class PlaylistEntry
    {
        public string Title { get; set; }
        public string Group { get; set; }
        public string Url { get; set; }
    }

    public sealed class LibraryItem
    {
        public string Name { get; set; }
        public string Type { get; set; }
        public int? Year { get; set; }
        public string Path { get; set; }
        public string SeriesName { get; set; }
        public int? Season { get; set; }
        public int? Episode { get; set; }
    }

    public sealed class PlanOptions
    {
        public string Layout { get; set; } = "group";
        public bool StripQuality { get; set; } = true;
        public bool UseYears { get; set; } = true;
        public bool MatchFilenames { get; set; } = true;
        public bool DropDuplicateStrm { get; set; } = true;
        public string ExtraTags { get; set; } = "";
        public string OutputFolder { get; set; } = "";
    }

    public sealed class PlanRow
    {
        public string Title { get; set; }
        public string Url { get; set; }
        public string Path { get; set; }
        public string Group { get; set; }
        public string Disposition { get; set; }
        public string Reason { get; set; }
    }

    public sealed class WriteCounts
    {
        public int Added { get; set; }
        public int Updated { get; set; }
        public int Unchanged { get; set; }
        public int Removed { get; set; }
    }

    public static class Cleaner
    {
        public static readonly string[] QualityTags = new[]
        {
            "2160p",
            "1080p",
            "720p",
            "576p",
            "480p",
            "4k",
            "uhd",
            "hdr10",
            "hdr",
            "dv",
            "dolby vision",
            "web-dl",
            "webrip",
            "bluray",
            "blu-ray",
            "bdrip",
            "brrip",
            "dvdrip",
            "hdtv",
            "hdrip",
            "x264",
            "x265",
            "h264",
            "h265",
            "hevc",
            "aac",
            "5.1",
            "7.1",
            "dts-hd",
            "dts",
            "atmos",
            "truehd",
            "remux",
            "proper",
            "repack",
            "extended",
            "unrated",
            "amzn",
            "nf",
            "hmax",
            "[WEB]",
            "[x265]",
            "[x264]",
            "[TS]",
            "[10BIT]",
            "-LAMA",
            "-WORLD",
            "Megusta",
            "[ ]",
        };

        public static List<string> ExtraTagNames(string tags)
        {
            var names = new List<string>();
            if (string.IsNullOrWhiteSpace(tags))
            {
                return names;
            }
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var part in Regex.Split(tags, @"[\n,;]+"))
            {
                var tag = part.Trim();
                if (tag.Length >= 2 && seen.Add(tag))
                {
                    names.Add(tag);
                }
            }
            return names;
        }

        private static readonly Regex Quality = new Regex(
            @"\[\s*(?:web|x26[45]|ts|10\s*bit)\s*\]"
            + @"|-\s*(?:lama|world)\b"
            + @"|(?<![a-z0-9])(?:(?:dd|ddp|aac|ac-?3|eac-?3|dts)\s*)?(?:5|7)\s*[.\s]\s*1(?:\s*ch)?(?![a-z0-9])"
            + @"|(?<![a-z0-9])(?:dd|ddp|aac|ac-?3|eac-?3|dts)\s*2\s*[.\s]\s*0(?:\s*ch)?(?![a-z0-9])"
            + @"|\[\s*\]"
            + @"|\b(?:2160p|1080p|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dv|dolby(?:\s*vision)?|"
            + @"web-?dl|web-?rip|bluray|blu-ray|bdrip|brrip|dvdrip|hdtv|hdrip|x264|x265|"
            + @"h\.?264|h\.?265|hevc|aac|dts(?:-hd)?|atmos|truehd|remux|proper|repack|"
            + @"extended|unrated|amzn|nf|hmax|megusta)\b",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled,
            TimeSpan.FromSeconds(2));

        private static readonly Regex EmptyBrackets = new Regex(@"\[\s*\]", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex YearPattern = new Regex(@"\b(?:19|20)\d{2}\b", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex EpisodePattern = new Regex(
            @"^(.*?)\s+(?:s(\d{1,2})\s*e(\d{1,3})|(\d{1,2})x(\d{2,3}))\b\s*(.*)$",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled,
            TimeSpan.FromSeconds(2));
        private static readonly Regex AttributePattern = new Regex(
            @"([A-Za-z0-9-]+)\s*=\s*(?:""([^""]*)""|'([^']*)')",
            RegexOptions.Compiled,
            TimeSpan.FromSeconds(2));
        private static readonly Regex Reserved = new Regex(
            @"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled,
            TimeSpan.FromSeconds(2));
        private static readonly Regex NumberedFile = new Regex(
            @"^(?<stem>.+) \((?<number>\d+)\)(?<suffix>\.[^.]+)$",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.Compiled,
            TimeSpan.FromSeconds(2));
        private static readonly Regex BracketPattern = new Regex(@"\[[^\]]*\]", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex ParenPattern = new Regex(@"\([^)]*\)", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex NonWord = new Regex(@"[^a-z0-9]+", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex LeadingArticle = new Regex(@"^(?:the|a|an)\s+", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex Spaces = new Regex(@"\s+", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex RestPrefix = new Regex(@"^[-–—:\s]+", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex SeasonSuffix = new Regex(@"\s+season\s+\d{1,2}\s*$", RegexOptions.IgnoreCase | RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex InvalidName = new Regex(@"[<>:""/\\|?*\u0000-\u001f]", RegexOptions.Compiled, TimeSpan.FromSeconds(2));
        private static readonly Regex Extension = new Regex(@"\.[A-Za-z0-9]{2,5}$", RegexOptions.Compiled, TimeSpan.FromSeconds(2));

        public static string StripQuality(string value, string extraTags = null)
        {
            return StripQuality(value ?? "", CompileExtra(extraTags));
        }

        public static Tuple<string, int?> NormalizeTitle(string value, string extraTags = null)
        {
            return Normalize(value ?? "", CompileExtra(extraTags));
        }

        public static List<PlaylistEntry> ParseM3u(string source)
        {
            var entries = new List<PlaylistEntry>();
            PlaylistEntry pending = null;
            var extGroup = "";
            var text = (source ?? "").Replace("\ufeff", "");
            foreach (var raw in text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.None))
            {
                var line = raw.Trim();
                if (line.Length == 0)
                {
                    continue;
                }
                if (line.StartsWith("#", StringComparison.Ordinal))
                {
                    if (line.StartsWith("#EXTGRP:", StringComparison.OrdinalIgnoreCase))
                    {
                        extGroup = line.Substring(line.IndexOf(':') + 1).Trim();
                    }
                    else if (line.StartsWith("#EXTINF:", StringComparison.OrdinalIgnoreCase))
                    {
                        var body = Regex.Replace(line, @"^#EXTINF:", "", RegexOptions.IgnoreCase);
                        var quoted = false;
                        var comma = -1;
                        for (var index = 0; index < body.Length; index++)
                        {
                            var character = body[index];
                            if (character == '"')
                            {
                                quoted = !quoted;
                            }
                            else if (character == ',' && !quoted)
                            {
                                comma = index;
                                break;
                            }
                        }
                        var meta = comma < 0 ? body : body.Substring(0, comma);
                        var title = comma < 0 ? "" : body.Substring(comma + 1).Trim();
                        var attrs = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                        foreach (Match match in AttributePattern.Matches(meta))
                        {
                            var key = match.Groups[1].Value.ToLowerInvariant();
                            if (!attrs.ContainsKey(key))
                            {
                                var quotedValue = match.Groups[2].Success ? match.Groups[2].Value : match.Groups[3].Value;
                                attrs[key] = quotedValue ?? "";
                            }
                        }
                        string tvg;
                        if (string.IsNullOrEmpty(title))
                        {
                            attrs.TryGetValue("tvg-name", out tvg);
                            title = string.IsNullOrEmpty(tvg) ? "Untitled" : tvg;
                        }
                        string group;
                        if (!attrs.TryGetValue("group-title", out group) || string.IsNullOrEmpty(group))
                        {
                            group = string.IsNullOrEmpty(extGroup) ? "Ungrouped" : extGroup;
                        }
                        pending = new PlaylistEntry { Title = title, Group = group, Url = "" };
                    }
                    continue;
                }
                if (pending != null)
                {
                    pending.Url = line;
                    entries.Add(pending);
                    pending = null;
                }
            }
            return entries;
        }

        public static List<PlanRow> Plan(IReadOnlyList<PlaylistEntry> entries, IReadOnlyList<LibraryItem> library, PlanOptions options)
        {
            options = options ?? new PlanOptions();
            var extra = CompileExtra(options.ExtraTags);
            var indexed = library == null
                ? EmptyIndex()
                : IndexLibrary(library, options.MatchFilenames, extra);
            var used = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var planned = new List<PlanRow>();
            foreach (var entry in entries)
            {
                var natural = RelativePath(entry, options.Layout, options.StripQuality, extra);
                var path = options.DropDuplicateStrm ? natural : UniquePath(natural, used);
                var url = (entry.Url ?? "").Trim();
                var row = new PlanRow
                {
                    Title = entry.Title ?? "",
                    Url = url,
                    Path = path,
                    Group = entry.Group ?? "",
                    Disposition = "keep",
                    Reason = "",
                };
                if (seen.Contains(url))
                {
                    row.Disposition = "drop";
                    row.Reason = "Duplicate stream URL in the playlist";
                    planned.Add(row);
                    continue;
                }
                seen.Add(url);
                if (options.DropDuplicateStrm)
                {
                    if (!used.Add(natural))
                    {
                        row.Disposition = "drop";
                        row.Reason = "Duplicate .strm file";
                        planned.Add(row);
                        continue;
                    }
                }
                if (library != null)
                {
                    var cleaned = options.StripQuality ? StripQuality(entry.Title ?? "", extra) : (entry.Title ?? "");
                    var reason = MatchReason(cleaned, extra, options, indexed);
                    if (!string.IsNullOrEmpty(reason))
                    {
                        row.Disposition = "drop";
                        row.Reason = reason;
                    }
                }
                planned.Add(row);
            }
            if (options.DropDuplicateStrm && !string.IsNullOrWhiteSpace(options.OutputFolder))
            {
                planned.AddRange(DiskDuplicateRows(options.OutputFolder, planned));
            }
            return planned;
        }

        public static WriteCounts WritePlan(string folder, IReadOnlyList<PlanRow> planned, bool apply, Action<int, int> onProgress)
        {
            var counts = new WriteCounts();
            var kept = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var row in planned)
            {
                if (row.Disposition == "keep")
                {
                    kept.Add(Slash(row.Path));
                }
            }
            var total = planned.Count;
            for (var index = 0; index < planned.Count; index++)
            {
                var row = planned[index];
                var key = Slash(row.Path);
                if (row.Disposition != "keep" && kept.Contains(key))
                {
                    Report(onProgress, index + 1, total);
                    continue;
                }
                var target = Inside(folder, row.Path);
                if (row.Disposition == "keep")
                {
                    var wanted = (row.Url ?? "").Trim();
                    var current = StrmText(target);
                    if (current == null)
                    {
                        counts.Added++;
                        if (apply)
                        {
                            Directory.CreateDirectory(Path.GetDirectoryName(target));
                            File.WriteAllBytes(target, new UTF8Encoding(false).GetBytes(wanted + "\n"));
                        }
                    }
                    else if (current == wanted)
                    {
                        counts.Unchanged++;
                    }
                    else
                    {
                        counts.Updated++;
                        if (apply)
                        {
                            Directory.CreateDirectory(Path.GetDirectoryName(target));
                            File.WriteAllBytes(target, new UTF8Encoding(false).GetBytes(wanted + "\n"));
                        }
                    }
                }
                else if (File.Exists(target))
                {
                    counts.Removed++;
                    if (apply)
                    {
                        File.Delete(target);
                    }
                }
                Report(onProgress, index + 1, total);
            }
            return counts;
        }

        public static bool DirectoryReachable(string path, int timeoutMs = 2000)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return false;
            }
            try
            {
                var task = System.Threading.Tasks.Task.Run(() => Directory.Exists(path));
                return task.Wait(timeoutMs) && task.Result;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private static void Report(Action<int, int> onProgress, int done, int total)
        {
            if (onProgress != null)
            {
                onProgress(done, total);
            }
        }

        private struct Index
        {
            public Dictionary<string, List<Indexed>> Movies;
            public Dictionary<string, List<Indexed>> Series;
            public Dictionary<string, List<Indexed>> Episodes;
            public Dictionary<string, List<Indexed>> Filenames;
            public Dictionary<string, HashSet<int>> Seasons;
        }

        private struct Indexed
        {
            public string Label;
            public int? Year;
        }

        private static Index EmptyIndex()
        {
            return new Index
            {
                Movies = new Dictionary<string, List<Indexed>>(StringComparer.Ordinal),
                Series = new Dictionary<string, List<Indexed>>(StringComparer.Ordinal),
                Episodes = new Dictionary<string, List<Indexed>>(StringComparer.Ordinal),
                Filenames = new Dictionary<string, List<Indexed>>(StringComparer.Ordinal),
                Seasons = new Dictionary<string, HashSet<int>>(StringComparer.Ordinal),
            };
        }

        private static string MatchReason(string cleaned, Regex extra, PlanOptions options, Index indexed)
        {
            var episode = ParseEpisode(cleaned, extra);
            if (episode != null)
            {
                var showKey = OwnedShowKey(episode.Item1, indexed.Series, indexed.Seasons, extra);
                List<Indexed> hit;
                if (indexed.Episodes.TryGetValue(showKey + "|" + episode.Item2 + "|" + episode.Item3, out hit) && hit.Count > 0)
                {
                    return "Already in Emby · " + hit[0].Label;
                }
                HashSet<int> seasons;
                if (indexed.Seasons.TryGetValue(showKey, out seasons) && seasons.Contains(episode.Item2))
                {
                    List<Indexed> seriesHit;
                    var label = indexed.Series.TryGetValue(showKey, out seriesHit) && seriesHit.Count > 0
                        ? seriesHit[0].Label
                        : episode.Item1;
                    return "Already in Emby · Season " + episode.Item2 + " · " + label;
                }
                return "";
            }
            var named = Normalize(cleaned, extra);
            var movie = FindMatch(indexed.Movies, named.Item1, named.Item2, options.UseYears)
                ?? FindMatch(indexed.Series, named.Item1, named.Item2, options.UseYears)
                ?? (options.MatchFilenames ? FindMatch(indexed.Filenames, named.Item1, named.Item2, options.UseYears) : null);
            return movie == null ? "" : "Already in Emby · " + movie.Value.Label;
        }

        private static Index IndexLibrary(IReadOnlyList<LibraryItem> items, bool matchFilenames, Regex extra)
        {
            var index = EmptyIndex();
            foreach (var item in items)
            {
                if (IsStrm(item.Path))
                {
                    continue;
                }
                var name = (item.Name ?? "").Trim();
                if (name.Length == 0)
                {
                    continue;
                }
                var kind = string.IsNullOrEmpty(item.Type) ? "Other" : item.Type;
                var named = Normalize(name, extra);
                var year = item.Year ?? named.Item2;
                var label = kind + " \"" + name + "\"" + (year.HasValue ? " (" + year.Value + ")" : "");
                var stored = new Indexed { Label = label, Year = year };
                if (kind == "Movie")
                {
                    Remember(index.Movies, named.Item1, stored);
                }
                else if (kind == "Series")
                {
                    Remember(index.Series, named.Item1, stored);
                }
                else if (kind == "Episode")
                {
                    var show = Normalize(item.SeriesName ?? "", extra);
                    if (!string.IsNullOrEmpty(show.Item1) && item.Season.HasValue && item.Episode.HasValue)
                    {
                        Remember(index.Episodes, show.Item1 + "|" + item.Season.Value + "|" + item.Episode.Value, stored);
                        HashSet<int> seasons;
                        if (!index.Seasons.TryGetValue(show.Item1, out seasons))
                        {
                            seasons = new HashSet<int>();
                            index.Seasons[show.Item1] = seasons;
                        }
                        seasons.Add(item.Season.Value);
                    }
                }
                if (matchFilenames && !string.IsNullOrWhiteSpace(item.Path))
                {
                    var slash = Slash(item.Path);
                    var baseName = slash.Substring(slash.LastIndexOf('/') + 1);
                    baseName = Extension.Replace(baseName, "");
                    var file = Normalize(baseName, extra);
                    Remember(index.Filenames, file.Item1, new Indexed { Label = label, Year = year ?? file.Item2 });
                }
            }
            return index;
        }

        private static bool IsStrm(string path)
        {
            return !string.IsNullOrEmpty(path) && path.EndsWith(".strm", StringComparison.OrdinalIgnoreCase);
        }

        private static void Remember(Dictionary<string, List<Indexed>> index, string key, Indexed item)
        {
            if (string.IsNullOrEmpty(key))
            {
                return;
            }
            List<Indexed> list;
            if (!index.TryGetValue(key, out list))
            {
                list = new List<Indexed>();
                index[key] = list;
            }
            list.Add(item);
        }

        private static Indexed? FindMatch(Dictionary<string, List<Indexed>> index, string key, int? year, bool useYears)
        {
            List<Indexed> list;
            if (string.IsNullOrEmpty(key) || !index.TryGetValue(key, out list))
            {
                return null;
            }
            foreach (var item in list)
            {
                if (YearsOk(year, item.Year, useYears))
                {
                    return item;
                }
            }
            return null;
        }

        private static bool YearsOk(int? left, int? right, bool useYears)
        {
            if (!useYears || !left.HasValue || !right.HasValue)
            {
                return true;
            }
            return left.Value == right.Value;
        }

        private static string OwnedShowKey(string show, Dictionary<string, List<Indexed>> series, Dictionary<string, HashSet<int>> seasons, Regex extra)
        {
            var keys = ShowKeys(show, extra);
            foreach (var key in keys)
            {
                if (series.ContainsKey(key) || seasons.ContainsKey(key))
                {
                    return key;
                }
            }
            return keys.Count > 0 ? keys[0] : "";
        }

        private static List<string> ShowKeys(string show, Regex extra)
        {
            var keys = new List<string>();
            AddKey(keys, show, extra);
            var parts = Regex.Split(show ?? "", @"\s*[:|]\s*", RegexOptions.None, TimeSpan.FromSeconds(2));
            if (parts.Length > 1)
            {
                var head = parts[0].Trim();
                if (head.Length > 0 && head != show)
                {
                    AddKey(keys, head, extra);
                }
            }
            var trimmed = SeasonSuffix.Replace(show ?? "", "").Trim();
            if (trimmed.Length > 0 && trimmed != show)
            {
                AddKey(keys, trimmed, extra);
            }
            return keys;
        }

        private static void AddKey(List<string> keys, string value, Regex extra)
        {
            var key = Normalize(value, extra).Item1;
            if (!string.IsNullOrEmpty(key) && !keys.Contains(key))
            {
                keys.Add(key);
            }
        }

        private static Tuple<string, int, int, string> ParseEpisode(string title, Regex extra)
        {
            var match = EpisodePattern.Match(StripQuality(title ?? "", extra));
            if (!match.Success)
            {
                return null;
            }
            var show = (match.Groups[1].Value ?? "").Trim();
            if (show.Length == 0)
            {
                return null;
            }
            var seasonText = match.Groups[2].Success ? match.Groups[2].Value : match.Groups[4].Value;
            var episodeText = match.Groups[3].Success ? match.Groups[3].Value : match.Groups[5].Value;
            var rest = RestPrefix.Replace((match.Groups[6].Value ?? "").Trim(), "");
            return Tuple.Create(show, int.Parse(seasonText), int.Parse(episodeText), rest);
        }

        private static string RelativePath(PlaylistEntry entry, string layout, bool strip, Regex extra)
        {
            var raw = entry.Title ?? "";
            var title = strip ? StripQuality(raw, extra) : Spaces.Replace(raw, " ").Trim();
            if (string.IsNullOrEmpty(title))
            {
                title = string.IsNullOrEmpty(raw) ? "Untitled" : raw;
            }
            if (layout == "flat")
            {
                return SafeSegment(title) + ".strm";
            }
            if (layout == "library")
            {
                var episode = ParseEpisode(title, extra);
                if (episode != null)
                {
                    var show = episode.Item1;
                    var season = episode.Item2;
                    var number = episode.Item3;
                    var rest = episode.Item4;
                    var label = rest.Length > 0
                        ? show + " - S" + season.ToString("00") + "E" + number.ToString("00") + " - " + rest
                        : show + " - S" + season.ToString("00") + "E" + number.ToString("00");
                    return "TV/" + SafeSegment(show) + "/Season " + season.ToString("00") + "/" + SafeSegment(label) + ".strm";
                }
                return "Movies/" + SafeSegment(title) + ".strm";
            }
            var group = string.IsNullOrEmpty(entry.Group) ? "Ungrouped" : entry.Group;
            return SafeSegment(group) + "/" + SafeSegment(title) + ".strm";
        }

        private static string UniquePath(string path, HashSet<string> used)
        {
            if (used.Add(path))
            {
                return path;
            }
            var dot = path.LastIndexOf('.');
            var stem = dot < 0 ? path : path.Substring(0, dot);
            var ext = dot < 0 ? "" : path.Substring(dot);
            var number = 2;
            while (true)
            {
                var candidate = stem + " (" + number + ")" + ext;
                if (used.Add(candidate))
                {
                    return candidate;
                }
                number++;
            }
        }

        private static string SafeSegment(string name)
        {
            var cleaned = InvalidName.Replace(name ?? "", " ");
            cleaned = Spaces.Replace(cleaned, " ").Trim().TrimEnd('.', ' ');
            if (cleaned.Length > 150)
            {
                cleaned = cleaned.Substring(0, 150).TrimEnd('.', ' ');
            }
            if (cleaned.Length == 0)
            {
                cleaned = "untitled";
            }
            if (Reserved.IsMatch(cleaned))
            {
                cleaned += "_";
            }
            return cleaned;
        }

        private static List<PlanRow> DiskDuplicateRows(string folder, List<PlanRow> planned)
        {
            var found = new List<PlanRow>();
            if (!DirectoryReachable(folder))
            {
                return found;
            }
            var known = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var keptNames = new Dictionary<string, HashSet<string>>(StringComparer.OrdinalIgnoreCase);
            foreach (var row in planned)
            {
                known.Add(Slash(row.Path));
                if (row.Disposition != "keep")
                {
                    continue;
                }
                var relative = Slash(row.Path);
                var slash = relative.LastIndexOf('/');
                var parent = slash < 0 ? "." : relative.Substring(0, slash);
                var name = slash < 0 ? relative : relative.Substring(slash + 1);
                HashSet<string> names;
                if (!keptNames.TryGetValue(parent, out names))
                {
                    names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    keptNames[parent] = names;
                }
                names.Add(name);
            }
            foreach (var pair in keptNames)
            {
                var parent = pair.Key == "." ? folder : Path.Combine(folder, pair.Key.Replace('/', Path.DirectorySeparatorChar));
                string[] children;
                try
                {
                    if (!Directory.Exists(parent))
                    {
                        continue;
                    }
                    children = Directory.GetFileSystemEntries(parent);
                }
                catch (Exception)
                {
                    continue;
                }
                foreach (var child in children)
                {
                    var name = Path.GetFileName(child);
                    var match = NumberedFile.Match(name);
                    int number;
                    if (!match.Success || !int.TryParse(match.Groups["number"].Value, out number) || number < 2)
                    {
                        continue;
                    }
                    if (!File.Exists(child))
                    {
                        continue;
                    }
                    var plain = match.Groups["stem"].Value + match.Groups["suffix"].Value;
                    if (!pair.Value.Contains(plain))
                    {
                        continue;
                    }
                    var sibling = pair.Key == "." ? name : pair.Key + "/" + name;
                    if (!known.Add(sibling))
                    {
                        continue;
                    }
                    found.Add(new PlanRow
                    {
                        Title = Path.GetFileNameWithoutExtension(name),
                        Url = "",
                        Path = sibling,
                        Group = "",
                        Disposition = "drop",
                        Reason = "Duplicate .strm file already in the folder",
                    });
                }
            }
            return found;
        }

        private static string StrmText(string path)
        {
            try
            {
                if (!File.Exists(path))
                {
                    return null;
                }
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                {
                    var buffer = new byte[8192];
                    var read = stream.Read(buffer, 0, buffer.Length);
                    return new UTF8Encoding(false, false).GetString(buffer, 0, read).Trim();
                }
            }
            catch (Exception)
            {
                return "";
            }
        }

        public static string Inside(string root, string relative)
        {
            if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative))
            {
                throw new InvalidOperationException("Refusing an unsafe path: " + relative);
            }
            var parts = Slash(relative).Split('/');
            foreach (var part in parts)
            {
                if (part == "..")
                {
                    throw new InvalidOperationException("Refusing an unsafe path: " + relative);
                }
            }
            var fullRoot = Path.GetFullPath(root);
            var target = Path.GetFullPath(Path.Combine(fullRoot, relative.Replace('/', Path.DirectorySeparatorChar)));
            var prefix = fullRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (!target.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Refusing an unsafe path: " + relative);
            }
            return target;
        }

        private static string StripQuality(string value, Regex extra)
        {
            var text = Quality.Replace(value ?? "", " ");
            if (extra != null)
            {
                text = extra.Replace(text, " ");
            }
            text = EmptyBrackets.Replace(text, " ");
            text = text.Replace(".", " ").Replace("_", " ");
            return Spaces.Replace(text, " ").Trim();
        }

        private static Tuple<string, int?> Normalize(string value, Regex extra)
        {
            var years = new List<int>();
            foreach (Match match in YearPattern.Matches(value ?? ""))
            {
                years.Add(int.Parse(match.Value));
            }
            int? year = years.Count > 0 ? (int?)years[years.Count - 1] : null;
            var text = BracketPattern.Replace(value ?? "", " ");
            text = ParenPattern.Replace(text, " ");
            text = Quality.Replace(text, " ");
            if (extra != null)
            {
                text = extra.Replace(text, " ");
            }
            text = text.ToLowerInvariant().Replace("&", " and ");
            text = text.Replace("'", "").Replace("’", "").Replace("`", "");
            text = NonWord.Replace(text, " ");
            text = YearPattern.Replace(text, " ");
            text = LeadingArticle.Replace(text, "");
            text = Spaces.Replace(text, " ").Trim();
            if (text.Length == 0 && year.HasValue)
            {
                text = year.Value.ToString();
            }
            return Tuple.Create(text, year);
        }

        private static Regex CompileExtra(string tags)
        {
            if (string.IsNullOrWhiteSpace(tags))
            {
                return null;
            }
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var escaped = new List<Tuple<string, string>>();
            foreach (var part in Regex.Split(tags, @"[\n,;]+"))
            {
                var tag = part.Trim();
                if (tag.Length < 2 || !seen.Add(tag))
                {
                    continue;
                }
                var token = Regex.Escape(tag).Replace(" ", @"\s+");
                escaped.Add(Tuple.Create(tag, token));
            }
            if (escaped.Count == 0)
            {
                return null;
            }
            escaped.Sort((left, right) => right.Item1.Length.CompareTo(left.Item1.Length));
            var body = string.Join("|", escaped.ConvertAll(item => item.Item2).ToArray());
            return new Regex(
                @"(?<![A-Za-z0-9])(?:" + body + @")(?![A-Za-z0-9])",
                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant,
                TimeSpan.FromSeconds(2));
        }

        private static string Slash(string path)
        {
            return (path ?? "").Replace('\\', '/');
        }
    }
}
