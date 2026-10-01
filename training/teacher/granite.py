"""IBM Granite Speech 4.1 2B as a teacher: batched greedy transcription with per-utterance confidence.

The speech encoder and projector stay in bf16; the 1B-parameter language model can be loaded in 8-bit
(bitsandbytes LLM.int8) so the whole teacher fits next to the copy of Tiro that's already using the GPU.
`training/teacher/check_quant.py` measures that 8-bit labels match bf16 ones before any labelling run.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

PROMPT = "<|audio|>transcribe the speech with proper punctuation and capitalization."
REPO = "ibm-granite/granite-speech-4.1-2b"


@dataclass
class TeacherOut:
    text: str
    logprob: float  # mean log-probability of the generated tokens (confidence)
    min_logprob: float
    tokens: int
    hit_limit: bool  # stopped at max_new_tokens: likely a runaway/repetition, treat as low confidence


class GraniteTeacher:
    def __init__(self, repo: str = REPO, quant: str = "int8", device: str = "cuda"):
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, BitsAndBytesConfig

        self.torch = torch
        self.device = device
        self.processor = AutoProcessor.from_pretrained(repo)
        self.tok = self.processor.tokenizer
        self.tok.padding_side = "left"  # batched generation with a decoder-only LM needs left padding
        kw = dict(dtype=torch.bfloat16)
        if quant == "int8":
            kw["quantization_config"] = BitsAndBytesConfig(
                load_in_8bit=True, llm_int8_skip_modules=["encoder", "projector", "lm_head"])
            kw["device_map"] = {"": 0}
        elif quant == "bf16":
            kw["device_map"] = {"": 0} if device == "cuda" else {"": "cpu"}
        elif quant == "fp32-cpu":
            kw = dict(dtype=torch.float32, device_map={"": "cpu"})
            self.device = "cpu"
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(repo, **kw).eval()
        chat = [{"role": "user", "content": PROMPT}]
        self.prompt = self.tok.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)

    def transcribe(self, audios: list[np.ndarray], max_new_tokens: int | None = None) -> list[TeacherOut]:
        torch = self.torch
        longest = max(len(a) for a in audios) / 16000
        # ~4 tokens per second of speech is generous for English; never cut a real transcript short
        limit = max_new_tokens or int(longest * 6) + 24
        wavs = [torch.from_numpy(np.ascontiguousarray(a, dtype=np.float32)) for a in audios]
        inputs = self.processor([self.prompt] * len(wavs), wavs, device=self.device, return_tensors="pt")
        inputs = inputs.to(self.device)
        if "input_features" in inputs:
            inputs["input_features"] = inputs["input_features"].to(torch.bfloat16 if self.device == "cuda"
                                                                   else self.model.dtype)
        with torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=limit, do_sample=False, num_beams=1,
                                      output_scores=True, return_dict_in_generate=True)
        start = inputs["input_ids"].shape[-1]
        seqs = out.sequences[:, start:]
        # per-step log-probabilities of the chosen tokens
        steps = torch.stack([torch.log_softmax(s.float(), dim=-1) for s in out.scores], dim=1)  # [B, T, V]
        chosen = torch.gather(steps, 2, seqs[:, : steps.shape[1]].unsqueeze(-1)).squeeze(-1)
        eos = self.tok.eos_token_id
        pad = self.tok.pad_token_id
        results = []
        for i in range(seqs.shape[0]):
            ids = seqs[i].tolist()
            n = len(ids)
            for j, t in enumerate(ids):
                if t in (eos, pad):
                    n = j
                    break
            lp = chosen[i, :n].tolist() if n else [0.0]
            text = self.tok.decode(ids[:n], skip_special_tokens=True).strip()
            results.append(TeacherOut(text=text, logprob=float(np.mean(lp)), min_logprob=float(min(lp)),
                                      tokens=n, hit_limit=n >= limit))
        return results

    def vram_gb(self) -> float:
        t = self.torch
        return t.cuda.max_memory_allocated() / 2**30 if t.cuda.is_available() else 0.0


def fmt(o: TeacherOut) -> str:
    return f"{o.text}  [lp={o.logprob:.3f} min={o.min_logprob:.2f} n={o.tokens}{' LIMIT' if o.hit_limit else ''}]"


if __name__ == "__main__":
    import sys
    import time

    from training.eval import sets

    quant = sys.argv[1] if len(sys.argv) > 1 else "int8"
    t0 = time.time()
    teacher = GraniteTeacher(quant=quant)
    print(f"loaded {quant} in {time.time() - t0:.1f}s, VRAM {teacher.vram_gb():.2f} GB", flush=True)
    utts = list(sets.utterances("dev-ls-other", limit=16))
    t0 = time.time()
    outs = teacher.transcribe([u.audio for u in utts])
    dt = time.time() - t0
    audio = sum(u.duration for u in utts)
    for u, o in zip(utts, outs):
        print("REF:", u.text)
        print("TCH:", fmt(o))
    print(f"batch of {len(utts)} ({audio:.0f}s audio) in {dt:.1f}s -> RTFx {audio / dt:.1f}; "
          f"peak VRAM {teacher.vram_gb():.2f} GB", flush=True)
    _ = math
