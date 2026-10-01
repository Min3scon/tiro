import ctypes, sys
sys.path.insert(0, r"D:\dictation")
from tiro import winutil
h = 0x202b0
u = ctypes.WinDLL("user32")
print("exists:", bool(u.IsWindow(h)), "class:", winutil.window_class(h))
buf = ctypes.create_unicode_buffer(512); u.GetWindowTextW(h, buf, 512); print("title:", buf.value)
pid = winutil.window_pid(h); print("process:", winutil.process_name(pid))
