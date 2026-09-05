"""Bounded execution check of the unmodified upstream Video2BEV model.

Random initialization and synthetic pairs qualify execution only, not training
reproduction, checkpoint compatibility, geographic accuracy or navigation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace

from .video2bev_workspace import _sha, validate
from .video2bev_loss import load_matching_loss


def _tensor_record(tensor):
    value = tensor.detach().cpu().contiguous()
    return {"shape": list(value.shape), "dtype": str(value.dtype),
            "sha256": hashlib.sha256(value.numpy().tobytes()).hexdigest()}


def _parameters_digest(module):
    digest = hashlib.sha256()
    for name, tensor in module.state_dict().items():
        record = _tensor_record(tensor)
        digest.update(json.dumps([name, record], sort_keys=True).encode())
    return digest.hexdigest()


def run(workspace: Path, *, threads: int = 2, seed: int = 0) -> dict:
    if type(threads) is not int or not 1 <= threads <= 16:
        raise ValueError("threads must be an integer from 1 to 16")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be an unsigned 32-bit integer")
    workspace = workspace.resolve()
    manifest = validate(workspace)
    if any(name == "backbone" or name.startswith("backbone.")
           or name == "_video2bev_smoke_model" for name in sys.modules):
        raise ValueError("Run in a fresh process without an existing backbone package")

    import torch
    import torch.nn.functional as functional

    previous_threads = torch.get_num_threads()
    before_modules = set(sys.modules)
    started = time.monotonic()
    try:
        package = ModuleType("backbone")
        package.__path__ = [str(workspace / "backbone")]
        sys.modules["backbone"] = package
        spec = importlib.util.spec_from_file_location("_video2bev_smoke_model", workspace / "model.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        torch.set_num_threads(threads)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(seed)
            # This upstream branch has no pretrained=True call or checkpoint read.
            model = module.two_view_net(
                class_num=2, droprate=0.5, share_weight=True, vit=True,
                itc=True, itm=True, itm_share=True, lpn=True,
                two_stage_training=True, backbone_name="vit-small",
            ).cpu()
            if model.model_1 is not model.model_3:
                raise ValueError("Expected upstream shared encoder")
            model.model_1.requires_grad_(False)
            model.train()
            # The real second-stage trainer calls set_eval after model.train().
            model.model_1.eval()
            trainable = {name: parameter for name, parameter in model.named_parameters()
                         if parameter.requires_grad}
            if not trainable or any(not name.startswith(("model_3_fusion.", "itm_head_3."))
                                    for name in trainable):
                raise ValueError("Unexpected second-stage trainable parameters")
            encoder_before = _parameters_digest(model.model_1)
            head_before = _parameters_digest(model.itm_head_3)
            fusion_before = _parameters_digest(model.model_3_fusion)
            input_views = {name: torch.randn(2, 3, 256, 256) for name in (
                "satellite", "bev", "satellite_negative", "bev_negative",
                "drone_anchor_satellite", "drone_anchor_bev",
                "drone_anchor_satellite_negative", "drone_anchor_bev_negative")}
            outputs = []
            with torch.no_grad():
                values = list(input_views.values())
                for index in range(0, len(values), 2):
                    outputs.append(model(values[index], None, values[index + 1]))
            logits_by_anchor = []
            hook = model.itm_head_3.register_forward_hook(
                lambda _module, _args, output: logits_by_anchor.append(output.detach().clone()))
            labels = torch.tensor([0, 1], dtype=torch.long)
            def group(output, index):
                return [output[0][index], output[1][index]]
            try:
                with tempfile.TemporaryDirectory(prefix="video2bev-loss-") as temporary:
                    log_directory = Path(temporary) / "logs"
                    matching_loss, loss_provenance = load_matching_loss(
                        workspace, log_directory, ["0001", "0002"])
                    loss = matching_loss(
                        seq_feature_group=[group(outputs[0], 2), group(outputs[2], 2)],
                        feat_groups=[group(outputs[0], 1), group(outputs[2], 1)],
                        seq_feature_group_neg=[group(outputs[1], 2), group(outputs[3], 2)],
                        feat_groups_neg=[group(outputs[1], 1), group(outputs[3], 1)],
                        temper_parameter=None, model=model,
                        opt=SimpleNamespace(sd_negative_sample=True, fusion_cls_loss=False,
                                            matching_label_smooth=False),
                        satellite_based_label=labels, drone_based_label=labels,
                        sample_number=1, class_names=["0001", "0002"], epoch=0, iteration=0)
                    sampling_log = (log_directory / "neagtive_samples/000000/neg_sample.txt").read_text()
            finally:
                hook.remove()
            if len(logits_by_anchor) != 2 or any(value.shape != (10, 2) for value in logits_by_anchor):
                raise ValueError("Unexpected upstream two-anchor matching output")
            logits = torch.stack(logits_by_anchor)
            expected_labels = torch.tensor([1, 1] + [0] * 8, dtype=torch.long)
            independent_loss = sum(functional.cross_entropy(value, expected_labels)
                                   for value in logits_by_anchor) / 2
            if not torch.allclose(loss.detach(), independent_loss, atol=1e-7, rtol=1e-6):
                raise ValueError("Upstream loss differs from two-anchor matching cross-entropy")
            evidence_tensors = {**input_views, "satellite_tokens": outputs[0][0][2],
                                "bev_tokens": outputs[0][1][2], "logits": logits,
                                "labels_per_anchor": expected_labels}
            if any(not torch.isfinite(value).all() for value in evidence_tensors.values()):
                raise ValueError("Nonfinite or unexpected upstream model output")
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite pair loss")
            loss.backward()
            for name, parameter in trainable.items():
                if parameter.grad is None or not torch.isfinite(parameter.grad).all():
                    raise ValueError(f"Missing or nonfinite gradient: {name}")
            if any(parameter.grad is not None for parameter in model.model_1.parameters()):
                raise ValueError("Frozen encoder accumulated a gradient")
            gradient_norm = torch.nn.utils.clip_grad_norm_(
                list(trainable.values()), max_norm=1.0, error_if_nonfinite=True)
            if float(gradient_norm) <= 0:
                raise ValueError("Zero matching gradient")
            optimizer = torch.optim.AdamW(list(trainable.values()), lr=1e-4, weight_decay=0.01)
            optimizer.step()
            if any(not torch.isfinite(parameter).all() for parameter in model.parameters()):
                raise ValueError("Optimizer produced nonfinite parameters")
            encoder_after = _parameters_digest(model.model_1)
            head_after = _parameters_digest(model.itm_head_3)
            fusion_after = _parameters_digest(model.model_3_fusion)
            if encoder_before != encoder_after or head_before == head_after or fusion_before == fusion_after:
                raise ValueError("Encoder changed or matching modules did not update")
            result = {
                "schema_version": "vision_nav_video2bev_model_smoke_v1",
                "status": "passed", "scope": "random_init_upstream_matching_forward_backward",
                "source_revision": manifest["source_revision"],
                "workspace_manifest_sha256": _sha(workspace / "workspace.json"),
                "model_source_sha256": _sha(workspace / "model.py"),
                "backbone_source_sha256": _sha(workspace / "backbone/vit_pytorch.py"),
                "runner_sha256": _sha(Path(__file__)), "torch_version": str(torch.__version__),
                "device": "cpu", "threads": threads, "seed": seed,
                "backbone": "vit-small", "initialization": "random",
                "input_kind": "synthetic_random_tensors", "checkpoint_loaded": False,
                "parameters": sum(p.numel() for p in model.parameters()),
                "trainable_parameters": sum(p.numel() for p in trainable.values()),
                "encoder_unchanged": True, "matching_head_updated": True,
                "fusion_updated": True, "encoder_training_mode": model.model_1.training,
                "loss": float(loss.detach()), "gradient_norm_before_clipping": float(gradient_norm),
                "loss_provenance": loss_provenance, "negative_sampling_log": sampling_log,
                "upstream_matching_loss_executed": True, "synthetic_negatives_enabled": True,
                "negative_samples_per_query": 1, "anchor_directions": 2,
                "positive_pairs_per_anchor": 2, "negative_pairs_per_anchor": 8,
                "optimizer": {"name": "AdamW", "steps": 1, "lr": 1e-4, "weight_decay": 0.01},
                "tensors": {name: _tensor_record(value) for name, value in evidence_tensors.items()},
                "encoder_state_sha256": encoder_after,
                "matching_head_state_sha256": head_after, "fusion_state_sha256": fusion_after,
                "official_training_reproduced": False, "default_swin_base_verified": False,
                "checkpoint_compatibility_verified": False, "geographic_accuracy_verified": False,
                "position_authority": False, "px4_external_vision": "disabled",
                "elapsed_s": time.monotonic() - started,
            }
        return result
    finally:
        torch.set_num_threads(previous_threads)
        for name in set(sys.modules) - before_modules:
            if name == "backbone" or name.startswith("backbone.") or name == "_video2bev_smoke_model":
                del sys.modules[name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(args.workspace.resolve()):
        parser.error("Output must be new and outside the prepared workspace")
    report = run(args.workspace, threads=args.threads, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "scope": report["scope"]}))


if __name__ == "__main__":
    main()
