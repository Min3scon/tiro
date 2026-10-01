"""Quantise a Moonshine Streaming ONNX export (frontend, encoder, adapter, cross_kv, decoder_kv) for CPUs.

    python -m training.export.quantize_moonshine SRC_FP32_DIR OUT_DIR --recipe q8
    recipes:
      q8   encoder + cross_kv: dynamic int8 (per-channel weights); decoder_kv: 8-bit block weights (MatMulNBits)
      q4   as q8, but the decoder's weights are 4-bit blocks (smaller and faster per token, slightly less accurate)
      q4e  as q4, and the encoder's weights are 4-bit blocks too (smallest)
      f32  no quantisation (reference)

Why these choices: the decoder runs once per output token with a single row of input, so its cost is reading the
weights; MatMulNBits keeps them packed and dequantises inside the kernel. The encoder processes many frames per
call, where int8 GEMM with dynamically quantised activations is the fastest CPU path. The frontend and adapter
stay fp32 (small, and the frontend's convolutions are sensitive to quantisation).
"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

import onnx

GRAPHS = ("frontend", "encoder", "adapter", "cross_kv", "decoder_kv")


def untie_lm_head(model: onnx.ModelProto) -> int:
    """The output layer reuses the token embedding through MatMul(x, Transpose(embedding)). A transposed copy as
    its own initializer lets the weight-only quantiser pack it (it only handles constant MatMul weights), and the
    fp32 embedding is then only read by the Gather, which gets its own 4-bit copy."""
    import numpy as np
    from onnx import numpy_helper

    g = model.graph
    inits = {i.name: i for i in g.initializer}
    producer = {o: n for n in g.node for o in n.output}
    changed = 0
    for n in list(g.node):
        if n.op_type != "MatMul" or n.input[1] not in producer:
            continue
        t = producer[n.input[1]]
        if t.op_type != "Transpose" or t.input[0] not in inits:
            continue
        w = numpy_helper.to_array(inits[t.input[0]])
        perm = next((list(a.ints) for a in t.attribute if a.name == "perm"), None)
        wt = np.ascontiguousarray(np.transpose(w, perm) if perm else w.T)
        name = t.input[0] + ".transposed"
        g.initializer.append(numpy_helper.from_array(wt, name))
        n.input[1] = name
        changed += 1
    used = {i for n in g.node for i in n.input}
    for n in list(g.node):
        if n.op_type == "Transpose" and not any(o in used for o in n.output):
            g.node.remove(n)
    return changed


def nbits(src: Path, dst: Path, bits: int, block: int = 32, gather: bool = True, accuracy: int = 4) -> None:
    """Weight-only block quantisation (MatMulNBits / GatherBlockQuantized). accuracy_level 4 computes with int8
    activations inside the kernel, the fast path on x86 (AVX2/AVX-512 VNNI) and ARM (NEON dot product)."""
    from onnxruntime.quantization import matmul_nbits_quantizer as mnb

    model = onnx.load(str(src))
    print(f"   untied {untie_lm_head(model)} output layer(s)")
    passes = [(bits, ("MatMul",))]
    if gather:
        passes.append((4, ("Gather",)))  # GatherBlockQuantized only exists for 4 bits; lookups barely notice
    for b, ops in passes:
        cfg = mnb.DefaultWeightOnlyQuantConfig(block_size=block, is_symmetric=True, bits=b, accuracy_level=accuracy,
                                               op_types_to_quantize=ops, quant_axes=(("MatMul", 0), ("Gather", 1)))
        q = mnb.MatMulNBitsQuantizer(model, algo_config=cfg)
        q.process()
        model = q.model.model
    onnx.save(model, str(dst))


def dynamic_int8(src: Path, dst: Path) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(str(src), str(dst), weight_type=QuantType.QInt8, per_channel=True,
                     op_types_to_quantize=["MatMul", "Gemm"], extra_options={"MatMulConstBOnly": True})


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("src", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--recipe", default="q8", choices=["q8", "q4", "q4e", "f32"])
    p.add_argument("--block", type=int, default=32)
    p.add_argument("--accuracy", type=int, default=4, help="MatMulNBits accuracy_level (4 = int8 compute)")
    p.add_argument("--no-gather", action="store_true", help="keep the embedding lookup in fp32")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for extra in ("streaming_config.json", "tokenizer.bin"):
        shutil.copy2(a.src / extra, a.out / extra)
    for g in GRAPHS:
        src, dst = a.src / f"{g}.onnx", a.out / f"{g}.onnx"
        t0 = time.time()
        if a.recipe == "f32" or g in ("frontend", "adapter"):
            shutil.copy2(src, dst)
        elif g == "decoder_kv":
            nbits(src, dst, bits=8 if a.recipe == "q8" else 4, block=a.block, gather=not a.no_gather,
                  accuracy=a.accuracy)
        elif g == "encoder" and a.recipe == "q4e":
            nbits(src, dst, bits=4, block=a.block, gather=False)
        else:
            dynamic_int8(src, dst)
        print(f"{g:11s} {src.stat().st_size / 1e6:8.1f} MB -> {dst.stat().st_size / 1e6:8.1f} MB "
              f"({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()
