using System;
using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;

namespace TiroSetup.Services
{
    /// <summary>Resumable HTTP downloads with SHA-256 verification and retries.</summary>
    public sealed class Downloader : IDisposable
    {
        readonly HttpClient http;

        public Downloader()
        {
            var handler = new HttpClientHandler { AllowAutoRedirect = true };
            http = new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };
            http.DefaultRequestHeaders.UserAgent.ParseAdd("TiroSetup/1.1 (+https://github.com/Min3scon/tiro)");
        }

        public void Dispose() => http.Dispose();

        /// <param name="progress">called with bytes received so far for this file</param>
        public async Task DownloadAsync(string url, string dest, long expectedSize, string sha256,
                                        Action<long> progress, CancellationToken ct)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(dest));
            if (File.Exists(dest) && (expectedSize <= 0 || new FileInfo(dest).Length == expectedSize)
                && (string.IsNullOrEmpty(sha256) || await HashMatchesAsync(dest, sha256, ct)))
            {
                progress(new FileInfo(dest).Length);
                return;  // already downloaded and verified (e.g. a retried or resumed install)
            }
            var part = dest + ".part";
            Exception last = null;
            for (var attempt = 1; attempt <= 5; attempt++)
            {
                ct.ThrowIfCancellationRequested();
                try
                {
                    await FetchAsync(url, part, expectedSize, progress, ct).ConfigureAwait(false);
                    if (!string.IsNullOrEmpty(sha256) && !await HashMatchesAsync(part, sha256, ct))
                    {
                        File.Delete(part);
                        throw new IOException("The download was corrupted (checksum mismatch).");
                    }
                    if (File.Exists(dest)) File.Delete(dest);
                    File.Move(part, dest);
                    return;
                }
                catch (OperationCanceledException) { throw; }
                catch (Exception e)
                {
                    last = e;
                    await Task.Delay(TimeSpan.FromSeconds(Math.Min(10, attempt * 2)), ct).ConfigureAwait(false);
                }
            }
            throw new IOException($"Couldn't download {Path.GetFileName(dest)}: {last?.GetBaseException().Message}", last);
        }

        async Task FetchAsync(string url, string part, long expectedSize, Action<long> progress, CancellationToken ct)
        {
            long have = File.Exists(part) ? new FileInfo(part).Length : 0;
            if (expectedSize > 0 && have > expectedSize) { File.Delete(part); have = 0; }
            var req = new HttpRequestMessage(HttpMethod.Get, url);
            if (have > 0) req.Headers.Range = new RangeHeaderValue(have, null);
            using (var resp = await http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct).ConfigureAwait(false))
            {
                if (have > 0 && resp.StatusCode != System.Net.HttpStatusCode.PartialContent) have = 0;  // server ignored Range
                resp.EnsureSuccessStatusCode();
                using (var src = await resp.Content.ReadAsStreamAsync().ConfigureAwait(false))
                using (var dst = new FileStream(part, have > 0 ? FileMode.Append : FileMode.Create, FileAccess.Write,
                                                FileShare.None, 1 << 20, useAsync: true))
                {
                    var buf = new byte[1 << 20];
                    long total = have;
                    progress(total);
                    int n;
                    while ((n = await src.ReadAsync(buf, 0, buf.Length, ct).ConfigureAwait(false)) > 0)
                    {
                        await dst.WriteAsync(buf, 0, n, ct).ConfigureAwait(false);
                        total += n;
                        progress(total);
                    }
                }
            }
            if (expectedSize > 0 && new FileInfo(part).Length != expectedSize)
                throw new IOException("The connection dropped before the download finished.");
        }

        public static Task<bool> HashMatchesAsync(string path, string sha256, CancellationToken ct)
        {
            return Task.Run(() =>
            {
                using (var sha = SHA256.Create())
                using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20))
                {
                    var buf = new byte[1 << 20];
                    int n;
                    while ((n = f.Read(buf, 0, buf.Length)) > 0)
                    {
                        ct.ThrowIfCancellationRequested();
                        sha.TransformBlock(buf, 0, n, null, 0);
                    }
                    sha.TransformFinalBlock(new byte[0], 0, 0);
                    var hex = BitConverter.ToString(sha.Hash).Replace("-", "");
                    return string.Equals(hex, sha256, StringComparison.OrdinalIgnoreCase);
                }
            }, ct);
        }
    }
}
