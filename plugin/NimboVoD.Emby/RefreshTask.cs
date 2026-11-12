using System;
using System.Collections.Generic;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Tasks;
using NimboVoD.Cleaner;
using Planner = global::NimboVoD.Cleaner.Cleaner;

namespace NimboVoD.Emby
{
    public class RefreshTask : IScheduledTask
    {
        private static int _running;
        private readonly ILibraryManager _library;
        private readonly ILogger _logger;

        public RefreshTask(ILibraryManager library, ILogManager logManager)
        {
            _library = library;
            _logger = logManager.GetLogger("NimboVoD");
        }

        public string Name => "NimboVoD";

        public string Key => "NimboVoD";

        public string Description => "Load playlists, compare them with this Emby library, and update the .strm folder.";

        public string Category => "Library";

        public IEnumerable<TaskTriggerInfo> GetDefaultTriggers()
        {
            var minutes = 0;
            var plugin = Plugin.Instance;
            if (plugin != null)
            {
                minutes = plugin.CurrentOptions().RefreshMinutes;
            }
            if (minutes < 1 || minutes > 10080)
            {
                return Array.Empty<TaskTriggerInfo>();
            }
            return new[]
            {
                new TaskTriggerInfo
                {
                    Type = TaskTriggerInfo.TriggerInterval,
                    IntervalTicks = TimeSpan.FromMinutes(minutes).Ticks,
                },
            };
        }

        public async Task Execute(CancellationToken cancellationToken, IProgress<double> progress)
        {
            if (Interlocked.CompareExchange(ref _running, 1, 0) != 0)
            {
                _logger.Info("{0}", "NimboVoD is already running.");
                return;
            }
            try
            {
                await RunAsync(cancellationToken, progress ?? new Progress<double>()).ConfigureAwait(false);
            }
            finally
            {
                Interlocked.Exchange(ref _running, 0);
            }
        }

        public static string StartProblem(NimboVoDOptions config)
        {
            if (config == null || Sources(config).Count == 0)
            {
                return "Add a playlist link or playlist file.";
            }
            if (string.IsNullOrWhiteSpace(config.OutputFolder))
            {
                return "Choose the folder where the .strm files should be written.";
            }
            if (!config.ReadEveryLibrary && config.SelectedLibraryIds().Length == 0)
            {
                return "Select at least one Emby library.";
            }
            return null;
        }

        private async Task RunAsync(CancellationToken cancellationToken, IProgress<double> progress)
        {
            var config = Plugin.Instance.CurrentOptions();
            var problem = StartProblem(config);
            if (problem != null)
            {
                Remember(problem, null);
                return;
            }
            var sources = Sources(config);
            Remember("Loading playlists.", null);

            var entries = new List<PlaylistEntry>();
            var failures = new List<string>();
            for (var index = 0; index < sources.Count; index++)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var source = sources[index];
                var label = PlaylistClient.Label(source);
                progress.Report(5 + (25.0 * index / sources.Count));
                _logger.Info("Updating playlist {0} of {1}: {2}", index + 1, sources.Count, label);
                try
                {
                    var text = await PlaylistClient.ReadAsync(source, cancellationToken).ConfigureAwait(false);
                    var parsed = Planner.ParseM3u(text);
                    if (parsed.Count == 0)
                    {
                        failures.Add(label + ": No streams in the playlist.");
                    }
                    else
                    {
                        entries.AddRange(parsed);
                    }
                }
                catch (Exception exc)
                {
                    failures.Add(label + ": " + exc.Message);
                    _logger.ErrorException("Playlist {0} failed", exc, label);
                }
            }
            if (entries.Count == 0)
            {
                Remember(string.Join(" ", failures), null);
                return;
            }
            var pulledAt = DateTime.Now.ToString("MMM d, yyyy, h:mm tt", CultureInfo.InvariantCulture);

            progress.Report(35);
            Remember("Reading the Emby library.", pulledAt);
            List<LibraryItem> library;
            try
            {
                library = LibrarySource.Read(_library, config);
            }
            catch (Exception exc)
            {
                Remember(exc.Message, pulledAt);
                _logger.ErrorException("Emby library read failed", exc);
                return;
            }
            if (library.Count == 0)
            {
                Remember("Emby returned no movies, shows, or episodes.", pulledAt);
                return;
            }
            progress.Report(55);
            var layout = NimboVoDOptions.LayoutKey(config.Layout);
            if (layout != "group" && layout != "flat" && layout != "library")
            {
                layout = "library";
            }
            var planned = Planner.Plan(entries, library, new PlanOptions
            {
                Layout = layout,
                StripQuality = config.StripQuality,
                UseYears = config.UseYears,
                MatchFilenames = config.MatchFilenames,
                DropDuplicateStrm = config.DropDuplicateStrm,
                ExtraTags = config.ExtraTags ?? "",
                OutputFolder = config.OutputFolder,
            });
            progress.Report(70);
            Remember("Writing the .strm folder.", pulledAt);
            var counts = Planner.WritePlan(config.OutputFolder.Trim(), planned, true, (done, total) =>
            {
                if (total > 0)
                {
                    progress.Report(70 + (30.0 * done / total));
                }
            });
            var note = failures.Count == 0 ? "" : string.Join(" ", failures) + " ";
            var result = note + "Added " + counts.Added.ToString("N0")
                + ", updated " + counts.Updated.ToString("N0")
                + ", left " + counts.Unchanged.ToString("N0")
                + " unchanged, removed " + counts.Removed.ToString("N0")
                + ".";
            Remember(result, pulledAt);
            _logger.Info("{0}", result);
            progress.Report(100);
        }

        private static List<string> Sources(NimboVoDOptions config)
        {
            var sources = new List<string>();
            AddLines(sources, config.PlaylistFiles);
            AddLines(sources, config.PlaylistUrls);
            return sources;
        }

        private static void AddLines(List<string> sources, string text)
        {
            if (string.IsNullOrWhiteSpace(text))
            {
                return;
            }
            foreach (var line in text.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.None))
            {
                var trimmed = line.Trim();
                if (trimmed.Length > 0)
                {
                    sources.Add(trimmed);
                }
            }
        }

        private void Remember(string result, string pulledAt)
        {
            try
            {
                Plugin.Instance.Remember(result, pulledAt);
            }
            catch (Exception exc)
            {
                _logger.ErrorException("Could not save the NimboVoD result", exc);
            }
        }
    }
}
