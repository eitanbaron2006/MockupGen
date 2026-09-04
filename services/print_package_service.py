"""Print files packed into what a shop is allowed to upload.

Etsy takes **five files of twenty megabytes** for a digital listing. A full
pack of print files is ten files of three to eight megabytes each: measured on
real exports, ten ratios come to about 55MB. So the total was never the
problem -- the count is. Ten files do not go into five slots, and one archive
of all of them is 55MB against a 20MB ceiling.

The packing is size-aware rather than themed, and that is not a preference.
The grouping a buyer would expect -- the standard frames together -- measures
8.2 + 6.0 + 7.0 = 21.2MB on a heavy artwork and breaks the limit on its own.
So the shape of the answer follows the bytes, and the names carry the meaning.

Nothing here touches the disk or the network: it decides what goes with what,
and says so plainly when it cannot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# What a digital listing on Etsy accepts today. Both are settings rather than
# constants at the call site, because a marketplace rule is not ours to fix.
DEFAULT_MAX_FILES = 5
DEFAULT_MAX_BYTES = 20 * 1024 * 1024


class PackingError(ValueError):
    """A set of files that cannot be delivered under the limits given."""


@dataclass
class Parcel:
    """One archive-to-be: the files in it and what they weigh."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    bytes: int = 0

    def add(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)
        self.bytes += int(entry.get("bytes") or 0)

    @property
    def ratios(self) -> list[str]:
        return [str(entry.get("ratio") or "") for entry in self.entries]


def pack_files(
    files: list[dict[str, Any]],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    reserved_slots: int = 0,
) -> list[Parcel]:
    """Group print files into as few parcels as the limits allow.

    First-fit-decreasing: the heaviest file is placed first, into the parcel it
    fits with the least room to spare. Packing the big ones first is what keeps
    the count down -- filling small-first strands a 8MB file on its own at the
    end, and the count is the whole constraint.

    ``reserved_slots`` holds places back for things that are not print files --
    the printing guide, say -- so the packing does not use the last slot the
    listing still needs.
    """
    usable = max(0, max_files - reserved_slots)
    if usable <= 0:
        raise PackingError(f"No room left for print files: {max_files} allowed, {reserved_slots} reserved")

    oversized = [entry for entry in files if int(entry.get("bytes") or 0) > max_bytes]
    if oversized:
        # Splitting an image across archives would produce something no buyer
        # can open, so this is refused rather than worked around.
        names = ", ".join(str(entry.get("ratio") or entry.get("file")) for entry in oversized)
        raise PackingError(
            f"{len(oversized)} file(s) are larger than the {max_bytes // (1024 * 1024)}MB limit on their own: {names}"
        )

    parcels: list[Parcel] = []
    for entry in sorted(files, key=lambda item: int(item.get("bytes") or 0), reverse=True):
        weight = int(entry.get("bytes") or 0)
        # The tightest parcel that still has room: leaves the roomiest ones
        # free for whatever is still to come.
        candidates = [parcel for parcel in parcels if parcel.bytes + weight <= max_bytes]
        if candidates:
            min(candidates, key=lambda parcel: max_bytes - parcel.bytes - weight).add(entry)
            continue
        parcel = Parcel()
        parcel.add(entry)
        parcels.append(parcel)

    if len(parcels) > usable:
        raise PackingError(
            f"These {len(files)} files need {len(parcels)} archives, and only {usable} can be delivered. "
            f"Remove a ratio from the set, or deliver them another way."
        )
    return parcels


def parcel_name(index: int, total: int, ratios: list[str]) -> str:
    """What the buyer sees in their downloads.

    The ratios are in the name because the split follows the bytes, not any
    order a person would guess -- so the name has to say what is inside.
    """
    shapes = "_".join(str(ratio).replace(":", "x").replace(" ", "-").lower() for ratio in ratios if ratio)
    part = f"{index}-of-{total}" if total > 1 else "all-sizes"
    return f"print-files_{part}_{shapes}.zip" if shapes else f"print-files_{part}.zip"


def packing_report(parcels: list[Parcel], max_bytes: int = DEFAULT_MAX_BYTES) -> list[dict[str, Any]]:
    """What was decided, in the form the API and the screen both want."""
    total = len(parcels)
    return [
        {
            "index": index,
            "name": parcel_name(index, total, parcel.ratios),
            "ratios": parcel.ratios,
            "files": [entry.get("file") for entry in parcel.entries],
            "bytes": parcel.bytes,
            "headroom_bytes": max_bytes - parcel.bytes,
        }
        for index, parcel in enumerate(parcels, start=1)
    ]


def plan_delivery(
    files: list[dict[str, Any]],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    has_guide: bool = False,
) -> dict[str, Any]:
    """How these files should be handed over: as they are, or archived.

    Archiving is the fallback, not the default. If the print files already fit
    the allowance, the buyer should get images that open on a double click --
    a .zip is a step between them and what they paid for, and one more thing
    to go wrong on a phone.

    So: plain files whenever the count allows, and the printing guide included
    when there is a slot spare. Only when there are more files than slots does
    anything get archived, and then the guide rides inside every archive.
    """
    oversized = [entry for entry in files if int(entry.get("bytes") or 0) > max_bytes]
    if oversized:
        names = ", ".join(str(entry.get("ratio") or entry.get("file")) for entry in oversized)
        raise PackingError(
            f"{len(oversized)} file(s) are larger than the {max_bytes // (1024 * 1024)}MB limit on their own: {names}"
        )

    if len(files) <= max_files:
        room_for_guide = has_guide and len(files) < max_files
        return {
            "mode": "files",
            "entries": list(files),
            "include_guide": room_for_guide,
            # Said out loud rather than left to be noticed: five images the
            # buyer can open beats four plus a note.
            "guide_dropped": has_guide and not room_for_guide,
            "slots_used": len(files) + (1 if room_for_guide else 0),
        }

    parcels = pack_files(files, max_files=max_files, max_bytes=max_bytes)
    return {
        "mode": "archives",
        "parcels": parcels,
        "include_guide": has_guide,
        "guide_dropped": False,
        "slots_used": len(parcels),
    }
