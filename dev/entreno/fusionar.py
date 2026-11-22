"""Funde el adaptador LoRA entrenado con los pesos ORIGINALES de Qwen3.5 (misma estructura, visión incluida),
para que Ollama lo importe igual que el modelo oficial.

    python dev/entreno/fusionar.py --base <carpeta del modelo de Hugging Face> --lora dev/entreno/salida/lora
           --out dev/entreno/salida/fusionado
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

import torch
from safetensors.torch import load_file, save_file


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="carpeta local del modelo original (snapshot de Hugging Face)")
    ap.add_argument("--lora", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cfg = json.load(open(os.path.join(a.lora, "adapter_config.json"), encoding="utf-8"))
    scale = cfg["lora_alpha"] / cfg["r"]
    lora = load_file(os.path.join(a.lora, "adapter_model.safetensors"))
    deltas: dict[str, tuple] = {}
    for k, v in lora.items():
        if ".lora_A." not in k:
            continue
        mod = k.split(".lora_A.")[0].removeprefix("base_model.model.")
        b = lora[k.replace(".lora_A.", ".lora_B.")]
        target = mod.replace("model.layers.", "model.language_model.layers.", 1) + ".weight"
        deltas[target] = (v, b)
    os.makedirs(a.out, exist_ok=True)
    for f in os.listdir(a.base):
        src = os.path.join(a.base, f)
        if os.path.isfile(src) and not f.endswith(".safetensors"):
            shutil.copy2(src, os.path.join(a.out, f))
    done = 0
    for f in sorted(x for x in os.listdir(a.base) if x.endswith(".safetensors")):
        sd = load_file(os.path.join(a.base, f))
        for k in list(sd):
            if k in deltas:
                A, B = deltas[k]
                w = sd[k]
                sd[k] = (w.float() + scale * (B.float() @ A.float())).to(w.dtype)
                done += 1
        save_file(sd, os.path.join(a.out, f), metadata={"format": "pt"})
        print("fusionado", f)
    missing = len(deltas) - done
    print(f"{done} matrices actualizadas" + (f"; ¡{missing} sin encontrar!" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
