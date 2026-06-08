import json
import torch
from torch.utils.data import Dataset


class DefectDataset(Dataset):
    def __init__(self, file_path, tokenizer, max_length=512):
        self.examples = []
        with open(file_path, "r") as f:
            for line in f:
                item = json.loads(line.strip())
                code = item["func"]
                label = int(item["target"])
                encoding = tokenizer(
                    code,
                    max_length=max_length,
                    padding="max_length",
                    truncation=True,
                    return_tensors="pt",
                )
                self.examples.append({
                    "input_ids": encoding["input_ids"].squeeze(),
                    "attention_mask": encoding["attention_mask"].squeeze(),
                    "label": torch.tensor(label, dtype=torch.long),
                })

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, idx):
        return self.examples[idx]
