import argparse
import json
import os
import random
import signal
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from torchvision.models import convnext_tiny, convnext_base
from torchvision.models import ConvNeXt_Tiny_Weights, ConvNeXt_Base_Weights
from PIL import Image

from torch.optim.lr_scheduler import StepLR


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def set_seed(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def find_image_txt_pairs(
    root: str, subfolders: Optional[List[str]] = None
) -> List[Tuple[str, str]]:
    pairs = []
    folder_paths = []
    if subfolders:
        for subfolder in subfolders:
            folder_path = os.path.join(root, subfolder)
            if os.path.isdir(folder_path):
                folder_paths.append(folder_path)
    else:
        folder_paths.append(root)

    for folder_path in folder_paths:
        for dirpath, _, filenames in os.walk(folder_path):
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if ext not in IMAGE_EXTS:
                    continue
                img_path = os.path.join(dirpath, name)
                txt_path = os.path.splitext(img_path)[0] + ".txt"
                if os.path.exists(txt_path):
                    pairs.append((img_path, txt_path))
    return pairs


def parse_tags(txt_path: str) -> List[str]:
    raw = open(txt_path, "r", encoding="utf-8").read()
    tags = [t.strip().lower() for t in raw.split(",") if t.strip()]
    return tags


@dataclass
class Sample:
    img_path: str
    tags: List[str]


class TagDataset(Dataset):
    def __init__(self, samples: List[Sample], label_to_idx: dict, transform=None):
        self.samples = samples
        self.label_to_idx = label_to_idx
        self.transform = transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        image = Image.open(sample.img_path).convert("RGB")
        if self.transform:
            image = self.transform(image)
        target = torch.zeros(len(self.label_to_idx), dtype=torch.float32)
        for tag in sample.tags:
            if tag in self.label_to_idx:
                target[self.label_to_idx[tag]] = 1.0
        return image, target


class TagDatasetWithPaths(Dataset):
    def __init__(self, samples: List[Sample], label_to_idx: dict, transform=None):
        self.samples = samples
        self.label_to_idx = label_to_idx
        self.transform = transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        image = Image.open(sample.img_path).convert("RGB")
        if self.transform:
            image = self.transform(image)
        target = torch.zeros(len(self.label_to_idx), dtype=torch.float32)
        for tag in sample.tags:
            if tag in self.label_to_idx:
                target[self.label_to_idx[tag]] = 1.0
        return image, target, sample.img_path, sample.tags


def collate_with_paths(batch):
    images, targets, paths, expected_tags = zip(*batch)
    return (
        torch.stack(images),
        torch.stack(targets),
        list(paths),
        list(expected_tags),
    )


def build_label_vocab(samples: List[Sample]) -> List[str]:
    labels = set()
    for s in samples:
        labels.update(s.tags)
    return sorted(labels)


def split_samples(samples: List[Sample], val_ratio: float, test_ratio: float, seed: int):
    set_seed(seed)
    shuffled = samples[:]
    random.shuffle(shuffled)
    n = len(shuffled)
    n_test = int(n * test_ratio)
    n_val = int(n * val_ratio)
    test = shuffled[:n_test]
    val = shuffled[n_test : n_test + n_val]
    train = shuffled[n_test + n_val :]
    return train, val, test


def build_model(arch: str, num_labels: int, pretrained: bool):
    if arch == "convnext_tiny":
        weights = ConvNeXt_Tiny_Weights.DEFAULT if pretrained else None
        model = convnext_tiny(weights=weights)
        in_features = model.classifier[2].in_features
        model.classifier[2] = nn.Linear(in_features, num_labels)
    elif arch == "convnext_base":
        weights = ConvNeXt_Base_Weights.DEFAULT if pretrained else None
        model = convnext_base(weights=weights)
        in_features = model.classifier[2].in_features
        model.classifier[2] = nn.Linear(in_features, num_labels)
    else:
        raise ValueError(f"Unsupported arch: {arch}")
    return model


def evaluate(model, loader, device, threshold=0.5):
    model.eval()
    total = 0
    correct = 0
    tp = 0
    fp = 0
    fn = 0
    with torch.no_grad():
        for images, targets in loader:
            images = images.to(device)
            targets = targets.to(device)
            logits = model(images)
            probs = torch.sigmoid(logits)
            preds = (probs >= threshold).float()
            total += targets.numel()
            correct += (preds == targets).sum().item()
            tp += ((preds == 1) & (targets == 1)).sum().item()
            fp += ((preds == 1) & (targets == 0)).sum().item()
            fn += ((preds == 0) & (targets == 1)).sum().item()
    acc = correct / max(1, total)
    precision = tp / max(1, (tp + fp))
    recall = tp / max(1, (tp + fn))
    f1 = 2 * precision * recall / max(1e-8, (precision + recall))
    return {"acc": acc, "precision": precision, "recall": recall, "f1": f1}


def evaluate_per_class(model, loader, device, threshold=0.5):
    model.eval()
    tp = None
    fp = None
    fn = None
    support = None
    with torch.no_grad():
        for images, targets in loader:
            images = images.to(device)
            targets = targets.to(device)
            logits = model(images)
            probs = torch.sigmoid(logits)
            preds = (probs >= threshold).float()

            if tp is None:
                num_labels = targets.shape[1]
                tp = torch.zeros(num_labels, device=targets.device)
                fp = torch.zeros(num_labels, device=targets.device)
                fn = torch.zeros(num_labels, device=targets.device)
                support = torch.zeros(num_labels, device=targets.device)

            tp += ((preds == 1) & (targets == 1)).sum(dim=0)
            fp += ((preds == 1) & (targets == 0)).sum(dim=0)
            fn += ((preds == 0) & (targets == 1)).sum(dim=0)
            support += (targets == 1).sum(dim=0)

    precision = tp / torch.clamp(tp + fp, min=1)
    recall = tp / torch.clamp(tp + fn, min=1)
    f1 = 2 * precision * recall / torch.clamp(precision + recall, min=1e-8)
    return {
        "precision": precision.cpu().tolist(),
        "recall": recall.cpu().tolist(),
        "f1": f1.cpu().tolist(),
        "support": support.cpu().tolist(),
    }


def predict_and_report(
    model,
    samples: List[Sample],
    label_to_idx: dict,
    labels: List[str],
    transform,
    device: str,
    out_path: str,
    threshold: float = 0.5,
    max_samples: int = 0,
):
    model.eval()
    if max_samples and max_samples > 0:
        samples = samples[:max_samples]
    dataset = TagDatasetWithPaths(samples, label_to_idx, transform)
    loader = DataLoader(
        dataset,
        batch_size=32,
        shuffle=False,
        num_workers=4,
        pin_memory=True,
        collate_fn=collate_with_paths,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("image_path\texpected\tpredicted\tmissing\textra\n")
        with torch.no_grad():
            for images, targets, paths, expected_tags in loader:
                images = images.to(device)
                logits = model(images)
                probs = torch.sigmoid(logits).cpu()
                preds = (probs >= threshold)
                for i in range(len(paths)):
                    expected = set(t.strip().lower() for t in expected_tags[i])
                    predicted = {
                        labels[j]
                        for j, flag in enumerate(preds[i].tolist())
                        if flag
                    }
                    missing = expected - predicted
                    extra = predicted - expected
                    f.write(
                        f"{paths[i]}\t"
                        f"{', '.join(sorted(expected))}\t"
                        f"{', '.join(sorted(predicted))}\t"
                        f"{', '.join(sorted(missing))}\t"
                        f"{', '.join(sorted(extra))}\n"
                    )


def main():
    parser = argparse.ArgumentParser(
        description="Fine-tune a multi-label tagger using image+txt pairs."
    )
    parser.add_argument("--data", required=True, help="Dataset root folder")
    parser.add_argument(
        "--folders",
        nargs="*",
        default=None,
        help="Optional list of subfolders under --data to scan. If omitted, scans --data recursively.",
    )
    parser.add_argument("--out", default="runs/tagger", help="Output folder")
    parser.add_argument("--arch", default="convnext_base", choices=["convnext_tiny", "convnext_base"])
    parser.add_argument("--image-size", type=int, default=448)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--lr-patience", type=int, default=2)
    parser.add_argument("--lr-extend-epochs", type=int, default=2)
    parser.add_argument("--lr-extend-max", type=int, default=8)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--freeze-epochs", type=int, default=2)
    parser.add_argument("--no-pretrained", action="store_true")
    parser.add_argument("--predict-train", action="store_true")
    parser.add_argument("--predict-threshold", type=float, default=0.5)
    parser.add_argument("--predict-max", type=int, default=0)
    parser.add_argument("--per-class-metrics", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    pairs = find_image_txt_pairs(args.data, args.folders)
    if not pairs:
        raise SystemExit("No image+txt pairs found.")

    samples = []
    for img_path, txt_path in pairs:
        tags = parse_tags(txt_path)
        samples.append(Sample(img_path=img_path, tags=tags))

    labels = build_label_vocab(samples)
    label_to_idx = {label: i for i, label in enumerate(labels)}

    with open(os.path.join(args.out, "labels.json"), "w", encoding="utf-8") as f:
        json.dump(labels, f, indent=2)

    train_samples, val_samples, test_samples = split_samples(
        samples, args.val_ratio, args.test_ratio, args.seed
    )

    transform = transforms.Compose(
        [
            transforms.Resize((args.image_size, args.image_size)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ]
    )

    train_ds = TagDataset(train_samples, label_to_idx, transform)
    val_ds = TagDataset(val_samples, label_to_idx, transform)
    test_ds = TagDataset(test_samples, label_to_idx, transform)

    train_loader = DataLoader(train_ds, batch_size=args.batch, shuffle=True, num_workers=4, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch, shuffle=False, num_workers=4, pin_memory=True)
    test_loader = DataLoader(test_ds, batch_size=args.batch, shuffle=False, num_workers=4, pin_memory=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.backends.cudnn.benchmark = False
    model = build_model(args.arch, len(labels), pretrained=not args.no_pretrained)
    model.to(device)

    if device == "cuda" and len(train_loader) > 0:
        warmup_images, _ = next(iter(train_loader))
        warmup_images = warmup_images[:1].to(device)
        with torch.no_grad():
            _ = model(warmup_images)
        torch.cuda.synchronize()

    criterion = nn.BCEWithLogitsLoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scaler = torch.cuda.amp.GradScaler(enabled=device == "cuda")
    base_lr = args.lr
    min_lr = base_lr * 0.03125
    plateau_count = 0

    # Add StepLR scheduler
    scheduler = StepLR(optimizer, step_size=10, gamma=0.7)

    best_f1 = -1.0
    stop_requested = {"flag": False}

    def handle_sigint(signum, frame):
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        total_epochs = args.epochs
        extra_epochs_used = 0
        epoch = 1
        while epoch <= total_epochs:
            model.train()
            if epoch <= args.freeze_epochs:
                for name, param in model.features.named_parameters():
                    param.requires_grad = False
            else:
                for param in model.features.parameters():
                    param.requires_grad = True

            running_loss = 0.0
            start = time.time()
            for images, targets in train_loader:
                images = images.to(device)
                targets = targets.to(device)
                optimizer.zero_grad(set_to_none=True)
                with torch.cuda.amp.autocast(enabled=device == "cuda"):
                    logits = model(images)
                    loss = criterion(logits, targets)
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
                running_loss += loss.item()
                if stop_requested["flag"]:
                    raise KeyboardInterrupt

            val_metrics = evaluate(model, val_loader, device)
            epoch_time = time.time() - start
            avg_loss = running_loss / max(1, len(train_loader))
            print(
                f"Epoch {epoch}/{args.epochs} - loss={avg_loss:.4f} "
                f"val_f1={val_metrics['f1']:.4f} val_p={val_metrics['precision']:.4f} "
                f"val_r={val_metrics['recall']:.4f} lr={optimizer.param_groups[0]['lr']:.6g} "
                f"({epoch_time:.1f}s)"
            )

            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "labels": labels,
                    "arch": args.arch,
                    "epoch": epoch,
                    "val_metrics": val_metrics,
                },
                os.path.join(args.out, "latest.pt"),
            )

            if val_metrics["f1"] > best_f1:
                best_f1 = val_metrics["f1"]
                plateau_count = 0
                torch.save(
                    {
                        "model_state_dict": model.state_dict(),
                        "labels": labels,
                        "arch": args.arch,
                        "epoch": epoch,
                        "val_metrics": val_metrics,
                    },
                    os.path.join(args.out, "best.pt"),
                )
            else:
                plateau_count += 1
                if plateau_count >= args.lr_patience:
                    current_lr = optimizer.param_groups[0]["lr"]
                    if current_lr > min_lr:
                        new_lr = max(min_lr, current_lr * 0.5)
                        for group in optimizer.param_groups:
                            group["lr"] = new_lr
                        print(
                            f"Plateau detected. Reducing LR to {new_lr:.6g}."
                        )
                        if extra_epochs_used < args.lr_extend_max:
                            add_epochs = min(
                                args.lr_extend_epochs,
                                args.lr_extend_max - extra_epochs_used,
                            )
                            total_epochs += add_epochs
                            extra_epochs_used += add_epochs
                            print(
                                f"Extending training by {add_epochs} epochs (total={total_epochs})."
                            )
                    plateau_count = 0

            epoch += 1

            # Step the scheduler at the end of each epoch
            scheduler.step()

        test_metrics = evaluate(model, test_loader, device)
        print("Test metrics:", test_metrics)
        if args.per_class_metrics:
            val_per_class = evaluate_per_class(
                model, val_loader, device, threshold=args.predict_threshold
            )
            test_per_class = evaluate_per_class(
                model, test_loader, device, threshold=args.predict_threshold
            )

            def write_per_class(path, metrics):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("tag\tprecision\trecall\tf1\tsupport\n")
                    for i, tag in enumerate(labels):
                        f.write(
                            f"{tag}\t"
                            f"{metrics['precision'][i]:.6f}\t"
                            f"{metrics['recall'][i]:.6f}\t"
                            f"{metrics['f1'][i]:.6f}\t"
                            f"{int(metrics['support'][i])}\n"
                        )

            write_per_class(os.path.join(args.out, "val_per_class.tsv"), val_per_class)
            write_per_class(os.path.join(args.out, "test_per_class.tsv"), test_per_class)
        torch.save(
            {
                "model_state_dict": model.state_dict(),
                "labels": labels,
                "arch": args.arch,
                "epoch": args.epochs,
                "val_metrics": {"f1": best_f1},
                "test_metrics": test_metrics,
            },
            os.path.join(args.out, "final.pt"),
        )

        if args.predict_train:
            report_path = os.path.join(args.out, "train_predictions.tsv")
            predict_and_report(
                model,
                train_samples,
                label_to_idx,
                labels,
                transform,
                device,
                report_path,
                threshold=args.predict_threshold,
                max_samples=args.predict_max,
            )
            print(f"Wrote training prediction report to {report_path}")
    except KeyboardInterrupt:
        print("Interrupted. Saving latest checkpoint...")
        torch.save(
            {
                "model_state_dict": model.state_dict(),
                "labels": labels,
                "arch": args.arch,
                "epoch": None,
            },
            os.path.join(args.out, "latest.pt"),
        )


if __name__ == "__main__":
    main()
