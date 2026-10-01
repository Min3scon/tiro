"""Show a node of an ONNX graph and where its inputs come from (initializer / producer node)."""
import sys

import onnx

path, *names = sys.argv[1:]
m = onnx.load(path, load_external_data=False)
g = m.graph
inits = {i.name: i for i in g.initializer}
producer = {o: n for n in g.node for o in n.output}
for name in names:
    for n in g.node:
        if n.name == name:
            print(f"{n.name}: {n.op_type} attrs={[a.name for a in n.attribute]}")
            for i in n.input:
                if i in inits:
                    t = inits[i]
                    print(f"   in {i}: initializer {list(t.dims)} type={t.data_type}")
                elif i in producer:
                    p = producer[i]
                    print(f"   in {i}: from {p.op_type} {p.name} (inputs {list(p.input)[:3]})")
                else:
                    print(f"   in {i}: graph input")
ops = {}
for n in g.node:
    ops[n.op_type] = ops.get(n.op_type, 0) + 1
print("op counts:", dict(sorted(ops.items(), key=lambda kv: -kv[1])[:12]))
big = sorted(g.initializer, key=lambda t: -int(__import__("numpy").prod(t.dims)))[:6]
for t in big:
    print("big initializer", t.name, list(t.dims), "type", t.data_type)
