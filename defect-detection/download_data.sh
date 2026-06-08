#!/bin/bash
# Download Devign dataset from CodeXGLUE
set -e

DATA_DIR="./data"
mkdir -p "$DATA_DIR"

BASE_URL="https://raw.githubusercontent.com/microsoft/CodeXGLUE/main/Code-Code/Defect-detection/dataset"

echo "Downloading train.jsonl..."
curl -L "$BASE_URL/train.jsonl" -o "$DATA_DIR/train.jsonl"

echo "Downloading valid.jsonl..."
curl -L "$BASE_URL/valid.jsonl" -o "$DATA_DIR/valid.jsonl"

echo "Downloading test.jsonl..."
curl -L "$BASE_URL/test.jsonl" -o "$DATA_DIR/test.jsonl"

echo "Done. Files saved to $DATA_DIR/"
wc -l "$DATA_DIR"/*.jsonl
