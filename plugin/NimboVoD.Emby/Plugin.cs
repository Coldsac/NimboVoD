using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Emby.Web.GenericEdit.Common;
using MediaBrowser.Common;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Tasks;

namespace NimboVoD.Emby
{
    public class Plugin : BasePluginSimpleUI<NimboVoDOptions>
    {
        public const string PluginId = "8f4c2a19-6b7e-4d53-9a10-3c6e5d2b7f41";
        private readonly IApplicationHost _host;
        private int _handlingSave;

        public Plugin(IApplicationHost applicationHost)
            : base(applicationHost)
        {
            _host = applicationHost;
            Instance = this;
        }

        public static Plugin Instance { get; private set; }

        public override Guid Id => new Guid(PluginId);

        public override string Name => "NimboVoD";

        public override string Description => "Turn M3U playlists into a .strm folder and leave out anything already in this Emby library.";

        public NimboVoDOptions CurrentOptions()
        {
            return GetOptions();
        }

        protected override void OnCreatePageInfo(PluginPageInfo pageInfo)
        {
            pageInfo.DisplayName = "NimboVoD";
            pageInfo.EnableInMainMenu = true;
            pageInfo.MenuSection = "server";
            pageInfo.MenuIcon = "folder";
        }

        protected override NimboVoDOptions OnBeforeShowUI(NimboVoDOptions options)
        {
            options.PrepareTagList();
            options.LibraryChoices = new List<EditorSelectOption>();
            try
            {
                var library = _host.Resolve<ILibraryManager>();
                foreach (var folder in LibrarySource.List(library))
                {
                    options.LibraryChoices.Add(new EditorSelectOption(folder.Id, folder.Name));
                }
            }
            catch (Exception)
            {
                options.LibraryChoices.Clear();
            }
            return options;
        }

        protected override bool OnOptionsSaving(NimboVoDOptions options)
        {
            if (!base.OnOptionsSaving(options))
            {
                return false;
            }
            options.AcceptBrowsedM3u();
            return true;
        }

        protected override void OnOptionsSaved(NimboVoDOptions options)
        {
            if (Interlocked.Exchange(ref _handlingSave, 1) == 1)
            {
                return;
            }
            try
            {
                try
                {
                    var tasks = _host.Resolve<ITaskManager>();
                    Schedule.EnsureRegistered(tasks, _host);
                    Schedule.Apply(tasks, options.RefreshMinutes);
                }
                catch (Exception)
                {
                }
                if (!options.RunNow)
                {
                    return;
                }
                options.RunNow = false;
                var problem = RefreshTask.StartProblem(options);
                options.LastResult = problem ?? "Run started.";
                try
                {
                    SaveOptions(options);
                }
                catch (Exception)
                {
                }
                if (problem != null)
                {
                    return;
                }
                Task.Run(() => RunNowAsync());
            }
            finally
            {
                Interlocked.Exchange(ref _handlingSave, 0);
            }
        }

        private async Task RunNowAsync()
        {
            try
            {
                var library = _host.Resolve<ILibraryManager>();
                var logs = _host.Resolve<ILogManager>();
                var task = new RefreshTask(library, logs);
                await task.Execute(CancellationToken.None, new Progress<double>()).ConfigureAwait(false);
            }
            catch (Exception exc)
            {
                try
                {
                    Remember("The update failed. " + exc.Message, null);
                }
                catch (Exception)
                {
                }
            }
        }

        public void Remember(string result, string pulledAt)
        {
            var text = "";
            if (!string.IsNullOrEmpty(pulledAt))
            {
                text = "Playlists last pulled " + pulledAt + ". ";
            }
            if (!string.IsNullOrEmpty(result))
            {
                text += result;
            }
            text = text.Trim();
            if (text.Length == 0)
            {
                return;
            }
            var current = GetOptions();
            current.LastResult = text;
            SaveOptions(current);
        }
    }
}
