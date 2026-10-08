"""Turn SBA research text into lead records.

The CEO's bootstrap prospecting asked SBA to "research 5 high-fit target
accounts" and then stopped. The agents produced the research — competently, as
it happens — and it went into a task row as text. Nothing ever wrote a lead.
So `total_leads` stayed 0, the heartbeat kept seeing an empty pipeline, and it
kept dispatching prospecting. Nineteen identical runs, zero leads.

This module is the missing link. It is deliberately a pure parser plus a thin
persist step: the parse is testable without a database, and the persist step
never raises, because a lead-capture failure must not mark an otherwise good
orchestration as failed.

Two output shapes have been observed in production and both are supported:

    1. Bright Smile Dental - Austin, TX - Pain: 40 % of calls
       unanswered after 6 pm. Hook: "Your phone rings at 9 pm..."

and

    | 1 | **Bright Smile Dental** | Austin, TX | New-patient lead flow
      inconsistent | *"Dr. Lee, noticed your Yelp..."* |

Parsed values are a best effort and are recorded as such. A lead with a slightly
mangled pain point is worth more than no lead and a retry loop.
"""

from __future__ import annotations

import re
from typing import Any

# En dash and em dash are what the models actually emit; a plain hyphen appears
# too, but only as a fallback because it also appears inside the prose
# ("new-patient", "30-day"), which would shred the fields.
_DASHES = "–—"
_DASH_RE = re.compile(rf"[{_DASHES}]")
_FALLBACK_DASH_RE = re.compile(r"\s-\s")

_LIST_ITEM_RE = re.compile(r"^\s{0,4}(\d{1,2})[.)]\s+(.*\S)\s*$")
_CITY_RE = re.compile(r"^[A-Z][A-Za-z .'-]{1,28}(?:\s*,\s*[A-Z]{2})?$")
# "<business> <City> (qualifier)" as the models actually write it.
_EMBEDDED_CITY_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<city>[A-Z][a-z]{2,15})\s*\([^)]*\)\s*$")
# A city at the start of the field after the name, followed by more prose:
# "Nagpur. Pain-point: ..." did not match _CITY_RE because the rest of the
# sentence rode along, which left city empty for every live lead captured so far.
_CITY_LEAD_RE = re.compile(r"^([A-Z][a-z]{2,15}(?:\s*,\s*[A-Z]{2})?)\s*[.·–—-]?\s*")
# Leading words that look like a capitalised token but are field labels.
_NOT_A_CITY = frozenset({
    "pain", "hook", "niche", "tag", "tags", "note", "notes", "ready",
    "merging", "the", "this", "these", "our", "we", "it", "they", "their",
})
_STAR = re.compile(r"\*\*")
_QUOTES = "\"'“”‘’"
_LEAD_NOISE = re.compile(
    r"^\s*(tags?|niche|merging note|ready for|note)\b[:\-]?\s*", re.I)


def _clean(text: str) -> str:
    """Strip bold markers and the quote characters the models wrap fields in.

    The table fixture wraps hooks as `*"..."*`, so an asterisk has to come off
    the edges as well as the double-asterisk pairs from the inside.
    """
    t = _STAR.sub("", text or "")
    t = t.strip().strip(_QUOTES).strip().strip("*").strip()
    t = t.strip(_QUOTES).strip()
    return re.sub(r"\s+", " ", t)


