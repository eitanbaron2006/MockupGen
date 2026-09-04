import sys
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from services.print_package_service import (  # noqa: E402
    DEFAULT_MAX_BYTES,
    PackingError,
    pack_files,
    packing_report,
    parcel_name,
)

MB = 1024 * 1024


def files(*sizes_in_mb: float) -> list[dict]:
    """Print files with the sizes real exports actually produced."""
    return [
        {"ratio": f"r{index}", "file": f"file-{index}.jpg", "bytes": int(size * MB)}
        for index, size in enumerate(sizes_in_mb, start=1)
    ]


def test_a_full_pack_of_real_files_fits_in_what_etsy_allows():
    """The measured case: ten ratios, 3.4-8.2MB each, five slots of 20MB.

    The total was never the problem -- 55MB against a 100MB ceiling. The count
    is: ten files do not go into five slots, and one archive of all of them is
    55MB against a 20MB limit.
    """
    measured = files(8.19, 7.38, 6.95, 6.17, 5.97, 5.57, 5.37, 4.76, 4.30, 3.54)
    parcels = pack_files(measured)

    assert len(parcels) <= 5, f"needed {len(parcels)} archives"
    assert all(parcel.bytes <= DEFAULT_MAX_BYTES for parcel in parcels)
    # Nothing is left behind.
    packed = [entry["file"] for parcel in parcels for entry in parcel.entries]
    assert sorted(packed) == sorted(entry["file"] for entry in measured)


def test_the_grouping_a_person_would_choose_is_the_one_that_breaks():
    """Standard frames together measure 21.2MB on a heavy artwork.

    This is why the packing follows the bytes rather than the theme: the
    obvious grouping exceeds the limit on its own.
    """
    standard = files(8.19, 5.97, 6.95)  # 2:3, 3:4, 4:5 at their measured worst
    assert sum(entry["bytes"] for entry in standard) > DEFAULT_MAX_BYTES

    parcels = pack_files(standard)
    assert len(parcels) == 2
    assert all(parcel.bytes <= DEFAULT_MAX_BYTES for parcel in parcels)


def test_the_heaviest_files_are_placed_first():
    """Filling small-first strands a big file on its own and costs a slot."""
    # Small-first would put 6+6 together and leave 9 and 9 needing one each: 3
    # archives. Big-first pairs 9+6 and 9+6: two.
    parcels = pack_files(files(9, 9, 6, 6), max_files=5)
    assert len(parcels) == 2


def test_reserved_slots_keep_room_for_what_is_not_a_print_file():
    """The printing guide needs a slot too, and it must not be the missing one."""
    measured = files(8.19, 7.38, 6.95, 6.17, 5.97, 5.57, 5.37, 4.76, 4.30, 3.54)

    # Four archives plus the guide is five files, which is the whole allowance.
    parcels = pack_files(measured, reserved_slots=1)
    assert len(parcels) <= 4

    with pytest.raises(PackingError, match="No room left"):
        pack_files(measured, max_files=1, reserved_slots=1)


def test_a_single_file_over_the_limit_is_refused_rather_than_split():
    """Half an image is something no buyer can open."""
    with pytest.raises(PackingError, match="larger than the 20MB limit"):
        pack_files(files(4, 26, 5))


def test_too_many_files_says_so_instead_of_dropping_some():
    """Silently delivering eight of eleven ratios would be the worst outcome."""
    with pytest.raises(PackingError, match="only 5 can be delivered"):
        pack_files(files(*([18] * 6)))


def test_the_name_says_what_is_inside():
    # The split follows the bytes, so the order is not one anybody would guess
    # -- the name has to carry it.
    assert parcel_name(1, 3, ["2:3", "3:4"]) == "print-files_1-of-3_2x3_3x4.zip"
    assert parcel_name(1, 1, ["1:1"]) == "print-files_all-sizes_1x1.zip"
    assert parcel_name(2, 2, ["ISO A"]) == "print-files_2-of-2_iso-a.zip"
    assert parcel_name(1, 1, []) == "print-files_all-sizes.zip"


