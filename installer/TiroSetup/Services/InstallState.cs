using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;

namespace TiroSetup.Services
{
    /// <summary>
    /// The versioned install layout shared with the launcher (installer/TiroLauncher) and the app (tiro/update):
    ///   Programs\Tiro\Tiro.exe (launcher), state.json, app-X.Y.Z\ (one folder per version), models\
    /// </summary>
    [DataContract]
    public sealed class TrialInfo
    {
        [DataMember(Name = "version")] public string Version;
        [DataMember(Name = "attempts")] public int Attempts;
        [DataMember(Name = "token")] public string Token;
    }

    [DataContract]
    public sealed class InstallState
    {
        [DataMember(Name = "format")] public int Format = 1;
        [DataMember(Name = "current")] public string Current;
        [DataMember(Name = "previous")] public string Previous;
        [DataMember(Name = "pending")] public string Pending;
        [DataMember(Name = "trial")] public TrialInfo Trial;
        [DataMember(Name = "bad")] public List<string> Bad = new List<string>();
        [DataMember(Name = "unconfirmed")] public int Unconfirmed;

        public static string PathIn(string root) => Path.Combine(root, "state.json");
        public static string VersionDir(string root, string v) => Path.Combine(root, "app-" + v);

        public static InstallState Load(string root)
        {
            try
            {
                using (var f = File.OpenRead(PathIn(root)))
                {
                    var s = (InstallState)new DataContractJsonSerializer(typeof(InstallState)).ReadObject(f);
                    if (s != null && s.Bad == null) s.Bad = new List<string>();
                    return s;
                }
            }
            catch
            {
                return null;
            }
        }

        public void Save(string root)
        {
            var path = PathIn(root);
            var tmp = path + ".tmp";
            using (var f = File.Create(tmp))
                new DataContractJsonSerializer(typeof(InstallState)).WriteObject(f, this);
            if (File.Exists(path)) File.Replace(tmp, path, null);
            else File.Move(tmp, path);
        }

        /// <summary>The folder of the version that runs today: versioned layout, or the old flat one.</summary>
        public static string CurrentAppDir(string root)
        {
            var s = Load(root);
            if (s?.Current != null && File.Exists(Path.Combine(VersionDir(root, s.Current), "Tiro.exe")))
                return VersionDir(root, s.Current);
            return File.Exists(Path.Combine(root, "_internal", "base_library.zip")) ? root : null;
        }

        /// <summary>
        /// Tiro 2.0.x installed flat (Tiro.exe and _internal\ directly in the folder). Move that version into its
        /// own app-X folder so it becomes the "previous" version, and the launcher can take Tiro.exe's place.
        /// </summary>
        public static string MigrateFlat(string root, string oldVersion)
        {
            var exe = Path.Combine(root, "Tiro.exe");
            var internalDir = Path.Combine(root, "_internal");
            if (File.Exists(PathIn(root)) || !Directory.Exists(internalDir) || !File.Exists(exe)) return null;
            var v = string.IsNullOrEmpty(oldVersion) ? "0.0.0" : oldVersion;
            var dir = VersionDir(root, v);
            if (Directory.Exists(dir)) dir = VersionDir(root, v = v + "-old");
            Directory.CreateDirectory(dir);
            Directory.Move(internalDir, Path.Combine(dir, "_internal"));
            File.Move(exe, Path.Combine(dir, "Tiro.exe"));
            return v;
        }

        /// <summary>Write the embedded launcher as Programs\Tiro\Tiro.exe.</summary>
        public static void WriteLauncher(string root)
        {
            using (var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("TiroLauncher.exe"))
            {
                if (s == null) throw new InvalidOperationException("this Setup has no launcher inside");
                var path = Path.Combine(root, "Tiro.exe");
                var tmp = path + ".new";
                using (var f = File.Create(tmp)) s.CopyTo(f);
                if (File.Exists(path)) File.Delete(path);
                File.Move(tmp, path);
            }
        }

        /// <summary>Remove version folders that are neither current nor previous.</summary>
        public void Cleanup(string root)
        {
            foreach (var d in Directory.GetDirectories(root, "app-*"))
            {
                var v = Path.GetFileName(d).Substring(4);
                if (v == Current || v == Previous || v == Pending) continue;
                try { Directory.Delete(d, true); } catch { }
            }
        }
    }
}
