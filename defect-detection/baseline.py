"""
TextCNN and BiLSTM baselines for defect detection.
Usage:
  python baseline.py --model textcnn
  python baseline.py --model bilstm
"""
import argparse
import json
import os
import random
import re
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from tqdm import tqdm


# ── Tokenizer (simple word-level) ───────────────────────────────────────────

def tokenize(code, max_len=512):
    tokens = re.findall(r"[a-zA-Z_]\w*|[^\s\w]", code)
    return tokens[:max_len]


def build_vocab(file_paths, min_freq=2):
    freq = {}
    for path in file_paths:
        with open(path) as f:
            for line in f:
                for tok in tokenize(json.loads(line)["func"]):
                    freq[tok] = freq.get(tok, 0) + 1
    vocab = {"<PAD>": 0, "<UNK>": 1}
    for tok, cnt in freq.items():
        if cnt >= min_freq:
            vocab[tok] = len(vocab)
    return vocab


# ── Dataset ──────────────────────────────────────────────────────────────────

class BaselineDataset(Dataset):
    def __init__(self, file_path, vocab, max_len=512):
        self.examples = []
        with open(file_path) as f:
            for line in f:
                item = json.loads(line.strip())
                tokens = tokenize(item["func"], max_len)
                ids = [vocab.get(t, 1) for t in tokens]
                # Pad or truncate to max_len
                ids = ids[:max_len] + [0] * max(0, max_len - len(ids))
                self.examples.append((
                    torch.tensor(ids, dtype=torch.long),
                    torch.tensor(int(item["target"]), dtype=torch.long),
                ))

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, idx):
        return self.examples[idx]


# ── Models ───────────────────────────────────────────────────────────────────

class TextCNN(nn.Module):
    def __init__(self, vocab_size, embed_dim=128, num_filters=128,
                 kernel_sizes=(2, 3, 4), num_classes=2, dropout=0.5):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.convs = nn.ModuleList([
            nn.Conv1d(embed_dim, num_filters, k) for k in kernel_sizes
        ])
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(num_filters * len(kernel_sizes), num_classes)

    def forward(self, x):
        x = self.embedding(x).permute(0, 2, 1)  # (B, E, L)
        pooled = [torch.relu(conv(x)).max(dim=-1).values for conv in self.convs]
        out = torch.cat(pooled, dim=-1)
        return self.fc(self.dropout(out))


class BiLSTM(nn.Module):
    def __init__(self, vocab_size, embed_dim=128, hidden_dim=128,
                 num_layers=2, num_classes=2, dropout=0.5):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.lstm = nn.LSTM(embed_dim, hidden_dim, num_layers=num_layers,
                            batch_first=True, bidirectional=True, dropout=dropout)
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(hidden_dim * 2, num_classes)

    def forward(self, x):
        x = self.embedding(x)
        out, _ = self.lstm(x)
        out = out[:, -1, :]  # last timestep
        return self.fc(self.dropout(out))


# ── Train / Evaluate ─────────────────────────────────────────────────────────

def evaluate(model, loader, device):
    model.eval()
    preds, labels = [], []
    with torch.no_grad():
        for x, y in loader:
            logits = model(x.to(device))
            preds.extend(logits.argmax(-1).cpu().numpy())
            labels.extend(y.numpy())
    acc = accuracy_score(labels, preds)
    p, r, f1, _ = precision_recall_fscore_support(
        labels, preds, average="binary", zero_division=0
    )
    return {"acc": acc, "precision": p, "recall": r, "f1": f1}


def train(args):
    random.seed(42); np.random.seed(42); torch.manual_seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    train_path = os.path.join(args.data_dir, "train.jsonl")
    valid_path = os.path.join(args.data_dir, "valid.jsonl")
    test_path  = os.path.join(args.data_dir, "test.jsonl")

    print("Building vocabulary...")
    vocab = build_vocab([train_path])
    print(f"Vocab size: {len(vocab)}")

    train_ds = BaselineDataset(train_path, vocab)
    valid_ds = BaselineDataset(valid_path, vocab)
    test_ds  = BaselineDataset(test_path,  vocab)

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    valid_loader = DataLoader(valid_ds, batch_size=args.batch_size)
    test_loader  = DataLoader(test_ds,  batch_size=args.batch_size)

    if args.model == "textcnn":
        model = TextCNN(len(vocab)).to(device)
    else:
        model = BiLSTM(len(vocab)).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    best_f1, best_state = 0.0, None

    for epoch in range(args.epochs):
        model.train()
        total_loss = 0.0
        for x, y in tqdm(train_loader, desc=f"Epoch {epoch+1}/{args.epochs}"):
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        val = evaluate(model, valid_loader, device)
        print(f"Epoch {epoch+1} | Loss: {total_loss/len(train_loader):.4f} | "
              f"Val Acc: {val['acc']:.4f} | Val F1: {val['f1']:.4f}")

        if val["f1"] > best_f1:
            best_f1 = val["f1"]
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

    model.load_state_dict(best_state)
    test = evaluate(model, test_loader, device)
    print(f"\n--- {args.model.upper()} Test Results ---")
    print(f"Accuracy:  {test['acc']:.4f}")
    print(f"Precision: {test['precision']:.4f}")
    print(f"Recall:    {test['recall']:.4f}")
    print(f"F1:        {test['f1']:.4f}")

    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, f"results_{args.model}.txt"), "w") as f:
        f.write(f"Model: {args.model}\n")
        f.write(f"Accuracy:  {test['acc']:.4f}\n")
        f.write(f"Precision: {test['precision']:.4f}\n")
        f.write(f"Recall:    {test['recall']:.4f}\n")
        f.write(f"F1:        {test['f1']:.4f}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["textcnn", "bilstm"], default="textcnn")
    parser.add_argument("--data_dir", default="./data")
    parser.add_argument("--output_dir", default="./output")
    parser.add_argument("--batch_size", type=int, default=32)
    parser.add_argument("--epochs", type=int, default=10)
    args = parser.parse_args()
    train(args)
