"""Summarise ONNX Runtime profiler JSON files: total time per operator type and the slowest nodes."""
import collections
import json
import sys

for path in sys.argv[1:]:
    events = json.load(open(path, encoding="utf-8"))
    by_op = collections.Counter()
    by_node = collections.Counter()
    runs = 0
    for e in events:
        if e.get("cat") == "Session" and e.get("name") == "model_run":
            runs += 1
        if e.get("cat") != "Node" or not e.get("name", "").endswith("_kernel_time"):
            continue
        op = e.get("args", {}).get("op_name", "?")
        by_op[op] += e["dur"]
        by_node[e["name"].replace("_kernel_time", "")] += e["dur"]
    total = sum(by_op.values())
    if not total:
        continue
    print(f"== {path}: runs={runs} kernel time {total / 1000:.1f} ms")
    for op, us in by_op.most_common(8):
        print(f"   {op:28s} {us / 1000:9.1f} ms  {100 * us / total:5.1f}%")
    for node, us in by_node.most_common(5):
        print(f"      {node[:70]:70s} {us / 1000:8.1f} ms")
