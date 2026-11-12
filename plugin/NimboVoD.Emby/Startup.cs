using System;
using System.Collections.Generic;
using MediaBrowser.Common;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Tasks;

namespace NimboVoD.Emby
{
    public sealed class Startup : IServerEntryPoint, IDisposable
    {
        private readonly ITaskManager _tasks;
        private readonly IApplicationHost _host;

        public Startup(ITaskManager tasks, IApplicationHost host)
        {
            _tasks = tasks;
            _host = host;
        }

        public void Run()
        {
            Schedule.EnsureRegistered(_tasks, _host);
            var plugin = Plugin.Instance;
            var minutes = plugin == null ? 0 : plugin.CurrentOptions().RefreshMinutes;
            Schedule.Apply(_tasks, minutes);
        }

        public void Dispose()
        {
        }
    }

    public static class Schedule
    {
        public static void EnsureRegistered(ITaskManager tasks, IApplicationHost host)
        {
            if (tasks == null || host == null)
            {
                return;
            }
            if (tasks.ScheduledTasks != null)
            {
                foreach (var worker in tasks.ScheduledTasks)
                {
                    if (worker != null && worker.ScheduledTask is RefreshTask)
                    {
                        return;
                    }
                }
            }
            var library = host.Resolve<ILibraryManager>();
            var logs = host.Resolve<ILogManager>();
            tasks.AddTasks(new List<IScheduledTask> { new RefreshTask(library, logs) });
        }

        public static void Apply(ITaskManager tasks, int minutes)
        {
            if (tasks == null || tasks.ScheduledTasks == null)
            {
                return;
            }
            if (minutes < 0)
            {
                minutes = 0;
            }
            if (minutes > 10080)
            {
                minutes = 10080;
            }
            foreach (var worker in tasks.ScheduledTasks)
            {
                if (worker == null || !(worker.ScheduledTask is RefreshTask))
                {
                    continue;
                }
                if (minutes < 1)
                {
                    worker.Triggers = Array.Empty<TaskTriggerInfo>();
                }
                else
                {
                    worker.Triggers = new[]
                    {
                        new TaskTriggerInfo
                        {
                            Type = TaskTriggerInfo.TriggerInterval,
                            IntervalTicks = TimeSpan.FromMinutes(minutes).Ticks,
                        },
                    };
                }
                worker.ReloadTriggerEvents();
            }
        }
    }
}
