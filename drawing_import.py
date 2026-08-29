"""
Shared "Import from Drawing" logic (Item 1, and its Rough Estimator reuse):
upload a floor-plan drawing (architect PDF, photo/scan, or rough hand
sketch) and get back a room list -- name + approx sq ft where the drawing
shows a dimension. Used by both blueprints/quotes.py (Quote Builder) and
blueprints/estimator.py (Rough Estimator's Quick Estimate mode) -- the
extraction itself has nothing quote- or estimate-specific about it, so it
lives here once rather than being copy-pasted per caller.

The uploaded file is validated and processed entirely in memory -- never
written to disk -- matching the original scope decision not to keep a copy
of a client's drawing longer than the single API call needs it for.
"""
import base64
import os

import config

# Imported defensively so a deploy that hasn't installed the optional
# `anthropic` package yet (or a dev sandbox with no outbound package-install
# access) degrades to a clean "not configured" error from analyze_drawing()
# below instead of taking down the whole app at import time.
try:
    import anthropic
except ImportError:  # pragma: no cover - exercised only when the optional dep is missing
    anthropic = None

ALLOWED_DRAWING_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}
MAX_DRAWING_BYTES = 15 * 1024 * 1024  # 15 MB
_DRAWING_MEDIA_TYPES = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "pdf": "application/pdf",
}

_ROOM_TOOL = {
    "name": "record_rooms",
    "description": (
        "Record every distinct room/cabin/space identified in the floor-plan drawing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rooms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "approx_sqft": {"type": ["number", "null"]},
                        "source_note": {"type": ["string", "null"]},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["rooms"],
    },
}

_DRAWING_PROMPT = """You are looking at a floor-plan drawing for a villa or \
office renovation/construction project. It could be a formal architect PDF, \
a photo or scan of a printed plan, OR a rough, unlabeled hand sketch.

Identify every distinct room, cabin, or enclosed space shown in the drawing \
and call the record_rooms tool with the full list. For each space:

- name: use the room's on-drawing label if one is present (e.g. "Kitchen", \
"Manager's Cabin", "Master Bedroom"). If the drawing is an unlabeled hand \
sketch, do NOT give up -- still separate every distinct enclosed space you \
can see and give each a sensible generic name such as "Room 1", "Room 2", \
"Cabin 1", in a consistent reading order (left-to-right, top-to-bottom).
- approx_sqft: if a dimension is written near the space, convert it to an \
approximate square-foot number (a plain number, not a string). Handle:
  * a single area already in sq ft -> use as-is.
  * a single area in sq m -> multiply by 10.7639 and round to a sensible \
whole number.
  * a length x width given in feet (e.g. "12' x 10'") -> multiply them.
  * a length x width given in metres (e.g. "3.6m x 3.0m") -> multiply them, \
then multiply the result by 10.7639.
  If no dimension is shown or legible for a space, leave approx_sqft null -- \
never guess a size that isn't indicated somewhere on the drawing.
- source_note: optionally include the raw dimension text you read (e.g. \
"12' x 10'" or "10.5 sq m"), the room-type/use if noted, or any other short \
useful text next to that space. Leave null if there's nothing worth noting.

Only report spaces you can actually see distinguished in the drawing -- do \
not invent rooms that aren't shown. This is purely a room-list extraction \
step; do not suggest materials, quantities, or pricing.
"""


class DrawingImportError(Exception):
    """Carries an HTTP status alongside the message, so every caller's route
    can do the same `except DrawingImportError as e: jsonify({"error": str(e)}), e.status`
    one-liner instead of re-deriving status codes itself."""
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def _validate_drawing_upload(file_storage):
    """Validates an uploaded drawing file entirely in memory. Returns
    (extension, media_type, raw_bytes). Raises DrawingImportError(..., 400)
    on any invalid input, mirroring the shape of
    repositories/settings.py: save_logo()."""
    if not file_storage or not file_storage.filename:
        raise DrawingImportError("Choose a drawing file first.", 400)

    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_DRAWING_EXTENSIONS:
        raise DrawingImportError("Drawing must be a .png, .jpg, .jpeg, .webp, or .pdf file.", 400)

    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size == 0:
        raise DrawingImportError("That file appears to be empty.", 400)
    if size > MAX_DRAWING_BYTES:
        raise DrawingImportError("Drawing file is too large (max 15 MB).", 400)

    raw_bytes = file_storage.read()
    return ext, _DRAWING_MEDIA_TYPES[ext], raw_bytes


def analyze_drawing(file_storage):
    """Validates and analyzes an uploaded drawing file. Returns
    {"rooms": [{"name", "approx_sqft", "source_note"}, ...]}, with a
    "message" key added when zero rooms were found. Raises
    DrawingImportError (with the right status already chosen) for every
    failure case -- missing/invalid file, unconfigured API key, or an
    Anthropic API error -- so callers never need to catch anything else or
    worry about an unhandled exception 500-ing out as raw HTML."""
    ext, media_type, raw_bytes = _validate_drawing_upload(file_storage)

    if anthropic is None or not config.ANTHROPIC_API_KEY:
        raise DrawingImportError(
            "Drawing import isn't configured yet -- ask an admin to set the "
            "ANTHROPIC_API_KEY environment variable to enable this feature.",
            503,
        )

    b64_data = base64.standard_b64encode(raw_bytes).decode("ascii")
    if media_type == "application/pdf":
        content_block = {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": b64_data}}
    else:
        content_block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_data}}

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.DRAWING_MODEL,
            max_tokens=4096,
            tools=[_ROOM_TOOL],
            tool_choice={"type": "tool", "name": "record_rooms"},
            messages=[{
                "role": "user",
                "content": [content_block, {"type": "text", "text": _DRAWING_PROMPT}],
            }],
        )
    except anthropic.APIError as e:
        message = getattr(e, "message", None) or str(e) or "Anthropic API request failed."
        if isinstance(e, anthropic.AuthenticationError):
            status, message = 401, "Drawing import is misconfigured (invalid Anthropic API key)."
        elif isinstance(e, getattr(anthropic, "RateLimitError", ())):
            status, message = 429, "Anthropic API rate limit reached -- try again shortly."
        elif isinstance(e, getattr(anthropic, "BadRequestError", ())):
            status = 400
        else:
            status = 502
        raise DrawingImportError(message, status)
    except Exception as e:  # never let an unhandled exception 500 out as raw HTML
        raise DrawingImportError(f"Unexpected error analyzing the drawing: {e}", 500)

    rooms = []
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "record_rooms":
            rooms = (block.input or {}).get("rooms") or []
            break

    clean_rooms = []
    for r in rooms:
        name = (r.get("name") or "").strip() if isinstance(r, dict) else ""
        if not name:
            continue
        sqft = r.get("approx_sqft")
        try:
            sqft = float(sqft) if sqft is not None else None
        except (TypeError, ValueError):
            sqft = None
        note = r.get("source_note")
        note = note.strip() or None if isinstance(note, str) else None
        clean_rooms.append({"name": name, "approx_sqft": sqft, "source_note": note})

    if not clean_rooms:
        return {"rooms": [], "message": "Couldn't make out distinct rooms — try a clearer photo or add rooms manually."}

    return {"rooms": clean_rooms}
