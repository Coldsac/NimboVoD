using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Xunit;

namespace NimboVoD.Cleaner.Tests
{
    public class CleanerTests
    {
        [Fact]
        public void TitlesAndEpisodesMatchTheDesktopCleaner()
        {
            Assert.Equal(Tuple.Create("matrix", (int?)1999), Cleaner.NormalizeTitle("The Matrix (1999) 1080p"));
            Assert.Equal(Tuple.Create("1917", (int?)1917), Cleaner.NormalizeTitle("1917"));
            Assert.Equal("The Matrix", Cleaner.StripQuality("The.Matrix.YTS.1080p", "YTS, RARBG"));
            Assert.Equal("Movie", Cleaner.StripQuality("Movie [WEB] [x265] [x264] [TS] Megusta [ ]"));
            Assert.Equal("Movie", Cleaner.StripQuality("Movie [1080p]"));
            Assert.Equal("Movie", Cleaner.StripQuality("Movie [10BIT]"));
            Assert.Equal("Movie Name", Cleaner.StripQuality("Movie.Name-LAMA"));
            Assert.Equal("Show Name S01E01", Cleaner.StripQuality("Show Name S01E01-WORLD"));
            Assert.Equal("Show Name S01E01", Cleaner.StripQuality("Show.Name.S01E01.1080p-LAMA"));
            Assert.Equal("The World S01E01", Cleaner.StripQuality("The World S01E01 - WORLD"));
            Assert.Equal("Heart Of The Beast (2026)", Cleaner.StripQuality("Heart Of The Beast (2026) 1080p WEBRip 5 1-LAMA"));
            Assert.Equal("Movie Name 2024", Cleaner.StripQuality("Movie.Name.2024.DDP5.1-LAMA"));
            Assert.Equal("Movie", Cleaner.StripQuality("Movie 7 1"));
            Assert.Equal("2 0 (2018)", Cleaner.StripQuality("2.0 (2018)"));
            Assert.Equal("movie", Cleaner.NormalizeTitle("Movie Megusta").Item1);
        }

        [Fact]
        public void MoviesAlreadyInTheLibraryAreLeftOut()
        {
            var entries = Cleaner.ParseM3u(
                "#EXTM3U\n#EXTINF:-1 group-title=\"Movies\",Dune (1984)\nhttps://example.com/dune\n"
                + "#EXTINF:-1 group-title=\"Movies\",The Matrix (1999)\nhttps://example.com/matrix\n");
            var planned = Cleaner.Plan(entries, new[]
            {
                Item("The Matrix", "Movie", 1999),
                Item("Dune", "Movie", 2021),
            }, Options("group"));
            Assert.Equal("keep", ByTitle(planned, "Dune (1984)").Disposition);
            Assert.Equal("drop", ByTitle(planned, "The Matrix (1999)").Disposition);

            var tagged = Cleaner.ParseM3u(
                "#EXTM3U\n#EXTINF:-1 group-title=\"Movies\",The Matrix YTS 1080p\nhttps://example.com/yts\n");
            var taggedPlan = Cleaner.Plan(tagged, new[] { Item("The Matrix", "Movie", 1999) }, new PlanOptions
            {
                Layout = "flat",
                StripQuality = true,
                UseYears = true,
                MatchFilenames = false,
                ExtraTags = "YTS",
            });
            Assert.Equal("The Matrix.strm", taggedPlan[0].Path);
            Assert.Equal("drop", taggedPlan[0].Disposition);
        }

