import sys
sys.path.insert(0, r"D:\dictation")
from tiro import audio
print("tiro default_input_name():", audio.default_input_name())
print("tiro list_input_devices():", audio.list_input_devices())
import sounddevice as sd
print("sd.default.device:", sd.default.device)
for i, a in enumerate(sd.query_hostapis()):
    d = a["default_input_device"]
    print(i, a["name"], d, sd.query_devices(d)["name"] if d >= 0 else None)
