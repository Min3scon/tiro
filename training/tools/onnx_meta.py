"""Print ONNX/ORT model metadata, inputs and outputs (without running it)."""
import sys

import onnxruntime as ort

for path in sys.argv[1:]:
    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
    meta = sess.get_modelmeta()
    print("==", path)
    print("  custom:", dict(meta.custom_metadata_map))
    for i in sess.get_inputs():
        print("  in ", i.name, i.type, i.shape)
    for o in sess.get_outputs():
        print("  out", o.name, o.type, o.shape)
