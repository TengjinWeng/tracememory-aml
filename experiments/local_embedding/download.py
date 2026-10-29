"""Download only public, pinned model artifacts into this isolated project."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

from assets import download

MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    target = ROOT / "models-embedding/qwen3-0.6b"
    target.mkdir(parents=True, exist_ok=True)
    api = f"https://huggingface.co/api/models/{MODEL_ID}/revision/{REVISION}?blobs=true"
    with urlopen(api, timeout=30) as response:
        metadata = json.load(response)
    if metadata.get("sha") != REVISION:
        raise RuntimeError("model revision mismatch")
    allowed = {"config.json", "tokenizer_config.json", "tokenizer.json", "vocab.json", "merges.txt",
               "special_tokens_map.json", "added_tokens.json", "README.md", "LICENSE",
               "generation_config.json", "config_sentence_transformers.json", "modules.json"}

    def small_file(item):
        name, size = item["rfilename"], item["size"]
        path = target / name
        def valid(data):
            if len(data) != size:
                return False
            if item.get("lfs"):
                return hashlib.sha256(data).hexdigest() == item["lfs"]["sha256"]
            return hashlib.sha1(f"blob {size}\0".encode()+data).hexdigest() == item["blobId"]
        if path.is_file() and valid(path.read_bytes()):
            return
        # Mirror bytes must match the exact pinned publisher Git blob, not merely the model name.
        urls = [f"https://modelscope.cn/models/{MODEL_ID}/resolve/master/{name}",
                f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}"]
        for url in urls:
            try:
                with urlopen(url, timeout=30) as response:
                    data = response.read(size+1)
                if valid(data):
                    temporary = path.with_suffix(path.suffix+".partial")
                    temporary.write_bytes(data)
                    temporary.replace(path)
                    return
            except Exception:
                continue
        raise RuntimeError("could not verify pinned model metadata: " + name)

    weight = next(item for item in metadata["siblings"] if item["rfilename"] == "model.safetensors")
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(small_file,item) for item in metadata["siblings"] if item["rfilename"] in allowed]
        futures.append(pool.submit(download, f"https://modelscope.cn/models/{MODEL_ID}/resolve/master/model.safetensors",
                                   target / "model.safetensors", weight["size"], weight["lfs"]["sha256"]))
        for future in futures:
            future.result()
    print("Pinned local model download complete.")
