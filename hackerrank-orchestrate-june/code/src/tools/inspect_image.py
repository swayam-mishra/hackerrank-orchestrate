"""The inspect_image tool: deterministic crop/zoom of the ORIGINAL full-res image so
the VLM can re-examine sub-threshold detail. No object detector — just a crop."""
from __future__ import annotations

from src.config import Config
from src.perception.ingest import crop_region

INSPECT_IMAGE_TOOL: dict = {
    "name": "inspect_image",
    "description": (
        "Zoom into a region of an ORIGINAL submitted image to re-examine fine detail "
        "(hairline cracks, faint scratches, small dents). Returns a cropped, high-resolution view. "
        "Use this before deciding whenever a detail is too small or unclear to judge from the overview."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "image_id": {"type": "string", "description": "Which image to zoom, e.g. 'img_1'."},
            "focus_area": {
                "type": "string",
                "description": ("Region to zoom: one of center, top, bottom, left, right, top_left, "
                                "top_right, bottom_left, bottom_right, full — or a part name such as "
                                "'windshield', 'hinge', 'seal'."),
            },
            "bbox": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional [x0,y0,x1,y1] pixel box on the ORIGINAL image; overrides focus_area.",
            },
        },
        "required": ["image_id", "focus_area"],
    },
}


def handle_inspect_image(args: dict, abs_by_id: dict[str, str], cfg: Config) -> list[dict]:
    """Return tool_result content blocks (a zoomed image + caption), or an error note."""
    image_id = str(args.get("image_id", "")).strip()
    focus = str(args.get("focus_area", "center")).strip() or "center"
    bbox = args.get("bbox")
    abs_path = abs_by_id.get(image_id)
    if not abs_path:
        return [{"type": "text", "text": f"No such image '{image_id}'. Available: {', '.join(abs_by_id) or 'none'}."}]
    bbox_t = tuple(int(v) for v in bbox) if isinstance(bbox, list) and len(bbox) == 4 else None
    b64 = crop_region(abs_path, focus, cfg, bbox_t, image_id=image_id)  # type: ignore[arg-type]
    if not b64:
        return [{"type": "text", "text": f"Could not crop {image_id} at '{focus}'."}]
    return [
        {"type": "text", "text": (
            f"Zoomed view of {image_id} (region: {focus}). Scale note: this is "
            "a magnified crop — do not over-estimate severity based on loss of "
            "global context. A cosmetic mark may appear larger than it is at "
            "normal viewing distance."
        )},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
    ]
