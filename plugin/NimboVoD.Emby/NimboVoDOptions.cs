using System;
using System.Collections.Generic;
using System.ComponentModel;
using Emby.Web.GenericEdit;
using Emby.Web.GenericEdit.Common;
using MediaBrowser.Model.Attributes;

namespace NimboVoD.Emby
{
    public enum StreamLayout
    {
        [Description("Movies and TV")]
        Library,

        [Description("By group")]
        Group,

        [Description("Flat")]
        Flat,
    }

    public class NimboVoDOptions : EditableOptionsBase
    {
        public NimboVoDOptions()
        {
            LastResult = "";
            PlaylistUrls = "";
            PlaylistFiles = "";
            BrowseM3u = "";
            OutputFolder = "";
            Layout = StreamLayout.Library;
            StripQuality = true;
            ExtraTags = "";
            UseYears = true;
            MatchFilenames = true;
            DropDuplicateStrm = true;
            ReadEveryLibrary = false;
            LibraryIds = "";
            RefreshMinutes = 0;
            RunNow = false;
            LibraryChoices = new List<EditorSelectOption>();
            ShowRemovedTags = false;
            RemovedTags = "";
            PrepareTagList();
        }

        public override string EditorTitle => "NimboVoD";

        public override string EditorDescription =>
            "Load M3U playlists, leave out anything already in this Emby library, and update a folder of .strm files.";

        [DisplayName("Last result")]
        [Description("Filled in after a run.")]
        [EditMultiline(3)]
        public string LastResult { get; set; }

        [DisplayName("M3U links")]
        [Description("One link per line.")]
        [EditMultiline(4)]
        public string PlaylistUrls { get; set; }

        [DisplayName("Local M3U Files")]
        [Description("One full path per line. These files are on this server.")]
        [EditMultiline(3)]
        public string PlaylistFiles { get; set; }

        [DisplayName("Browse for an M3U file")]
        [Description("Opens this server's folders. Saving adds the file to Local M3U Files.")]
        [EditFilePicker]
        public string BrowseM3u { get; set; }

        [DisplayName("Save folder")]
        [Description("NimboVoD writes .strm files here. Files that are not part of the plan are left alone.")]
        [EditFolderPicker]
        public string OutputFolder { get; set; }

        [DisplayName("Folder layout")]
        public StreamLayout Layout { get; set; }

        [DisplayName("Strip quality tags from file names")]
        public bool StripQuality { get; set; }

        [DisplayName("View tags being removed")]
        [Description("Turn this on to show the tags stripped from file names.")]
        public bool ShowRemovedTags { get; set; }

        [DisplayName("Tags being removed")]
        [EditMultiline(12)]
        [VisibleCondition(nameof(ShowRemovedTags), SimpleCondition.IsTrue)]
        public string RemovedTags { get; set; }

        [DisplayName("Also strip these tags")]
        [Description("One per line or separated by commas.")]
        [EditMultiline(3)]
        public string ExtraTags { get; set; }

        [DisplayName("Use years when matching movies")]
        public bool UseYears { get; set; }

        [DisplayName("Match file names")]
        public bool MatchFilenames { get; set; }

        [DisplayName("Remove duplicate .strm files")]
        public bool DropDuplicateStrm { get; set; }

        [DisplayName("Read every library")]
        [Description("Leave this off and check the libraries to compare. Turn it on to use every library.")]
        public bool ReadEveryLibrary { get; set; }

        [Browsable(false)]
        public List<EditorSelectOption> LibraryChoices { get; set; }

        [DisplayName("Libraries")]
        [Description("Check the libraries to compare. A season already in Emby as real video is left out. An existing .strm file does not count as the library.")]
        [EditMultilSelect]
        [SelectItemsSource(nameof(LibraryChoices))]
        [VisibleCondition(nameof(ReadEveryLibrary), SimpleCondition.IsFalse)]
        public string LibraryIds { get; set; }

        [DisplayName("Refresh every")]
        [Description("Minutes. 0 runs only when you use Run now. One minute is the shortest interval. Seven days is the longest.")]
        [MinValue(0)]
        [MaxValue(10080)]
        public int RefreshMinutes { get; set; }

        [DisplayName("Run now")]
        [Description("Turn this on and press Save. Last result shows each step, then the counts. The box clears when the update starts.")]
        public bool RunNow { get; set; }

        public static string LayoutKey(StreamLayout layout)
        {
            switch (layout)
            {
                case StreamLayout.Group:
                    return "group";
                case StreamLayout.Flat:
                    return "flat";
                default:
                    return "library";
            }
        }

        public void PrepareTagList()
        {
            var lines = new List<string>(global::NimboVoD.Cleaner.Cleaner.QualityTags);
            var extras = global::NimboVoD.Cleaner.Cleaner.ExtraTagNames(ExtraTags);
            if (extras.Count > 0)
            {
                lines.Add("");
                lines.Add("Also strip these tags:");
                lines.AddRange(extras);
            }
            RemovedTags = string.Join("\n", lines.ToArray());
        }

        public void AcceptBrowsedM3u()
        {
            var picked = (BrowseM3u ?? "").Trim();
            BrowseM3u = "";
            if (picked.Length == 0)
            {
                return;
            }
            var lines = new List<string>();
            if (!string.IsNullOrWhiteSpace(PlaylistFiles))
            {
                foreach (var line in PlaylistFiles.Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.None))
                {
                    var trimmed = line.Trim();
                    if (trimmed.Length > 0)
                    {
                        lines.Add(trimmed);
                    }
                }
            }
            foreach (var line in lines)
            {
                if (string.Equals(line, picked, StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }
            }
            lines.Add(picked);
            PlaylistFiles = string.Join("\n", lines.ToArray());
        }

        public string[] SelectedLibraryIds()
        {
            if (string.IsNullOrWhiteSpace(LibraryIds))
            {
                return new string[0];
            }
            var ids = new List<string>();
            foreach (var part in LibraryIds.Split(new[] { ',', '\r', '\n', ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var trimmed = part.Trim().Trim('"', '\'', '[', ']');
                if (trimmed.Length > 0 && !ids.Exists(item => string.Equals(item, trimmed, StringComparison.OrdinalIgnoreCase)))
                {
                    ids.Add(trimmed);
                }
            }
            return ids.ToArray();
        }
    }
}
