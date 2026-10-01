using System;
using System.IO;
using System.Linq;
using System.Threading;

// behaviour.txt next to the exe: "good" (confirms its start), "crash" (exits 3 before confirming), "hang" (never
// confirms, exits after a while). Every run appends its arguments to runs.txt in the install root.
static class Program
{
    static int Main(string[] args)
    {
        var dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        var root = Path.GetDirectoryName(dir);
        var version = Path.GetFileName(dir).Substring(4);
        var behaviour = File.Exists(Path.Combine(dir, "behaviour.txt"))
            ? File.ReadAllText(Path.Combine(dir, "behaviour.txt")).Trim() : "good";
        File.AppendAllText(Path.Combine(root, "runs.txt"), version + " " + string.Join(" ", args) + Environment.NewLine);
        if (args.Contains("--quit")) return 7;  // a command: the launcher must hand back this exit code
        if (behaviour == "crash") return 3;
        if (behaviour == "hang") { Thread.Sleep(4000); return 0; }
        var i = Array.IndexOf(args, "--update-trial");
        var marker = i >= 0 ? Path.Combine(root, ".update", "trial-ok-" + args[i + 1]) : null;
        Thread.Sleep(300);  // "loading"
        if (marker != null) File.WriteAllText(marker, version);
        else File.WriteAllText(Path.Combine(root, ".update", "started-" + version), "ok");
        Thread.Sleep(1500);  // stay "running" for a moment, like the app would
        return 0;
    }
}
