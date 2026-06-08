import argparse
import os
import random
import numpy as np
import torch
from torch.utils.data import DataLoader
from transformers import RobertaTokenizer, get_linear_schedule_with_warmup
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from tqdm import tqdm

from dataset import DefectDataset
from model import CodeBERTDefectDetector


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def evaluate(model, loader, device):
    model.eval()
    preds, labels = [], []
    with torch.no_grad():
        for batch in loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            batch_labels = batch["label"].to(device)
            _, logits = model(input_ids, attention_mask)
            pred = logits.argmax(dim=-1).cpu().numpy()
            preds.extend(pred)
            labels.extend(batch_labels.cpu().numpy())

    acc = accuracy_score(labels, preds)
    precision, recall, f1, _ = precision_recall_fscore_support(
        labels, preds, average="binary", zero_division=0
    )
    return {"acc": acc, "precision": precision, "recall": recall, "f1": f1}


def train(args):
    set_seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    tokenizer = RobertaTokenizer.from_pretrained(args.model_name)
    print("Loading datasets...")
    train_dataset = DefectDataset(
        os.path.join(args.data_dir, "train.jsonl"), tokenizer, args.max_length
    )
    valid_dataset = DefectDataset(
        os.path.join(args.data_dir, "valid.jsonl"), tokenizer, args.max_length
    )
    test_dataset = DefectDataset(
        os.path.join(args.data_dir, "test.jsonl"), tokenizer, args.max_length
    )

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    valid_loader = DataLoader(valid_dataset, batch_size=args.batch_size)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size)

    print(f"Train: {len(train_dataset)}, Valid: {len(valid_dataset)}, Test: {len(test_dataset)}")

    model = CodeBERTDefectDetector(args.model_name).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    total_steps = len(train_loader) * args.epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=total_steps // 10, num_training_steps=total_steps
    )

    best_f1 = 0.0
    os.makedirs(args.output_dir, exist_ok=True)

    for epoch in range(args.epochs):
        model.train()
        total_loss = 0.0
        for batch in tqdm(train_loader, desc=f"Epoch {epoch+1}/{args.epochs}"):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["label"].to(device)

            optimizer.zero_grad()
            loss, _ = model(input_ids, attention_mask, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            total_loss += loss.item()

        avg_loss = total_loss / len(train_loader)
        val_metrics = evaluate(model, valid_loader, device)
        print(
            f"Epoch {epoch+1} | Loss: {avg_loss:.4f} | "
            f"Val Acc: {val_metrics['acc']:.4f} | "
            f"Val F1: {val_metrics['f1']:.4f}"
        )

        if val_metrics["f1"] > best_f1:
            best_f1 = val_metrics["f1"]
            torch.save(model.state_dict(), os.path.join(args.output_dir, "best_model.pt"))
            print(f"  => Saved best model (F1={best_f1:.4f})")

    # Evaluate on test set with best model
    print("\n--- Test Results ---")
    model.load_state_dict(torch.load(os.path.join(args.output_dir, "best_model.pt")))
    test_metrics = evaluate(model, test_loader, device)
    print(f"Accuracy:  {test_metrics['acc']:.4f}")
    print(f"Precision: {test_metrics['precision']:.4f}")
    print(f"Recall:    {test_metrics['recall']:.4f}")
    print(f"F1:        {test_metrics['f1']:.4f}")

    # Save results
    with open(os.path.join(args.output_dir, "results.txt"), "w") as f:
        f.write(f"Model: {args.model_name}\n")
        f.write(f"Accuracy:  {test_metrics['acc']:.4f}\n")
        f.write(f"Precision: {test_metrics['precision']:.4f}\n")
        f.write(f"Recall:    {test_metrics['recall']:.4f}\n")
        f.write(f"F1:        {test_metrics['f1']:.4f}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_name", default="microsoft/codebert-base")
    parser.add_argument("--data_dir", default="./data")
    parser.add_argument("--output_dir", default="./output")
    parser.add_argument("--max_length", type=int, default=512)
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    train(args)