        [Fact]
        public void ARealSeasonIsLeftOutAndAStrmCopyIsNotTheLibrary()
        {
            var shows = Cleaner.ParseM3u(
                "#EXTM3U\n"
                + "#EXTINF:-1 group-title=\"The Office\",The Office S01E01\nhttps://example.com/e1\n"
                + "#EXTINF:-1 group-title=\"The Office\",The Office S01E03\nhttps://example.com/e3\n"
                + "#EXTINF:-1 group-title=\"The Office\",The Office S02E01\nhttps://example.com/e4\n"
                + "#EXTINF:-1 group-title=\"The Agency: Central Intelligence\",The Agency: Central Intelligence S01E05\nhttps://example.com/a1\n"
                + "#EXTINF:-1 group-title=\"The Agency: Central Intelligence\",The Agency: Central Intelligence S02E01\nhttps://example.com/a2\n"
                + "#EXTINF:-1 group-title=\"The Agency: Unfiltered\",The Agency: Unfiltered S02E01\nhttps://example.com/u2\n"
                + "#EXTINF:-1 group-title=\"TV VOD\",Brand New Show S01E01\nhttps://example.com/new\n");
            var planned = Cleaner.Plan(shows, new[]
            {
                Item("The Office", "Series", 2005),
                Episode("Pilot", "The Office", 1, 1, "Z:/TV/The Office/Season 1/Pilot.mkv", 2005),
                Item("The Agency (2024)", "Series", 2024),
                Episode("The Bends", "The Agency (2024)", 1, 1, "Z:/TV/The Agency (2024)/Season 1/The Bends.mkv", 2024),
                Episode("Kick the Wasp's Nest", "The Agency (2024)", 2, 1, "D:/NimiboVoD/The Agency Central Intelligence/Season 02/S02E01.strm", 2025),
                Item("The Agency: Unfiltered", "Series", null),
            }, Options("library"));
            Assert.Equal("drop", ByTitle(planned, "The Office S01E01").Disposition);
            Assert.Equal("drop", ByTitle(planned, "The Office S01E03").Disposition);
            Assert.Contains("Season 1", ByTitle(planned, "The Office S01E03").Reason);
            Assert.Equal("keep", ByTitle(planned, "The Office S02E01").Disposition);
            Assert.Equal("drop", ByTitle(planned, "The Agency: Central Intelligence S01E05").Disposition);
            Assert.Equal("keep", ByTitle(planned, "The Agency: Central Intelligence S02E01").Disposition);
            Assert.Equal("keep", ByTitle(planned, "The Agency: Unfiltered S02E01").Disposition);
            Assert.Equal("keep", ByTitle(planned, "Brand New Show S01E01").Disposition);
        }

        [Fact]
        public void AudioChannelTagsDoNotHideAMovie()
        {
            var beast = Cleaner.ParseM3u(
                "#EXTM3U\n#EXTINF:-1 group-title=\"Movie VOD\",Heart Of The Beast (2026) 1080p WEBRip 5 1-LAMA\nhttps://example.com/beast\n");
            var planned = Cleaner.Plan(beast, new[]
            {
                new LibraryItem
                {
                    Name = "Heart of the Beast",
                    Type = "Movie",
                    Year = 2026,
                    Path = "Z:/Movies/Heart Of The Beast (2026)/movie.mkv",
                },
            }, Options("library"));
            Assert.Equal("drop", planned[0].Disposition);
            Assert.Equal("Movies/Heart Of The Beast (2026).strm", planned[0].Path);
        }

