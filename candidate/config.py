from pathlib import Path


def read_config(path):
    config = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or not name.strip().replace("_", "").isalnum():
            raise ValueError("invalid private model configuration line")
        config[name.strip()] = value.strip().strip("\"").strip("'")
    return config