def test_the_report_says_what_was_decided_and_what_room_is_left():
    parcels = pack_files(files(8, 7, 6))
    report = packing_report(parcels)

    assert [entry["index"] for entry in report] == list(range(1, len(parcels) + 1))
    for entry in report:
        assert entry["bytes"] + entry["headroom_bytes"] == DEFAULT_MAX_BYTES
        assert entry["name"].endswith(".zip")
        assert entry["ratios"] and entry["files"]


def test_nothing_to_pack_is_not_an_error():
    assert pack_files([]) == []


def test_files_that_already_fit_are_handed_over_as_they_are():
    """A .zip is a step between the buyer and what they paid for.

    Five images that open on a double click beat three archives, and the
    allowance is five files -- so archiving is the fallback, not the default.
    """
    from services.print_package_service import plan_delivery

    plan = plan_delivery(files(8, 7, 6, 5), has_guide=True)

    assert plan["mode"] == "files"
    assert len(plan["entries"]) == 4
    # Four images and the note is five files, which is the whole allowance.
    assert plan["include_guide"] is True
    assert plan["slots_used"] == 5


def test_a_full_five_keeps_the_images_and_says_the_note_did_not_fit():
    from services.print_package_service import plan_delivery

    plan = plan_delivery(files(8, 7, 6, 5, 4), has_guide=True)

    assert plan["mode"] == "files"
    assert plan["slots_used"] == 5
    assert plan["include_guide"] is False
    # Not left to be noticed later.
    assert plan["guide_dropped"] is True


def test_more_files_than_slots_is_when_archiving_starts():
    from services.print_package_service import plan_delivery

    measured = files(8.19, 7.38, 6.95, 6.17, 5.97, 5.57, 5.37, 4.76, 4.30, 3.54)
    plan = plan_delivery(measured, has_guide=True)

    assert plan["mode"] == "archives"
    assert plan["slots_used"] <= 5
    # Inside every archive, so whichever one is opened first has it.
    assert plan["include_guide"] is True
    assert plan["guide_dropped"] is False


def test_one_ratio_on_its_own_is_one_plain_file():
    """The matching-ratio case, which is most of them."""
    from services.print_package_service import plan_delivery

    plan = plan_delivery(files(8), has_guide=True)
    assert plan["mode"] == "files"
    assert plan["slots_used"] == 2 and plan["include_guide"] is True


def test_a_file_over_the_limit_is_recompressed_rather_than_refused(tmp_path):
    """A detailed artwork at 7200x10800 is 34MB at quality 95.

    Four of six ratios exceed Etsy's per-file limit on their own, before any
    packing. What gives is the compression -- the dimensions and the 300 DPI
    are the two a print actually needs.
    """
    import random

    from PIL import Image

    from services.print_export_service import fit_file_to_limit

    random.seed(11)
    detailed = Image.new("RGB", (1400, 1400))
    pixels = detailed.load()
    for y in range(1400):
        for x in range(1400):
            pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))

    path = tmp_path / "big.jpg"
    detailed.save(path, "JPEG", quality=95, optimize=True, dpi=(300, 300))
    original = path.stat().st_size
    limit = original // 2

    outcome = fit_file_to_limit(path, limit)

    assert outcome["fitted"] is True
    assert outcome["bytes"] <= limit < outcome["was_bytes"]
    assert outcome["quality"] < 95
    # The pixels and the DPI are untouched: only the compression moved.
    with Image.open(path) as saved:
        assert saved.size == (1400, 1400)
        assert saved.info.get("dpi") == (300, 300)


def test_a_file_already_under_the_limit_is_left_exactly_as_it_is(tmp_path):
    from PIL import Image

    from services.print_export_service import fit_file_to_limit

    path = tmp_path / "small.jpg"
    Image.new("RGB", (400, 400), (200, 90, 70)).save(path, "JPEG", quality=95, dpi=(300, 300))
    before = path.read_bytes()

    outcome = fit_file_to_limit(path, 20 * 1024 * 1024)

    assert outcome["fitted"] is False and outcome["quality"] is None
    # Not re-encoded: a second pass would cost quality for nothing.
    assert path.read_bytes() == before
