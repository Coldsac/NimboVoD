using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Entities.TV;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Entities;
using NimboVoD.Cleaner;

namespace NimboVoD.Emby
{
    public sealed class LibraryChoice
    {
        public string Id { get; set; }
        public string Name { get; set; }
        public string Type { get; set; }
    }

    public static class LibrarySource
    {
        public static List<LibraryChoice> List(ILibraryManager library)
        {
            var choices = new List<LibraryChoice>();
            foreach (var folder in Folders(library))
            {
                var id = ChoiceId(folder);
                var name = (folder.Name ?? "").Trim();
                if (id.Length == 0 || name.Length == 0)
                {
                    continue;
                }
                if (choices.Any(item => string.Equals(item.Id, id, StringComparison.OrdinalIgnoreCase)))
                {
                    continue;
                }
                choices.Add(new LibraryChoice
                {
                    Id = id,
                    Name = name,
                    Type = folder.CollectionType ?? "",
                });
            }
            choices.Sort((left, right) => string.Compare(left.Name, right.Name, StringComparison.OrdinalIgnoreCase));
            return choices;
        }

        public static List<LibraryItem> Read(ILibraryManager library, NimboVoDOptions options)
        {
            var selected = new HashSet<string>(options.SelectedLibraryIds(), StringComparer.OrdinalIgnoreCase);
            var limit = !options.ReadEveryLibrary;
            if (limit && selected.Count == 0)
            {
                throw new InvalidOperationException("Select at least one Emby library.");
            }
            var parents = new List<BaseItem>();
            if (limit)
            {
                var children = RootChildren(library);
                foreach (var folder in Folders(library))
                {
                    if (!Chosen(folder, selected))
                    {
                        continue;
                    }
                    var parent = OpenFolder(library, folder) ?? MatchChild(children, folder);
                    if (parent != null && !parents.Exists(item => item.InternalId == parent.InternalId))
                    {
                        parents.Add(parent);
                    }
                }
                if (parents.Count == 0)
                {
                    foreach (var child in children)
                    {
                        if (ChosenItem(child, selected) && !parents.Exists(item => item.InternalId == child.InternalId))
                        {
                            parents.Add(child);
                        }
                    }
                }
                if (parents.Count == 0)
                {
                    var found = string.Join(", ", Folders(library).Select(folder => (folder.Name ?? "").Trim()).Where(name => name.Length > 0).ToArray());
                    var picked = string.Join(", ", selected.ToArray());
                    throw new InvalidOperationException("Could not open the checked libraries (" + picked + "). Libraries on this server: " + found + ".");
                }
            }
            else
            {
                parents.Add(null);
            }

            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var items = new List<LibraryItem>();
            foreach (var parent in parents)
            {
                var query = new InternalItemsQuery
                {
                    IncludeItemTypes = new[] { "Movie", "Series", "Episode" },
                    Recursive = true,
                };
                if (parent != null)
                {
                    query.Parent = parent;
                }
                foreach (var item in library.GetItemList(query) ?? Array.Empty<BaseItem>())
                {
                    if (item == null || !seen.Add(item.Id.ToString()))
                    {
                        continue;
                    }
                    var mapped = new LibraryItem
                    {
                        Name = item.Name,
                        Type = item.GetType().Name,
                        Year = item.ProductionYear,
                        Path = item.Path,
                    };
                    var episode = item as Episode;
                    if (episode != null)
                    {
                        mapped.SeriesName = episode.SeriesName;
                        mapped.Season = episode.ParentIndexNumber;
                        mapped.Episode = episode.IndexNumber;
                    }
                    items.Add(mapped);
                }
            }
            return items;
        }

        private static List<VirtualFolderInfo> Folders(ILibraryManager library)
        {
            return library.GetVirtualFolders() ?? new List<VirtualFolderInfo>();
        }

        private static string ChoiceId(VirtualFolderInfo folder)
        {
            var itemId = (folder.ItemId ?? "").Trim();
            if (itemId.Length > 0)
            {
                return itemId;
            }
            var id = (folder.Id ?? "").Trim();
            if (id.Length > 0)
            {
                return id;
            }
            var guid = (folder.Guid ?? "").Trim();
            if (guid.Length > 0)
            {
                return guid;
            }
            return (folder.Name ?? "").Trim();
        }

        private static bool Chosen(VirtualFolderInfo folder, HashSet<string> selected)
        {
            return Hits(selected, folder.ItemId) || Hits(selected, folder.Id) || Hits(selected, folder.Guid) || Hits(selected, folder.Name);
        }

        private static bool ChosenItem(BaseItem item, HashSet<string> selected)
        {
            if (item == null)
            {
                return false;
            }
            return Hits(selected, item.Name) || Hits(selected, item.Id.ToString("D")) || Hits(selected, item.Id.ToString("N")) || Hits(selected, item.InternalId.ToString(CultureInfo.InvariantCulture));
        }

        private static bool Hits(HashSet<string> selected, string token)
        {
            var text = (token ?? "").Trim();
            if (text.Length == 0)
            {
                return false;
            }
            if (selected.Contains(text))
            {
                return true;
            }
            Guid guid;
            if (!Guid.TryParse(text, out guid))
            {
                return false;
            }
            return selected.Contains(guid.ToString("D")) || selected.Contains(guid.ToString("N"));
        }

        private static BaseItem OpenFolder(ILibraryManager library, VirtualFolderInfo folder)
        {
            return ByGuid(library, folder.ItemId)
                ?? ByGuid(library, folder.Id)
                ?? ByGuid(library, folder.Guid)
                ?? ByLong(library, folder.ItemId)
                ?? ByLong(library, folder.Id)
                ?? ByLong(library, folder.Guid);
        }

        private static BaseItem ByGuid(ILibraryManager library, string token)
        {
            Guid guid;
            if (!Guid.TryParse((token ?? "").Trim(), out guid) || guid == Guid.Empty)
            {
                return null;
            }
            return library.GetItemById(guid);
        }

        private static BaseItem ByLong(ILibraryManager library, string token)
        {
            long id;
            if (!long.TryParse((token ?? "").Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out id) || id <= 0)
            {
                return null;
            }
            return library.GetItemById(id);
        }

        private static List<BaseItem> RootChildren(ILibraryManager library)
        {
            var root = library.RootFolder as Folder;
            if (root == null)
            {
                return new List<BaseItem>();
            }
            var children = new List<BaseItem>();
            foreach (var child in root.GetChildren(new InternalItemsQuery()))
            {
                if (child != null)
                {
                    children.Add(child);
                }
            }
            return children;
        }

        private static BaseItem MatchChild(List<BaseItem> children, VirtualFolderInfo folder)
        {
            var name = (folder.Name ?? "").Trim();
            if (name.Length == 0)
            {
                return null;
            }
            foreach (var child in children)
            {
                if (string.Equals((child.Name ?? "").Trim(), name, StringComparison.OrdinalIgnoreCase))
                {
                    return child;
                }
            }
            return null;
        }
    }
}
