using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace NimboVoD.Emby
{
    public static class PlaylistClient
    {
        public const int MaxBytes = 80 * 1024 * 1024;
        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(120) };
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, false);

        public static async Task<string> ReadAsync(string source, CancellationToken cancellationToken)
        {
            var text = (source ?? "").Trim();
            if (text.StartsWith("http://", StringComparison.OrdinalIgnoreCase) || text.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            {
                return await DownloadAsync(text, cancellationToken).ConfigureAwait(false);
            }
            var info = new FileInfo(text);
            if (!info.Exists)
            {
                throw new FileNotFoundException("Playlist file was not found.", text);
            }
            if (info.Length > MaxBytes)
            {
                throw new InvalidOperationException("The download is over 80 MB.");
            }
            using (var stream = new FileStream(info.FullName, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                return await ReadLimitedAsync(stream, cancellationToken).ConfigureAwait(false);
            }
        }

        public static string Label(string source)
        {
            Uri uri;
            if (Uri.TryCreate(source, UriKind.Absolute, out uri) && (uri.Scheme == "http" || uri.Scheme == "https"))
            {
                var path = uri.AbsolutePath;
                var name = path.Substring(path.LastIndexOf('/') + 1);
                return string.IsNullOrEmpty(name) ? uri.Host : name;
            }
            return Path.GetFileName(source);
        }

        private static async Task<string> DownloadAsync(string url, CancellationToken cancellationToken)
        {
            Uri uri;
            if (!Uri.TryCreate(url, UriKind.Absolute, out uri) || (uri.Scheme != "http" && uri.Scheme != "https"))
            {
                throw new InvalidOperationException("The playlist URL must start with http or https.");
            }
            using (var request = new HttpRequestMessage(HttpMethod.Get, uri))
            {
                request.Headers.TryAddWithoutValidation("User-Agent", "NimboVoD/1.0");
                using (var response = await Http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken).ConfigureAwait(false))
                {
                    response.EnsureSuccessStatusCode();
                    var length = response.Content.Headers.ContentLength;
                    if (length.HasValue && length.Value > MaxBytes)
                    {
                        throw new InvalidOperationException("The download is over 80 MB.");
                    }
                    using (var stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false))
                    {
                        return await ReadLimitedAsync(stream, cancellationToken).ConfigureAwait(false);
                    }
                }
            }
        }

        private static async Task<string> ReadLimitedAsync(Stream stream, CancellationToken cancellationToken)
        {
            using (var buffer = new MemoryStream())
            {
                var chunk = new byte[65536];
                while (true)
                {
                    var read = await stream.ReadAsync(chunk, 0, chunk.Length, cancellationToken).ConfigureAwait(false);
                    if (read == 0)
                    {
                        break;
                    }
                    if (buffer.Length + read > MaxBytes)
                    {
                        throw new InvalidOperationException("The download is over 80 MB.");
                    }
                    buffer.Write(chunk, 0, read);
                }
                return Utf8.GetString(buffer.ToArray());
            }
        }
    }
}
