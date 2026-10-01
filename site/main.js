// Point the main download button at the right file for this visitor, and show the current version.
(function () {
  const REPO = "Min3scon/tiro";
  const base = `https://github.com/${REPO}/releases/latest/download/`;
  const files = {
    windows: { href: base + "TiroSetup.exe", label: "Download for Windows" },
    mac: { href: base + "Tiro-mac-arm64.dmg", label: "Download for Mac (Apple Silicon)" },
  };

  function platform() {
    const ua = navigator.userAgent || "";
    const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/iPhone|iPad|iPod|Android/i.test(ua)) return "mobile";
    if (/Win/i.test(p) || /Windows/i.test(ua)) return "windows";
    if (/Mac/i.test(p) || /Macintosh/i.test(ua)) return "mac";
    return "other";
  }

  // Browsers don't say which chip a Mac has; the graphics renderer name is a good hint ("Apple M2" vs "Intel").
  function looksLikeIntelMac() {
    try {
      const gl = document.createElement("canvas").getContext("webgl");
      const ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
      const renderer = ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : "";
      return /Intel|AMD|Radeon/i.test(renderer) && !/Apple M\d/i.test(renderer);
    } catch (e) {
      return false;
    }
  }

  const primary = document.getElementById("primary-dl");
  const secondary = document.getElementById("secondary-dl");
  const note = document.getElementById("dl-note");
  const os = platform();
  if (os === "windows") {
    primary.href = files.windows.href;
    primary.textContent = files.windows.label;
    secondary.href = files.mac.href;
    secondary.textContent = "Mac (Apple Silicon)";
    note.textContent = "Windows 10 or 11, 64-bit · about 2 MB; Setup downloads the rest and explains every choice.";
  } else if (os === "mac") {
    primary.href = files.mac.href;
    primary.textContent = files.mac.label;
    secondary.href = files.windows.href;
    secondary.textContent = "Windows";
    note.textContent = "Needs a Mac with Apple Silicon (M1 or newer) and macOS 13+. Intel Macs aren't supported.";
    if (looksLikeIntelMac()) document.getElementById("intel-warning").hidden = false;
  } else {
    primary.href = files.windows.href;
    primary.textContent = files.windows.label;
    secondary.href = files.mac.href;
    secondary.textContent = files.mac.label;
    note.textContent = os === "mobile"
      ? "Tiro is a desktop app for Windows and Mac (Apple Silicon). Open this page on your computer to download it."
      : "Tiro runs on Windows 10/11 and on Macs with Apple Silicon. Intel Macs aren't supported.";
  }

  fetch(`https://api.github.com/repos/${REPO}/releases/latest`)
    .then((r) => (r.ok ? r.json() : null))
    .then((rel) => {
      if (!rel || !rel.tag_name) return;
      const line = document.getElementById("version-line");
      const date = rel.published_at ? new Date(rel.published_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }) : "";
      line.textContent = `Latest version ${rel.tag_name.replace(/^v/, "")}${date ? " · " + date : ""} · free and open source (MIT)`;
    })
    .catch(() => {});
})();