        [Fact]
        public void DuplicateStreamsAndNumberedFilesFollowTheDesktopRules()
        {
            var copies = Cleaner.ParseM3u(
                "#EXTM3U\n"
                + "#EXTINF:-1 group-title=\"Movies\",Movie [WEB] Megusta\nhttps://example.com/one\n"
                + "#EXTINF:-1 group-title=\"Movies\",Movie [x265]\nhttps://example.com/two\n");
            var dropped = Cleaner.Plan(copies, null, new PlanOptions
            {
                Layout = "flat",
                StripQuality = true,
                DropDuplicateStrm = true,
            });
            Assert.Equal("Movie.strm", dropped[0].Path);
            Assert.Equal("keep", dropped[0].Disposition);
            Assert.Equal("drop", dropped[1].Disposition);
            Assert.Equal("Duplicate .strm file", dropped[1].Reason);

            var kept = Cleaner.Plan(copies, null, new PlanOptions
            {
                Layout = "flat",
                StripQuality = true,
                DropDuplicateStrm = false,
            });
            Assert.Equal("Movie (2).strm", kept[1].Path);
            Assert.Equal("keep", kept[1].Disposition);

            var directory = Path.Combine(Path.GetTempPath(), "nimbovod-dup-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                File.WriteAllText(Path.Combine(directory, "Movie (2).strm"), "old\n");
                var withDisk = Cleaner.Plan(new[] { copies[0] }, null, new PlanOptions
                {
                    Layout = "flat",
                    StripQuality = true,
                    DropDuplicateStrm = true,
                    OutputFolder = directory,
                });
                Assert.Contains(withDisk, row => row.Path == "Movie (2).strm" && row.Disposition == "drop");
                Assert.True(Cleaner.DirectoryReachable(directory));
                Assert.False(Cleaner.DirectoryReachable(Path.Combine(directory, "missing")));
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        [Fact]
        public void WritePlanChangesOnlyThePlannedFiles()
        {
            var directory = Path.Combine(Path.GetTempPath(), "nimbovod-write-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                var matching = "https://example.com/a\r\n";
                File.WriteAllBytes(Path.Combine(directory, "A.strm"), System.Text.Encoding.UTF8.GetBytes(matching));
                File.WriteAllText(Path.Combine(directory, "B.strm"), "https://example.com/b-old\n");
                File.WriteAllText(Path.Combine(directory, "C.strm"), "https://example.com/c\n");
                File.WriteAllText(Path.Combine(directory, "Other.strm"), "leave me\n");
                var rows = new List<PlanRow>
                {
                    Row("A.strm", "https://example.com/a", "keep"),
                    Row("B.strm", "https://example.com/b-new", "keep"),
                    Row("New.strm", "https://example.com/new", "keep"),
                    Row("Shows/Pilot.strm", "https://example.com/pilot", "keep"),
                    Row("C.strm", "https://example.com/c", "drop", "Already in Emby"),
                    Row("A.strm", "https://example.com/a-old", "drop", "Duplicate .strm file"),
                };
                var seen = new List<Tuple<int, int>>();
                var preview = Cleaner.WritePlan(directory, rows, false, (done, total) => seen.Add(Tuple.Create(done, total)));
                Assert.Equal(2, preview.Added);
                Assert.Equal(1, preview.Updated);
                Assert.Equal(1, preview.Unchanged);
                Assert.Equal(1, preview.Removed);
                Assert.Equal(Tuple.Create(rows.Count, rows.Count), seen[seen.Count - 1]);
                Assert.False(Directory.Exists(Path.Combine(directory, "Shows")));
                Assert.Equal(matching, File.ReadAllText(Path.Combine(directory, "A.strm")));

                var applied = Cleaner.WritePlan(directory, rows, true, null);
                Assert.Equal(2, applied.Added);
                Assert.Equal(1, applied.Updated);
                Assert.Equal(1, applied.Unchanged);
                Assert.Equal(1, applied.Removed);
                Assert.Equal(matching, File.ReadAllText(Path.Combine(directory, "A.strm")));
                Assert.Equal("https://example.com/b-new", File.ReadAllText(Path.Combine(directory, "B.strm")).Trim());
                Assert.Equal("https://example.com/new", File.ReadAllText(Path.Combine(directory, "New.strm")).Trim());
                Assert.Equal("https://example.com/pilot", File.ReadAllText(Path.Combine(directory, "Shows", "Pilot.strm")).Trim());
                Assert.False(File.Exists(Path.Combine(directory, "C.strm")));
                Assert.Equal("leave me\n", File.ReadAllText(Path.Combine(directory, "Other.strm")));

                var again = Cleaner.WritePlan(directory, rows, true, null);
                Assert.Equal(0, again.Added);
                Assert.Equal(0, again.Updated);
                Assert.Equal(4, again.Unchanged);
                Assert.Equal(0, again.Removed);
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        private static PlanOptions Options(string layout)
        {
            return new PlanOptions
            {
                Layout = layout,
                StripQuality = true,
                UseYears = true,
                MatchFilenames = true,
            };
        }

        private static LibraryItem Item(string name, string type, int? year)
        {
            return new LibraryItem { Name = name, Type = type, Year = year };
        }

        private static LibraryItem Episode(string name, string series, int season, int episode, string path, int? year)
        {
            return new LibraryItem
            {
                Name = name,
                Type = "Episode",
                SeriesName = series,
                Season = season,
                Episode = episode,
                Path = path,
                Year = year,
            };
        }

        private static PlanRow ByTitle(List<PlanRow> rows, string title)
        {
            return rows.First(row => row.Title == title);
        }

        private static PlanRow Row(string path, string url, string disposition, string reason = "")
        {
            return new PlanRow { Path = path, Url = url, Disposition = disposition, Reason = reason, Title = path };
        }
    }
}
