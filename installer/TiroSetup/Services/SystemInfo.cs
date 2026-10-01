using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Management;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace TiroSetup.Services
{
    /// <summary>What Setup learns about the PC to pick the best way to run Tiro.</summary>
    public sealed class SystemInfo
    {
        public string WindowsName = "Windows";
        public int WindowsBuild;
        public bool Is64Bit = Environment.Is64BitOperatingSystem;

        public string CpuName = "Unknown processor";
        public int CpuCores = Environment.ProcessorCount;
        public int CpuThreads = Environment.ProcessorCount;
        public double CpuGhz;
        public bool Avx2;
        public bool Avx512;

        public double RamGb;

        public List<string> DisplayAdapters = new List<string>();
        public NvidiaGpu Nvidia;          // null when there is no usable NVIDIA driver
        public string NvidiaDriver = "";  // e.g. "581.29"

        public List<string> Microphones = new List<string>();

        public bool Online;
        public string OnlineDetail = "";

        public string CpuShortName
        {
            get
            {
                var n = CpuName.Replace("(R)", "").Replace("(TM)", "").Replace("(tm)", "").Replace(" CPU", "");
                var at = n.IndexOf(" @", StringComparison.Ordinal);
                if (at > 0) n = n.Substring(0, at);
                n = n.Replace("Intel Core", "Intel Core").Replace("  ", " ").Trim();
                var cut = n.IndexOf(" with ", StringComparison.OrdinalIgnoreCase);  // "AMD Ryzen 5 5600 6-Core Processor"
                if (cut > 0) n = n.Substring(0, cut);
                return n.Replace(" 6-Core Processor", "").Replace(" 8-Core Processor", "").Replace(" Processor", "").Trim();
            }
        }

        public static Task<SystemInfo> CollectAsync()
        {
            return Task.Run(() =>
            {
                var s = new SystemInfo();
                Try(s.ReadWindows);
                Try(s.ReadCpu);
                Try(s.ReadRam);
                Try(s.ReadGpus);
                Try(s.ReadMicrophones);
                return s;
            });
        }

        public async Task CheckOnlineAsync()
        {
            try
            {
                using (var http = new HttpClient { Timeout = TimeSpan.FromSeconds(8) })
                {
                    http.DefaultRequestHeaders.UserAgent.ParseAdd("TiroSetup/1.1");
                    var gh = http.SendAsync(new HttpRequestMessage(HttpMethod.Head, "https://github.com"));
                    var hf = http.SendAsync(new HttpRequestMessage(HttpMethod.Head, "https://huggingface.co"));
                    await Task.WhenAll(gh, hf).ConfigureAwait(false);
                    Online = gh.Result.IsSuccessStatusCode && hf.Result.IsSuccessStatusCode;
                    OnlineDetail = Online ? "GitHub and Hugging Face are reachable" : "A download server didn't answer";
                }
            }
            catch (Exception e)
            {
                Online = false;
                OnlineDetail = "No connection (" + e.GetBaseException().Message.TrimEnd('.') + ")";
            }
        }

        static void Try(Action a)
        {
            try { a(); } catch { /* detection is best-effort */ }
        }

        void ReadWindows()
        {
            using (var k = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion"))
            {
                var product = (k?.GetValue("ProductName") as string) ?? "Windows";
                var display = (k?.GetValue("DisplayVersion") as string) ?? (k?.GetValue("ReleaseId") as string) ?? "";
                int.TryParse(k?.GetValue("CurrentBuild") as string, out WindowsBuild);
                if (WindowsBuild >= 22000) product = product.Replace("Windows 10", "Windows 11");
                WindowsName = (product + " " + display).Trim();
            }
        }

        void ReadCpu()
        {
            using (var q = new ManagementObjectSearcher("SELECT Name, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed FROM Win32_Processor"))
            {
                int cores = 0, threads = 0;
                foreach (ManagementObject o in q.Get())
                {
                    CpuName = ((o["Name"] as string) ?? CpuName).Trim();
                    cores += Convert.ToInt32(o["NumberOfCores"]);
                    threads += Convert.ToInt32(o["NumberOfLogicalProcessors"]);
                    CpuGhz = Convert.ToDouble(o["MaxClockSpeed"]) / 1000.0;
                }
                if (cores > 0) CpuCores = cores;
                if (threads > 0) CpuThreads = threads;
            }
            Avx2 = Native.IsProcessorFeaturePresent(40);
            Avx512 = Native.IsProcessorFeaturePresent(41);
        }

        void ReadRam()
        {
            var m = new Native.MEMORYSTATUSEX { dwLength = (uint)Marshal.SizeOf(typeof(Native.MEMORYSTATUSEX)) };
            if (Native.GlobalMemoryStatusEx(ref m)) RamGb = m.ullTotalPhys / 1073741824.0;
        }

        void ReadGpus()
        {
            using (var q = new ManagementObjectSearcher("SELECT Name, DriverVersion FROM Win32_VideoController"))
            {
                foreach (ManagementObject o in q.Get())
                {
                    var name = (o["Name"] as string ?? "").Trim();
                    if (name.Length == 0 || name.IndexOf("Basic Display", StringComparison.OrdinalIgnoreCase) >= 0
                        || name.IndexOf("Remote", StringComparison.OrdinalIgnoreCase) >= 0) continue;
                    DisplayAdapters.Add(name);
                    var ver = o["DriverVersion"] as string ?? "";
                    if (name.IndexOf("NVIDIA", StringComparison.OrdinalIgnoreCase) >= 0 && NvidiaDriver.Length == 0)
                        NvidiaDriver = NvidiaVersion(ver);
                }
            }
            Nvidia = NvidiaGpu.Query();
        }

        static string NvidiaVersion(string windowsVersion)
        {
            // Windows reports NVIDIA drivers as e.g. 32.0.15.8129 -> the familiar "581.29"
            var digits = new string(windowsVersion.Where(char.IsDigit).ToArray());
            if (digits.Length < 5) return windowsVersion;
            var last5 = digits.Substring(digits.Length - 5);
            return last5.Substring(0, 3) + "." + last5.Substring(3);
        }

        void ReadMicrophones()
        {
            // Active capture endpoints (MMDevice IDs with {0.0.1.*} are inputs; {0.0.0.*} are outputs)
            using (var q = new ManagementObjectSearcher(
                "SELECT Name, DeviceID, Status FROM Win32_PnPEntity WHERE PNPClass = 'AudioEndpoint'"))
            {
                foreach (ManagementObject o in q.Get())
                {
                    var id = o["DeviceID"] as string ?? "";
                    var status = o["Status"] as string ?? "";
                    if (id.IndexOf("{0.0.1.", StringComparison.OrdinalIgnoreCase) >= 0 && status == "OK")
                        Microphones.Add((o["Name"] as string ?? "").Trim());
                }
            }
        }

        public static double FreeSpaceGb(string path)
        {
            try
            {
                var root = Path.GetPathRoot(Path.GetFullPath(path));
                return new DriveInfo(root).AvailableFreeSpace / 1073741824.0;
            }
            catch { return -1; }
        }
    }

    /// <summary>The first CUDA device, read straight from the NVIDIA driver (nvcuda.dll).</summary>
    public sealed class NvidiaGpu
    {
        public string Name;
        public double MemoryGb;
        public int Major, Minor, Multiprocessors;
        public int DriverCuda;  // 13000 = CUDA 13.0

        public string ComputeCapability => Major + "." + Minor;
        public string CudaVersion => (DriverCuda / 1000) + "." + (DriverCuda % 1000 / 10);

        public static NvidiaGpu Query()
        {
            try
            {
                if (Native.cuInit(0) != 0) return null;
                if (Native.cuDeviceGetCount(out var count) != 0 || count < 1) return null;
                Native.cuDeviceGet(out var dev, 0);
                var name = new StringBuilder(256);
                Native.cuDeviceGetName(name, 256, dev);
                Native.cuDeviceTotalMem(out var bytes, dev);
                Native.cuDeviceGetAttribute(out var major, 75, dev);
                Native.cuDeviceGetAttribute(out var minor, 76, dev);
                Native.cuDeviceGetAttribute(out var sms, 16, dev);
                Native.cuDriverGetVersion(out var drv);
                return new NvidiaGpu
                {
                    Name = name.ToString(),
                    MemoryGb = bytes.ToUInt64() / 1073741824.0,
                    Major = major,
                    Minor = minor,
                    Multiprocessors = sms,
                    DriverCuda = drv,
                };
            }
            catch (DllNotFoundException) { return null; }
            catch (EntryPointNotFoundException) { return null; }
        }
    }

    static class Native
    {
        [StructLayout(LayoutKind.Sequential)]
        public struct MEMORYSTATUSEX
        {
            public uint dwLength, dwMemoryLoad;
            public ulong ullTotalPhys, ullAvailPhys, ullTotalPageFile, ullAvailPageFile, ullTotalVirtual, ullAvailVirtual, ullAvailExtendedVirtual;
        }

        [DllImport("kernel32.dll")] public static extern bool GlobalMemoryStatusEx(ref MEMORYSTATUSEX m);
        [DllImport("kernel32.dll")] public static extern bool IsProcessorFeaturePresent(uint feature);

        [DllImport("nvcuda.dll")] public static extern int cuInit(uint flags);
        [DllImport("nvcuda.dll")] public static extern int cuDeviceGetCount(out int count);
        [DllImport("nvcuda.dll")] public static extern int cuDeviceGet(out int device, int ordinal);
        [DllImport("nvcuda.dll", CharSet = CharSet.Ansi)] public static extern int cuDeviceGetName(StringBuilder name, int len, int dev);
        [DllImport("nvcuda.dll", EntryPoint = "cuDeviceTotalMem_v2")] public static extern int cuDeviceTotalMem(out UIntPtr bytes, int dev);
        [DllImport("nvcuda.dll")] public static extern int cuDeviceGetAttribute(out int value, int attribute, int dev);
        [DllImport("nvcuda.dll")] public static extern int cuDriverGetVersion(out int version);
    }
}