def _norm_key(name: str) -> str:
    """Normalised form used for duplicate detection."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _split_fields(chunk: str) -> list[str]:
    parts = _DASH_RE.split(chunk)
    if len(parts) < 2:
        parts = _FALLBACK_DASH_RE.split(chunk)
    return [p.strip() for p in parts if p.strip()]


_LABEL_SPLIT_RE = re.compile(r"\s*(?:Pain|Hook|Niche)\s*[-–—]?\s*(?:point)?\s*:?\s*")


def _split_fields_on_label(chunk: str) -> list[str]:
    """Fallback split for "Bright Smile Dental Pain: ..." with no separator."""
    parts = [p.strip() for p in _LABEL_SPLIT_RE.split(chunk) if p and p.strip()]
    return parts if len(parts) >= 2 else []


def _parse_list_line(line: str) -> dict[str, Any] | None:
    m = _LIST_ITEM_RE.match(line)
    if not m:
        return None
    parts = _split_fields(m.group(2))
    if len(parts) < 2:
        # No separator at all. The model sometimes just runs the name into a
        # "Pain:" label instead, so fall back to splitting on the label.
        parts = _split_fields_on_label(m.group(2))
    if len(parts) < 2:
        return None

    name = _clean(parts[0])
    city = ""

    # The model sometimes glues the city onto the business name, e.g.
    # "BrightSmile Dental Nagpur (multi-location)". Pull it back out so the
    # lead is searchable by city and the name is not polluted.
    name, embedded_city = _split_embedded_city(name)

    # `tail` is everything after the name. Tracked separately from `parts`
    # because the name has to be dropped from it exactly once, and getting
    # that wrong silently prepends the business name to the pain point.
    tail_parts = parts[1:]

    if len(parts) >= 2 and _CITY_RE.match(_clean(parts[1])):
        city = _clean(parts[1])
        tail_parts = parts[2:]
    elif embedded_city:
        city = embedded_city
    elif len(parts) >= 2:
        # The city is the first token of the second field but shares it with
        # the rest of the sentence, so _CITY_RE rejected the whole field.
        lead_city, remainder = _split_leading_city(parts[1])
        if lead_city:
            city = lead_city
            tail_parts = [remainder]

    pain, hook = _split_pain_hook(" ".join(tail_parts))
    if not name:
        return None
    return {"business_name": name, "city": city, "pain_point": pain, "hook": hook}


def _split_leading_city(text: str) -> tuple[str, str]:
    """Pull a city off the front of a field that continues into prose.

    Returns ("", original) when the leading token is a field label such as
    "Pain" rather than a place.
    """
    m = _CITY_LEAD_RE.match(text or "")
    if not m:
        return "", text
    candidate = m.group(1).strip()
    if candidate.lower() in _NOT_A_CITY:
        return "", text
    remainder = (text[m.end():] or "").strip(" .;:-")
    return candidate, remainder


def _split_embedded_city(name: str) -> tuple[str, str]:
    """Separate a trailing city from a business name, when present.

    Only fires on a name shaped "<something> <City> (qualifier)", because that
    is the pattern actually observed. Splitting a bare name on its last word
    would turn "Peak Physio Clinic" into business="Peak Physio", city="Clinic".
    """
    m = _EMBEDDED_CITY_RE.match(name)
    if not m:
        return name, ""
    return m.group(1).strip(), m.group(2).strip()


def _parse_table_row(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells = [_clean(c) for c in stripped.strip("|").split("|")]
    cells = [c for c in cells if c]
    if len(cells) < 2:
        return None
    # Drop a leading ordinal cell ("1", "2", ...).
    if cells and re.fullmatch(r"\d{1,2}", cells[0]):
        cells = cells[1:]
    if not cells:
        return None
    name = cells[0]
    city = cells[1] if len(cells) > 1 and _CITY_RE.match(cells[1]) else ""
    rest = cells[2:] if city else cells[1:]
    pain = rest[0] if rest else ""
    hook = rest[1] if len(rest) > 1 else ""
    return {"business_name": name, "city": city, "pain_point": pain, "hook": hook}


def _split_pain_hook(text: str) -> tuple[str, str]:
    """Pull the outreach hook out of the prose tail.

    Only a quote that is long enough to be a sentence is treated as the hook, so
    a stray quotation mark mid-pain-point does not truncate it.
    """
    hook = ""
    m = re.search(r"Hook:\s*(.+)$", text, re.I)
    if m:
        hook = _clean(m.group(1))
        text = text[: m.start()]
    else:
        quoted = re.findall(r"[\"“']([^\"“”']{25,})[\"”']", text)
        if quoted:
            hook = _clean(quoted[-1])
    # "Pain:", "Pain -", and "Pain-point:" all appear. Stripping only "Pain:"
    # left the "-point:" suffix glued to the front of the pain text, which is
    # exactly what the first live captures stored.
    pain = re.sub(r"\bPain\s*[-–—]?\s*(?:point)?\s*:?\s*", "", text, flags=re.I)
    pain = pain.strip(" .;,-")
    return _clean(pain), hook


def parse_sba_targets(text: str) -> list[dict[str, str]]:
    """Extract lead candidates from SBA output.

    Handles both observed shapes, keeps the first occurrence of each business,
    and returns plain dicts so this can be tested with no database involved.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if _LEAD_NOISE.match(line.strip()) and not line.strip().startswith("|"):
            continue
        parsed = _parse_table_row(line) if line.strip().startswith("|") else _parse_list_line(line)
        if not parsed:
            continue
        name = parsed["business_name"]
        key = _norm_key(name)
        # A name this short is a parsing artefact, not a business.
        if not key or len(key) < 3 or key in seen:
            continue
        seen.add(key)
        parsed["source"] = "ceo_autonomy_prospecting"
        out.append(parsed)
    return out


async def capture_leads_from_sba(
    text: str,
    workspace_id: str = "",
    existing_names: set[str] | None = None,
) -> dict[str, Any]:
    """Parse SBA output and persist new leads. Never raises.

    Returns a small report so the caller can log what happened instead of
    guessing.
    """
    report: dict[str, Any] = {"parsed": 0, "created": 0, "skipped_duplicate": 0, "error": None}
    try:
        candidates = parse_sba_targets(text)
        report["parsed"] = len(candidates)
        if not candidates:
            return report

        from admin.agency.sba_store import create_lead, list_leads

        if existing_names is None:
            existing_names = {_norm_key(l.get("business_name") or l.get("name") or "")
                              for l in list_leads()}

        for c in candidates:
            key = _norm_key(c["business_name"])
            if key in existing_names:
                report["skipped_duplicate"] += 1
                continue
            await create_lead({
                "business_name": c["business_name"],
                "name": c["business_name"],
                "source": c["source"],
                "status": "new",
                "score": 60,
                "notes": [],
                "context": {
                    "city": c.get("city", ""),
                    "pain_point": c.get("pain_point", ""),
                    "outreach_hook": c.get("hook", ""),
                    "workspace_id": workspace_id,
                    "origin": "ceo_autonomy.bootstrap_prospecting",
                    "confidence": "parsed from free-text agent output",
                },
            })
            existing_names.add(key)
            report["created"] += 1
    except Exception as exc:  # never let this fail an otherwise good run
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report
