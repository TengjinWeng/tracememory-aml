"""Validate private configuration, then optionally make exactly one LLM and one embedding request."""
import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from candidate.config import read_config
from candidate.providers import ProviderError, SpecifiedModels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call", action="store_true", help="make up to two billable provider requests")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = read_config(root / "secrets/model-apis.env")
    required = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL")
    missing = [name for name in required if not config.get(name)]
    if missing:
        print("Missing configuration fields: " + ", ".join(missing))
        print("No provider request attempted.")
        return
    if not args.call:
        print("Private fields populated. No API request or billing attempted; availability not yet verified.")
        return
    from PIL import Image, ImageDraw
    fixture = Image.new("RGB", (320, 160), "white")
    draw = ImageDraw.Draw(fixture)
    draw.rectangle((20, 60, 120, 140), fill="green")
    draw.text((15, 15), "TraceMemory API TEST", fill="black")
    buffer = BytesIO()
    fixture.save(buffer, format="PNG")
    content = [{"type": "text", "text": "Describe only this synthetic test image for retrieval."},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(), "detail": "low"}}]
    provider = SpecifiedModels(config, root / "data-candidate/provider-cache.sqlite3")
    user = "synthetic:api-preflight:" + uuid4().hex
    try:
        described = provider.describe(user, content)
        vectors = provider.embed(user, ["A green rectangle on a white background."])
    except ProviderError as error:
        raise SystemExit(str(error))
    print(json.dumps({"vision_api_success": True, "embedding_api_success": True,
                      "caption_characters": len(described["caption"]), "vector_dimensions": len(vectors[0]),
                      "provider_requests": 2, "official_evaluation_started": False}))


if __name__ == "__main__":
    main()
