using System;

namespace TiroSetup.Services
{
    /// <summary>Decides GPU vs CPU for this PC and explains why, with estimated speed for both.</summary>
    public sealed class Recommendation
    {
        public bool GpuUsable;
        public bool RecommendGpu;
        public string GpuTitle = "";     // "NVIDIA GeForce RTX 3070"
        public string GpuDetail = "";    // "8 GB · CUDA 13.0 · driver 581.29"
        public string GpuProblem = "";   // why the GPU option is unavailable
        public string GpuFixUrl = "";
        public double GpuSeconds;        // estimated time per live update
        public double CpuSeconds;
        public string CpuTitle = "";
        public string CpuDetail = "";
        public string Headline = "";
        public string Why = "";

        // Measured on the reference PC (RTX 3070 / Core i5-10600KF, 6 cores at 4.1 GHz) for a 6 s phrase.
        const double RefGpuMs = 58, RefGpuSms = 46;
        const double RefCpuMs = 390, RefCpuCores = 6, RefCpuGhz = 4.1;

        public static Recommendation For(SystemInfo s)
        {
            var r = new Recommendation
            {
                CpuTitle = s.CpuShortName,
                CpuDetail = $"{s.CpuCores} cores · {s.CpuThreads} threads" + (s.Avx512 ? " · AVX-512" : s.Avx2 ? " · AVX2" : ""),
            };

            var cores = Math.Max(2, s.CpuCores);
            var ghz = s.CpuGhz > 0.5 ? s.CpuGhz : 3.0;
            var cpuMs = RefCpuMs * Math.Pow(RefCpuCores / cores, 0.8) * Math.Sqrt(RefCpuGhz / ghz);
            if (s.Avx512) cpuMs *= 0.7;
            else if (!s.Avx2) cpuMs *= 1.8;
            r.CpuSeconds = Clamp(cpuMs, 120, 4000) / 1000.0;

            var g = s.Nvidia;
            if (g == null)
            {
                var other = s.DisplayAdapters.Count > 0 ? s.DisplayAdapters[0] : "Your graphics card";
                r.GpuTitle = s.DisplayAdapters.Count > 0 ? other : "No NVIDIA graphics card";
                r.GpuProblem = s.DisplayAdapters.Count > 0
                    ? $"GPU acceleration needs an NVIDIA graphics card. {other} isn't supported."
                    : "GPU acceleration needs an NVIDIA graphics card, and none was found.";
            }
            else
            {
                r.GpuTitle = g.Name;
                r.GpuDetail = $"{g.MemoryGb:0} GB · CUDA {g.CudaVersion}" + (s.NvidiaDriver.Length > 0 ? $" · driver {s.NvidiaDriver}" : "");
                if (g.Major * 10 + g.Minor < 75)
                    r.GpuProblem = $"The {g.Name} is too old for CUDA 13 (it needs an RTX 20-series or newer).";
                else if (g.DriverCuda < 13000)
                {
                    r.GpuProblem = $"Your NVIDIA driver{(s.NvidiaDriver.Length > 0 ? " (" + s.NvidiaDriver + ")" : "")} is too old for CUDA 13. "
                                 + "Update to driver 580 or newer, then run Setup again to switch to the GPU.";
                    r.GpuFixUrl = "https://www.nvidia.com/Download/index.aspx";
                }
                else if (g.MemoryGb < 3.5)
                    r.GpuProblem = $"The {g.Name} has {g.MemoryGb:0.#} GB of video memory; Tiro's GPU model needs about 3 GB.";
                else
                {
                    r.GpuUsable = true;
                    var sms = Math.Max(8, g.Multiprocessors);
                    r.GpuSeconds = Clamp(RefGpuMs * Math.Pow(RefGpuSms / sms, 0.5), 35, 180) / 1000.0;
                }
            }

            r.RecommendGpu = r.GpuUsable;
            if (r.RecommendGpu)
            {
                var times = Math.Max(2, Math.Round(r.CpuSeconds / r.GpuSeconds));
                r.Headline = "Your graphics card is the fastest way to run Tiro";
                r.Why = $"Your {Short(r.GpuTitle)} recognises speech about {times}× faster than your {r.CpuTitle}, "
                      + "so words appear almost the instant you finish saying them. Accuracy is identical either way. "
                      + "The GPU just makes it snappier, and your processor stays free for everything else.";
            }
            else
            {
                r.Headline = "Your processor will run Tiro";
                var lag = r.CpuSeconds <= 0.6 ? "a moment behind your voice, which is perfectly comfortable for dictation"
                        : r.CpuSeconds <= 1.2 ? "about a second behind your voice"
                        : "a second or two behind your voice";
                r.Why = $"{r.GpuProblem} Tiro will use your {r.CpuTitle} instead. It's exactly as accurate; words just appear {lag}.";
            }
            return r;
        }

        static string Short(string gpu) => gpu.Replace("NVIDIA ", "");

        static double Clamp(double v, double lo, double hi) => Math.Max(lo, Math.Min(hi, v));

        public static string Seconds(double s) => s < 0.095 ? $"≈ {s:0.00} s" : $"≈ {s:0.0} s";
    }
}
