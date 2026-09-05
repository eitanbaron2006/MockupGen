"""Turn a PyTorch ESRGAN checkpoint into the pair realesrgan-ncnn-vulkan reads.

The upscaler on the print screen is `realesrgan-ncnn-vulkan.exe`, and it takes
an ncnn model: a `.param` describing the network and a `.bin` holding the
weights. Most models people share are neither -- they are PyTorch `.pth`
checkpoints, which is a zip of pickled tensors the binary cannot open at all.

The usual conversion is PyTorch -> ONNX -> ncnn, which needs two toolchains
this project does not have. It is also unnecessary here, because every 4x
ESRGAN model in circulation is the *same network* as realesrgan-x4plus --
RRDBNet, 64 features, 23 blocks -- trained on different data. Same shape,
different numbers. So the `.param` already sitting beside the binary describes
the new model exactly, and all that is really needed is to write its weights in
ncnn's layout.

That layout was not guessed. Adding up the declared weight sizes in the x4plus
`.param` predicts a file of 33,424,520 bytes in fp16 and 66,793,352 in fp32,
and those are byte for byte the sizes of the model files already on disk. Per
convolution, in the order the param lists them:

    4-byte tag  +  weights          (0x01306B47 = fp16, 0x00000000 = fp32)
                   biases            always raw fp32, no tag of their own

Checkpoints use two naming conventions -- the original ESRGAN one and the later
BasicSR one -- so both are read here. Anything whose shapes do not line up with
the param, layer for layer, is refused rather than written: a checkpoint of a
different architecture would otherwise convert cleanly and then upscale into
noise, which is the kind of failure nobody catches until a customer has the
file.

    python tools/convert_esrgan_to_ncnn.py <checkpoint.pth> <output-name>
"""
from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import numpy as np
import torch

MODELS = Path(__file__).resolve().parent / "realesrgan" / "models"
# The network every 4x ESRGAN checkpoint shares; used as the template.
TEMPLATE = MODELS / "realesrgan-x4plus.param"

FP16_TAG = 0x01306B47


def conv_layers(param: Path) -> list[tuple[str, int, int]]:
    """Every convolution in the param, in order: (name, weights, biases).

    The order is the point. ncnn reads the `.bin` as one stream, taking each
    layer's blobs in the order the param lists them, so the param is the index
    into the file.
    """
    layers = []
    for line in param.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Convolution"):
            continue
        fields = line.split()
        options = dict(kv.split("=", 1) for kv in fields if re.match(r"^-?\d+=", kv))
        layers.append((fields[1], int(options["6"]), int(options["0"])))
    return layers


def state_dict(checkpoint: Path) -> dict[str, torch.Tensor]:
    """The tensors, past the wrappers a checkpoint may be saved inside."""
    loaded = torch.load(checkpoint, map_location="cpu", weights_only=True)
    for key in ("params_ema", "params", "state_dict", "model"):
        if isinstance(loaded, dict) and key in loaded and isinstance(loaded[key], dict):
            loaded = loaded[key]
    return {key: value for key, value in loaded.items() if isinstance(value, torch.Tensor)}


def weight_order(weights: dict[str, torch.Tensor]) -> list[str]:
    """The convolutions in the order RRDBNet runs them.

    Both naming conventions are handled: the original ESRGAN release wrote
    `RRDB_trunk.0.RDB1.conv1` / `trunk_conv` / `upconv1` / `HRconv`, and BasicSR
    later renamed the same tensors `body.0.rdb1.conv1` / `conv_body` /
    `conv_up1` / `conv_hr`. Which one a file uses says nothing about the model.
    """
    old = "RRDB_trunk.0.RDB1.conv1.weight" in weights
    trunk, rdb = ("RRDB_trunk", "RDB") if old else ("body", "rdb")
    tail = ["trunk_conv", "upconv1", "upconv2", "HRconv"] if old else ["conv_body", "conv_up1", "conv_up2", "conv_hr"]

    blocks = {int(m.group(1)) for key in weights if (m := re.match(rf"{trunk}\.(\d+)\.", key))}
    if not blocks:
        raise SystemExit("This is not an RRDBNet checkpoint: no trunk blocks found.")

    order = ["conv_first"]
    for block in sorted(blocks):
        for dense in (1, 2, 3):
            for conv in range(1, 6):
                order.append(f"{trunk}.{block}.{rdb}{dense}.conv{conv}")
    return order + tail + ["conv_last"]


def convert(checkpoint: Path, name: str) -> Path:
    weights = state_dict(checkpoint)
    layers = conv_layers(TEMPLATE)
    order = weight_order(weights)

    if len(order) != len(layers):
        raise SystemExit(
            f"This checkpoint has {len(order)} convolutions; the network in "
            f"{TEMPLATE.name} has {len(layers)}. It is a different architecture, "
            f"and converting it against this template would produce a model that "
            f"loads and then renders noise."
        )

    # Shape check, layer by layer. The dense blocks widen 64, 96, 128, 160, 192
    # as they go, so the sequence of sizes is a fingerprint -- a checkpoint in
    # the wrong order or the wrong shape cannot match it by accident.
    planned = []
    for key, (layer, want_weights, want_biases) in zip(order, layers, strict=True):
        weight, bias = weights.get(f"{key}.weight"), weights.get(f"{key}.bias")
        if weight is None or bias is None:
            raise SystemExit(f"{key} is missing from the checkpoint (expected at {layer}).")
        if weight.numel() != want_weights or bias.numel() != want_biases:
            raise SystemExit(
                f"{key} is {weight.numel()} weights and {bias.numel()} biases; "
                f"{layer} expects {want_weights} and {want_biases}."
            )
        planned.append((weight, bias))

    destination = MODELS / f"{name}.bin"
    overflowed = 0
    with destination.open("wb") as out:
        for weight, bias in planned:
            values = weight.detach().numpy().astype(np.float32).ravel()
            # fp16 is what the GPU computes in regardless, which is why the
            # fp16 and fp32 builds of a model produce identical output. Values
            # this far outside its range would still be worth knowing about.
            overflowed += int(np.count_nonzero(np.abs(values) > 65504))
            out.write(struct.pack("<I", FP16_TAG))
            out.write(values.astype(np.float16).tobytes())
            out.write(bias.detach().numpy().astype(np.float32).ravel().tobytes())

    if overflowed:
        print(f"  warning: {overflowed} weights fell outside fp16 range")

    param = MODELS / f"{name}.param"
    param.write_bytes(TEMPLATE.read_bytes())

    expected = len(layers) * 4 + sum(layer[1] for layer in layers) * 2 + sum(layer[2] for layer in layers) * 4
    written = destination.stat().st_size
    if written != expected:
        raise SystemExit(f"Wrote {written} bytes, expected {expected}.")

    print(f"  {param.name}  ({param.stat().st_size:,} bytes)")
    print(f"  {destination.name}  ({written:,} bytes)")
    return destination


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__.strip().splitlines()[-1].strip())
    convert(Path(sys.argv[1]), sys.argv[2])
