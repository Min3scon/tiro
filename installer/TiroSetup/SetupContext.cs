using TiroSetup.Services;

namespace TiroSetup
{
    /// <summary>State shared by the Setup pages.</summary>
    public sealed class SetupContext
    {
        public readonly Manifest Manifest = Manifest.Load();
        public readonly Shell.Existing Existing = Shell.FindExisting();
        public SystemInfo System;
        public Recommendation Recommendation;
        public readonly InstallOptions Options = new InstallOptions();
        public InstallEngine Engine;

        public SetupContext()
        {
            if (Existing != null)
            {
                Options.InstallDir = Existing.Dir;
                Options.ModelKey = Existing.ModelKey;
            }
        }

        public long DownloadBytes(bool gpu)
        {
            var model = Manifest.Models[Options.ModelKey];
            return Manifest.App.Size + (gpu ? Manifest.Gpu.Size : 0) + model.Size(gpu);
        }
    }
}
