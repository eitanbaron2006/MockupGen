import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from test_mockup_api import build_client  # noqa: E402


def artwork(size=(1200, 1800)) -> io.BytesIO:
    stream = io.BytesIO()
    image = Image.new("RGB", size, (196, 76, 60))
    for x in range(0, size[0], 40):
        for y in range(0, size[1], 40):
            image.putpixel((x, y), (70, 120, 170))
    image.save(stream, format="PNG")
    stream.seek(0)
    return stream


def studio(tmp_path):
    return build_client(tmp_path, ADMIN_PASSWORD="test-admin", SECRET_KEY="test-secret")


def login(client) -> str:
    response = client.post("/api/admin/login", json={"password": "test-admin"})
    assert response.status_code == 200
    return response.get_json()["csrf_token"]


def small_ratios(client, csrf):
    """Shrink the seeded ratios so a test does not render 70 megapixels."""
    for ratio in client.get("/api/print/ratios").get_json()["ratios"]:
        client.patch(
            f"/api/print/ratios/{ratio['id']}",
            json={"width": max(100, ratio["width"] // 40), "height": max(100, ratio["height"] // 40)},
            headers={"X-CSRF-Token": csrf},
        )


def export(client, spec, size=(1200, 1800)):
    return client.post(
        "/api/print/export",
        data={"artwork": (artwork(size), "art.png"), "spec": json.dumps(spec)},
        content_type="multipart/form-data",
    )


def test_the_ratios_a_shop_sells_are_there_from_the_start(tmp_path):
    """A shop that has these numbers already should not retype them."""
    client, _ = studio(tmp_path)

    offered = client.get("/api/print/ratios").get_json()
    keys = [ratio["key"] for ratio in offered["ratios"]]
    assert keys[:3] == ["2:3", "3:4", "4:5"]
    # Every shape the seller's own resizer offered, panoramics included.
    assert set(keys) == {"2:3", "3:4", "4:5", "11:14", "ISO A", "1:1", "5:7", "US Letter", "3:1", "2:1"}
    panoramic = next(ratio for ratio in offered["ratios"] if ratio["key"] == "3:1")
    assert (panoramic["width"], panoramic["height"]) == (10800, 3600)
    two_three = offered["ratios"][0]
    assert (two_three["width"], two_three["height"]) == (7200, 10800)
    assert "24x36" in two_three["sizes"]

    # The qualities say what this machine can actually deliver. Whether the two
    # AI programs are installed here is not the test's business -- that they are
    # reported honestly either way is.
    qualities = {quality["key"]: quality for quality in offered["qualities"]}
    assert qualities["bicubic"]["available"] is True
    for key in ("ai", "gigapixel"):
        assert qualities[key]["available"] or qualities[key]["reason"]

    # And the three Etsy output modes are offered, with the wording the screen
    # shows and a flag for the one that can cut the artwork.
    assert [mode["key"] for mode in offered["modes"]] == ["safe_fit", "safe_fill", "fill_crop"]
    modes = {mode["key"]: mode for mode in offered["modes"]}
    assert modes["safe_fit"]["recommended"] is True
    assert modes["fill_crop"]["cuts"] is True
    assert modes["safe_fill"]["cuts"] is False


def test_an_export_never_crops_the_artwork(tmp_path):
    """A ratio the artwork does not fill gets margins, not a trimmed edge.

    A border cut off a print is a refund, so safe-fit is the whole point of the
    mode: the buyer gets the entire image, white space and all.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    response = export(client, {"ratios": "2:3, 4:5", "quality": "basic"})
    assert response.status_code == 200
    files = {entry["ratio"]: entry for entry in response.get_json()["files"]}
    assert set(files) == {"2:3", "4:5"}

    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])
    with Image.open(folder / files["2:3"]["file"]) as own:
        # Its own ratio fills the file edge to edge.
        assert own.getpixel((1, own.height // 2)) != (255, 255, 255)
        assert round(own.width / own.height, 2) == round(2 / 3, 2)
    with Image.open(folder / files["4:5"]["file"]) as other:
        # A taller artwork in a squarer canvas is held by its height, so the
        # margins land at the sides -- and the artwork itself is untouched.
        assert other.getpixel((1, other.height // 2)) == (255, 255, 255)
        assert other.getpixel((other.width // 2, other.height // 2)) != (255, 255, 255)

    # Each file says which frame sizes it prints at.
    assert "24x36" in files["2:3"]["prints_at"]
    # ...and the buyer's note ships with them.
    guide = response.get_json()["guide"]
    assert guide and guide["file"].endswith("printing_guide.txt")
    assert "nothing has been cropped" in (folder / guide["file"]).read_text(encoding="utf-8")


def test_a_landscape_artwork_turns_the_ratio_on_its_side(tmp_path):
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    response = export(client, {"ratios": "2:3", "quality": "basic"}, size=(1800, 1200))

    entry = response.get_json()["files"][0]
    assert entry["width"] > entry["height"]
    assert "landscape" in entry["file"]


def test_a_print_set_decides_which_files_an_artwork_produces(tmp_path):
    """The admin's call, per set: only the artwork's own ratio, or a chosen list."""
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    matching = client.post(
        "/api/print/sets",
        json={"name": "Its own ratio", "mode": "matching", "quality": "basic"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["set"]
    pack = client.post(
        "/api/print/sets",
        json={
            "name": "The full pack",
            "mode": "chosen",
            "ratio_keys": ["2:3", "3:4", "4:5"],
            "quality": "basic",
            "include_guide": False,
        },
        headers={"X-CSRF-Token": csrf},
    ).get_json()["set"]

    only_its_own = export(client, {"set": matching["id"]}).get_json()
    assert [entry["ratio"] for entry in only_its_own["files"]] == ["2:3"]
    assert only_its_own["guide"] is not None

    everything = export(client, {"set": pack["id"]}).get_json()
    assert [entry["ratio"] for entry in everything["files"]] == ["2:3", "3:4", "4:5"]
    # A set that ships no note does not write one.
    assert everything["guide"] is None

    # A square artwork under the matching set follows its own shape.
    square = export(client, {"set": matching["id"]}, size=(1000, 1000)).get_json()
    assert [entry["ratio"] for entry in square["files"]] == ["1:1"]

    assert export(client, {"set": 9999}).status_code == 404


def test_the_files_come_back_as_one_archive(tmp_path):
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)
    made = export(client, {"ratios": "2:3, 3:4", "quality": "basic"}).get_json()

    names = [entry["file"] for entry in made["files"]] + [made["guide"]["file"]]
    archive = client.post("/api/print/archive", json={"files": names, "name": "prints.zip"})

    assert archive.status_code == 200
    assert archive.headers["Content-Type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.data)) as bundle:
        inside = bundle.namelist()
    assert len(inside) == 3
    # The batch id the studio uses is not something the buyer should see.
    batch = made["files"][0]["file"].split("_")[0]
    assert all(not name.startswith(batch) for name in inside)
    archive.close()

    assert client.post("/api/print/archive", json={"files": ["../secrets.txt"]}).status_code == 400
    assert client.post("/api/print/archive", json={"files": ["nope.jpg"]}).status_code == 404


def test_managing_the_catalog_needs_the_administrator(tmp_path):
    client, _ = studio(tmp_path)

    # Reading is open, the way the render API is: the shop app asks for these.
    assert client.get("/api/print/ratios").status_code == 200
    assert client.get("/api/print/sets").status_code == 200

    # Changing them is not.
    assert client.post("/api/print/ratios", json={"key": "9:16", "width": 200, "height": 400}).status_code == 401
    assert client.post("/api/print/sets", json={"name": "x"}).status_code == 401
    assert client.get("/api/print/settings").status_code == 401

    csrf = login(client)
    # ...and even then it is refused without the token.
    assert client.post("/api/print/ratios", json={"key": "9:16", "width": 200, "height": 400}).status_code == 403
    created = client.post(
        "/api/print/ratios",
        json={"key": "9:16", "name": "Story", "width": 200, "height": 400, "sizes": "9x16"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    assert created.get_json()["ratio"]["key"] == "9:16"


def test_the_page_is_behind_the_same_login_as_the_studio(tmp_path):
    client, _ = studio(tmp_path)

    anonymous = client.get("/print")
    assert anonymous.status_code == 302
    assert "/admin" in anonymous.headers["Location"]

    login(client)
    page = client.get("/print")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    # The screen it renders, and the two assets it needs.
    assert "admin/print.css" in body
    assert "admin/print.js" in body
    assert 'id="exportResults"' in body and 'id="setList"' in body and 'id="ratioList"' in body


def test_a_ratio_is_refused_rather_than_stored_wrong(tmp_path):
    from services.print_catalog_service import PrintCatalogError, PrintCatalogService

    catalog = PrintCatalogService(tmp_path / "print.sqlite3")
    catalog.initialize()

    with pytest.raises(PrintCatalogError, match="already exists"):
        catalog.create_ratio({"key": "2:3", "width": 1000, "height": 1500})
    with pytest.raises(PrintCatalogError, match="at least 100px"):
        catalog.create_ratio({"key": "tiny", "width": 10, "height": 10})
    with pytest.raises(PrintCatalogError, match="at most 30000px"):
        catalog.create_ratio({"key": "huge", "width": 90000, "height": 90000})
    with pytest.raises(PrintCatalogError, match="needs a key"):
        catalog.create_ratio({"key": "  ", "width": 1000, "height": 1500})


def test_a_ratio_added_to_the_list_reaches_a_database_that_already_exists(tmp_path):
    """The seed used to run only on an empty table, which stranded upgrades."""
    from services.print_catalog_service import PrintCatalogService

    catalog = PrintCatalogService(tmp_path / "print.sqlite3")
    catalog.initialize()
    # An older database: two of the built-ins were never there, and one of the
    # ones that was has been switched off and renamed by the admin.
    two_to_three = catalog.get_ratio_by_key("2:3")
    catalog.update_ratio(two_to_three["id"], {"active": False, "name": "My own name"})
    import sqlite3

    with sqlite3.connect(catalog.database_path) as connection:
        connection.execute("DELETE FROM ratios WHERE key IN ('3:1', '2:1')")

    catalog.initialize()

    keys = [ratio["key"] for ratio in catalog.list_ratios()]
    assert "3:1" in keys and "2:1" in keys
    # ...and the admin's edits to an existing one survived the top-up.
    kept = catalog.get_ratio_by_key("2:3")
    assert kept["name"] == "My own name" and kept["active"] == 0


def test_a_built_in_ratio_is_switched_off_rather_than_deleted(tmp_path):
    """Deleting one would break every set naming it, with no way back."""
    client, _ = studio(tmp_path)
    csrf = login(client)

    built_in = next(r for r in client.get("/api/print/ratios").get_json()["ratios"] if r["key"] == "2:3")
    assert built_in["builtin"] == 1
    refused = client.delete(f"/api/print/ratios/{built_in['id']}", headers={"X-CSRF-Token": csrf})
    assert refused.status_code == 400
    assert "built-in" in refused.get_json()["error"]
    assert client.get("/api/print/ratios").get_json()["ratios"]

    # Switching it off is allowed, and is what the screen offers instead.
    off = client.patch(
        f"/api/print/ratios/{built_in['id']}",
        json={"active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert off.status_code == 200 and off.get_json()["ratio"]["active"] == 0

    # A ratio the admin added is theirs to delete.
    mine = client.post(
        "/api/print/ratios",
        json={"key": "9:16", "name": "Story", "width": 2000, "height": 3556},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["ratio"]
    assert mine["builtin"] == 0
    assert client.delete(f"/api/print/ratios/{mine['id']}", headers={"X-CSRF-Token": csrf}).status_code == 200


def test_a_panoramic_canvas_is_not_stood_on_its_end(tmp_path):
    """The canvas follows the artwork, rather than flipping what was stored.

    A panoramic is stored landscape because that is how it is sold. The first
    version swapped every non-square ratio for a landscape artwork, which stood
    a 3:1 print upright -- the one shape that must never happen to it.
    """
    from PIL import Image as PillowImage

    from services.print_export_service import target_size

    panoramic = {"key": "3:1", "width": 10800, "height": 3600}
    upright = {"key": "2:3", "width": 7200, "height": 10800}
    wide_art = PillowImage.new("RGB", (1800, 600))
    tall_art = PillowImage.new("RGB", (600, 900))
    square_art = PillowImage.new("RGB", (800, 800))

    assert target_size(panoramic, wide_art) == (10800, 3600)
    assert target_size(panoramic, tall_art) == (3600, 10800)
    assert target_size(upright, wide_art) == (10800, 7200)
    assert target_size(upright, tall_art) == (7200, 10800)
    # A square artwork has no orientation to follow.
    assert target_size(panoramic, square_art) == (10800, 3600)


def test_an_installed_upscaler_is_offered_without_being_configured(tmp_path, monkeypatch):
    """An empty setting is not the same as "not installed".

    The first cut reported Real-ESRGAN missing on a machine where it was
    installed and working, because nothing had been typed into the settings.
    """
    from services import print_export_service as export_service

    program = tmp_path / "realesrgan-ncnn-vulkan.exe"
    program.write_bytes(b"")
    monkeypatch.setattr(export_service, "TOOL_CANDIDATES", {"realesrgan": (str(program),), "topaz": ()})
    monkeypatch.setattr(export_service.shutil, "which", lambda name: None)

    found = export_service.resolved_tools({})
    assert found["realesrgan"] == str(program)
    offered = {q["key"]: q for q in export_service.available_qualities(found)}
    assert offered["ai"]["available"] is True
    # Topaz is genuinely absent here, and says so by name.
    assert offered["gigapixel"]["available"] is False
    assert "tpai.exe" in offered["gigapixel"]["reason"]

    # A path the admin typed still wins over what was found.
    typed = export_service.resolved_tools({"realesrgan_path": r"D:\elsewhere\x.exe"})
    assert typed["realesrgan"] == r"D:\elsewhere\x.exe"


def test_the_three_etsy_output_modes_do_what_they_say(tmp_path):
    """Safe Fit leaves margins, Safe Fill covers them, Fill/Crop cuts."""
    from PIL import Image as PillowImage

    from services.print_export_service import render_print_file

    # A tall artwork with a bright edge, into a square canvas.
    art = PillowImage.new("RGB", (300, 600), (40, 90, 200))
    for y in range(600):
        for x in range(6):
            art.putpixel((x, y), (250, 40, 40))
    square = {"key": "1:1", "width": 400, "height": 400}

    fit = render_print_file(art, square, quality="basic", mode="safe_fit")
    fill = render_print_file(art, square, quality="basic", mode="safe_fill")
    crop = render_print_file(art, square, quality="basic", mode="fill_crop")

    assert fit.size == fill.size == crop.size == (400, 400)
    # Safe Fit: plain white at the sides.
    assert fit.getpixel((2, 200)) == (255, 255, 255)
    # Safe Fill: the same margin is covered, and it is not white.
    assert fill.getpixel((2, 200)) != (255, 255, 255)
    # Both keep the whole artwork: its red edge survives across the middle.
    for produced in (fit, fill):
        middle = [produced.getpixel((x, 200)) for x in range(400)]
        assert any(pixel[0] > 180 and pixel[1] < 110 for pixel in middle), "the artwork edge was lost"
    # Fill/Crop fills the canvas rather than leaving a margin.
    assert crop.getpixel((2, 200)) != (255, 255, 255)


def test_an_unknown_output_mode_is_refused_rather_than_guessed(tmp_path):
    from services.print_catalog_service import PrintCatalogError, PrintCatalogService

    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    refused = export(client, {"ratios": "2:3", "mode": "creative"})
    assert refused.status_code == 400
    assert "creative" in refused.get_json()["error"]

    made = export(client, {"ratios": "2:3", "quality": "basic", "mode": "safe_fill"}).get_json()
    assert made["mode"] == "safe_fill"

    catalog = PrintCatalogService(tmp_path / "other.sqlite3")
    catalog.initialize()
    with pytest.raises(PrintCatalogError, match="Unknown output mode"):
        catalog.create_set({"name": "Bad", "output_mode": "sideways"})


def test_a_set_remembers_its_output_mode(tmp_path):
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    created = client.post(
        "/api/print/sets",
        json={"name": "Blurred pack", "mode": "matching", "quality": "basic", "output_mode": "safe_fill"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["set"]
    assert created["output_mode"] == "safe_fill"

    made = export(client, {"set": created["id"]}).get_json()
    assert made["mode"] == "safe_fill"
    assert "not part of the image" in Path(
        client.application.config["PRINT_OUTPUT_FOLDER"], made["guide"]["file"]
    ).read_text(encoding="utf-8")

    # An explicit mode on one export overrides the set without changing it.
    once = export(client, {"set": created["id"], "mode": "safe_fit"}).get_json()
    assert once["mode"] == "safe_fit"
    assert client.get("/api/print/sets").get_json()["sets"][0]["output_mode"] == "safe_fill"


def test_the_studio_ships_its_own_copy_of_the_upscaler():
    """A fresh checkout should have the AI quality without an install step."""
    from services.print_export_service import BUNDLED_ROOT, TOOL_CANDIDATES, discover_tool

    first = Path(TOOL_CANDIDATES["realesrgan"][0])
    assert first.parent.parent == BUNDLED_ROOT, "the bundled copy must be looked at first"
    if first.is_file():
        # On a machine that has the checkout, that is what gets used -- ahead
        # of any system-wide install.
        assert discover_tool("realesrgan") == str(first)


def test_a_cut_out_artwork_is_laid_on_white_not_black(tmp_path):
    """A print file is a JPEG, so the only question is which background.

    Dropping the alpha channel the plain way leaves what sat under it, which is
    black -- so a cut-out PNG exported as a black rectangle with the artwork in
    the middle of it.
    """
    from services.print_export_service import flatten_artwork, has_transparency

    cut_out = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    cut_out.paste(Image.new("RGBA", (200, 300), (200, 90, 70, 255)), (100, 150))
    assert has_transparency(cut_out) is True
    flat = flatten_artwork(cut_out)
    assert flat.mode == "RGB"
    assert flat.getpixel((5, 5)) == (255, 255, 255)
    assert flat.getpixel((200, 300)) == (200, 90, 70)

    # An artwork that never had transparency is left alone.
    opaque = Image.new("RGB", (100, 100), (10, 20, 30))
    assert has_transparency(opaque) is False
    assert flatten_artwork(opaque).getpixel((5, 5)) == (10, 20, 30)


def test_the_export_says_whether_the_upload_was_transparent(tmp_path):
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    stream = io.BytesIO()
    cut_out = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    cut_out.paste(Image.new("RGBA", (300, 450), (200, 90, 70, 255)), (150, 225))
    cut_out.save(stream, format="PNG")
    stream.seek(0)

    made = client.post(
        "/api/print/export",
        data={"artwork": (stream, "cutout.png"), "spec": json.dumps({"ratios": "2:3", "quality": "basic"})},
        content_type="multipart/form-data",
    ).get_json()
    assert made["artwork_was_transparent"] is True

    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])
    with Image.open(folder / made["files"][0]["file"]) as produced:
        corner = produced.convert("RGB").getpixel((4, 4))
    # White, not the black the plain conversion left behind.
    assert min(corner) > 240, corner


def test_an_export_leaves_a_record_of_what_it_made(tmp_path):
    """A folder of anonymous files cannot be answered for, or cleaned up.

    The record is what says which artwork a print file came from, under which
    set and mode -- and it is what lets anything sweep the folder later without
    guessing which files still matter.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    pack = client.post(
        "/api/print/sets",
        json={"name": "The pack", "mode": "chosen", "ratio_keys": ["2:3", "3:4"], "quality": "basic"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["set"]

    made = client.post(
        "/api/print/export",
        data={
            "artwork": (artwork(), "seaside.png"),
            "spec": json.dumps({"set": pack["id"], "reference": "listing-7781"}),
        },
        content_type="multipart/form-data",
    ).get_json()
    assert made["export_id"]

    history = client.get("/api/print/exports").get_json()
    assert history["total"] == 1
    entry = history["exports"][0]
    assert entry["artwork_name"] == "seaside.png"
    assert (entry["artwork_width"], entry["artwork_height"]) == (1200, 1800)
    assert entry["set_name"] == "The pack" and entry["set_id"] == pack["id"]
    assert entry["output_mode"] == "safe_fit" and entry["quality"] == "basic"
    assert entry["guide_file"].endswith("printing_guide.txt")
    assert [f["ratio_key"] for f in entry["files"]] == ["2:3", "3:4"]
    # The size on disk is recorded, so a cleanup can say what it will reclaim.
    assert all(f["bytes"] > 0 for f in entry["files"])

    # The shop app asks what it already holds for one listing.
    assert client.get("/api/print/exports?reference=listing-7781").get_json()["total"] == 1
    assert client.get("/api/print/exports?reference=listing-0000").get_json()["total"] == 0


def test_forgetting_an_export_takes_its_files_with_it(tmp_path):
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)
    made = export(client, {"ratios": "2:3, 3:4", "quality": "basic"}).get_json()

    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])
    produced = [folder / entry["file"] for entry in made["files"]] + [folder / made["guide"]["file"]]
    assert all(path.is_file() for path in produced)

    # Changing the history is guarded like the rest of it: this session is the
    # administrator already, so what is missing here is the CSRF token.
    assert client.delete(f"/api/print/exports/{made['export_id']}").status_code == 403

    gone = client.delete(f"/api/print/exports/{made['export_id']}", headers={"X-CSRF-Token": csrf})
    assert gone.status_code == 200
    assert gone.get_json()["files_removed"] == 3
    assert not any(path.is_file() for path in produced), "files outlived their record"
    assert client.get("/api/print/exports").get_json()["total"] == 0
    assert client.delete(f"/api/print/exports/{made['export_id']}", headers={"X-CSRF-Token": csrf}).status_code == 404


def test_the_sweep_clears_what_is_past_its_keeping_date(tmp_path):
    """Retention is the point of the record: nothing else can do this safely."""
    import sqlite3
    from datetime import datetime, timedelta, timezone

    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    old = export(client, {"ratios": "2:3", "quality": "basic"}).get_json()
    fresh = export(client, {"ratios": "3:4", "quality": "basic"}).get_json()
    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])
    old_file = folder / old["files"][0]["file"]
    fresh_file = folder / fresh["files"][0]["file"]

    # Age one of them past the retention window.
    long_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    with sqlite3.connect(client.application.config["PRINT_DATABASE_PATH"]) as connection:
        connection.execute("UPDATE exports SET created_at = ? WHERE id = ?", (long_ago, old["export_id"]))

    swept = client.post("/api/print/exports/sweep", headers={"X-CSRF-Token": csrf})
    assert swept.status_code == 200
    assert swept.get_json()["exports"] == 1
    assert not old_file.is_file() and fresh_file.is_file()
    assert [e["id"] for e in client.get("/api/print/exports").get_json()["exports"]] == [fresh["export_id"]]

    # A file no record claims is swept on the same clock: nothing can name it,
    # so once it is past the window it is only taking up room. This is what
    # clears whatever was written before the history existed.
    import os

    stray = folder / "left_behind_by_an_older_version.jpg"
    stray.write_bytes(b"x")
    old_time = stray.stat().st_mtime - 60 * 86400
    os.utime(stray, (old_time, old_time))
    again = client.post("/api/print/exports/sweep", headers={"X-CSRF-Token": csrf}).get_json()
    assert again["unclaimed_files"] == 1 and not stray.is_file()
    # The claimed set comes from the database, not from one page of history:
    # reading a page would call every older export unclaimed and delete files
    # that are still on the books.
    catalog = client.application.extensions["print_catalog"]
    assert catalog.claimed_file_names() >= {fresh["files"][0]["file"], fresh["guide"]["file"]}
    # ...and a claimed file of the same age is left exactly where it is.
    assert fresh_file.is_file()

    # Zero days means keep everything, for a shop that archives its own.
    client.put("/api/print/settings", json={"retention_days": "0"}, headers={"X-CSRF-Token": csrf})
    with sqlite3.connect(client.application.config["PRINT_DATABASE_PATH"]) as connection:
        connection.execute("UPDATE exports SET created_at = ?", (long_ago,))
    assert client.post("/api/print/exports/sweep", headers={"X-CSRF-Token": csrf}).get_json()["exports"] == 0
    assert fresh_file.is_file()


def test_the_files_can_arrive_one_at_a_time(tmp_path):
    """A six-ratio export is minutes of watching nothing happen otherwise.

    The stream is opt-in: the shop application asks for one JSON object and
    that contract must not move under it.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    response = client.post(
        "/api/print/export?stream=1",
        data={"artwork": (artwork(), "art.png"), "spec": json.dumps({"ratios": "2:3, 3:4, 4:5", "quality": "basic"})},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.mimetype == "application/x-ndjson"

    events = [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line.strip()]
    kinds = [event["event"] for event in events]
    # One line to say what is coming, one per file as it lands, one to close.
    assert kinds == ["start", "file", "file", "file", "done"]
    assert events[0]["ratios"] == ["2:3", "3:4", "4:5"]
    assert [event["ratio"] for event in events[1:4]] == ["2:3", "3:4", "4:5"]
    assert all(event["url"].startswith("/print-outputs/") for event in events[1:4])

    # Each file is on disk by the time its line is sent -- that is the point.
    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])
    assert all((folder / event["file"]).is_file() for event in events[1:4])

    # The closing line carries everything the plain answer would have.
    done = events[-1]
    assert done["success"] is True
    assert done["export_id"] and done["guide"]["file"].endswith("printing_guide.txt")
    assert [entry["ratio"] for entry in done["files"]] == ["2:3", "3:4", "4:5"]

    # ...and the history was written once, at the end.
    assert client.get("/api/print/exports").get_json()["total"] == 1


def test_the_plain_answer_is_still_what_a_caller_gets_by_default(tmp_path):
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    plain = export(client, {"ratios": "2:3", "quality": "basic"})
    assert plain.mimetype == "application/json"
    assert plain.get_json()["files"][0]["ratio"] == "2:3"

    # An Accept header asks for it just as well as the query does.
    streamed = client.post(
        "/api/print/export",
        data={"artwork": (artwork(), "art.png"), "spec": json.dumps({"ratios": "2:3", "quality": "basic"})},
        content_type="multipart/form-data",
        headers={"Accept": "application/x-ndjson"},
    )
    assert streamed.mimetype == "application/x-ndjson"


def test_each_file_carries_how_long_it_took(tmp_path):
    """At full size the ratios differ by seconds.

    Which quality is worth its wait is a question the screen should answer
    without a stopwatch, so the time travels with the file and is stored
    beside it.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    made = export(client, {"ratios": "2:3, 3:4", "quality": "basic"}).get_json()
    assert all(isinstance(entry["ms"], int) and entry["ms"] >= 0 for entry in made["files"])

    # It reaches the stream a file at a time, too.
    streamed = client.post(
        "/api/print/export?stream=1",
        data={"artwork": (artwork(), "art.png"), "spec": json.dumps({"ratios": "2:3", "quality": "basic"})},
        content_type="multipart/form-data",
    )
    events = [json.loads(line) for line in streamed.get_data(as_text=True).splitlines() if line.strip()]
    assert "ms" in next(event for event in events if event["event"] == "file")

    # ...and it is still there when the export is looked back at.
    kept = client.get("/api/print/exports").get_json()["exports"]
    assert all("ms" in entry for run in kept for entry in run["files"])


def test_a_few_ratios_come_back_as_plain_files_a_buyer_can_open(tmp_path):
    """Etsy takes five files. Fewer than five means no archive at all.

    A .zip is a step between the buyer and what they paid for, so it is the
    fallback rather than the shape everything gets forced into.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    made = client.post(
        "/api/print/deliverables",
        data={"artwork": (artwork(), "art.png"), "spec": json.dumps({"ratios": "2:3, 3:4, 4:5", "quality": "basic"})},
        content_type="multipart/form-data",
    ).get_json()

    assert made["success"] is True
    assert made["delivery"] == "files"
    kinds = [entry["kind"] for entry in made["deliverables"]]
    assert kinds == ["print", "print", "print", "guide"]
    assert made["slots_used"] == 4 <= made["limits"]["max_files"]
    # Every one is a real file the shop can upload as it is.
    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])
    for entry in made["deliverables"]:
        assert (folder / entry["file"]).is_file()
        assert not entry["file"].endswith(".zip")


def test_more_ratios_than_slots_come_back_as_archives(tmp_path):
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    made = client.post(
        "/api/print/deliverables",
        data={
            "artwork": (artwork(), "art.png"),
            "spec": json.dumps({"ratios": "2:3, 3:4, 4:5, 11:14, ISO A, 1:1", "quality": "basic"}),
        },
        content_type="multipart/form-data",
    ).get_json()

    assert made["delivery"] == "archives"
    assert 0 < made["slots_used"] <= made["limits"]["max_files"]
    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])

    inside = []
    for entry in made["deliverables"]:
        assert entry["kind"] == "archive" and entry["file"].endswith(".zip")
        with zipfile.ZipFile(folder / entry["file"]) as bundle:
            names = bundle.namelist()
        # The note rides in every archive, so whichever is opened first has it.
        assert any(name.endswith("printing_guide.txt") for name in names)
        inside += [name for name in names if name.endswith(".jpg")]

    # All six ratios are delivered, none dropped to make them fit.
    assert len(inside) == 6


def test_a_ratio_decides_which_package_an_incoming_artwork_produces(tmp_path):
    """The shop's answer to "what is a 2:3 artwork worth selling as".

    Without this the export could only ever make one file for an artwork that
    named no set -- its own ratio -- which is almost never the product. It is
    also why a Compile produced a single image.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    # Nothing configured yet: one file, the artwork's own shape.
    plain = export(client, {"quality": "basic"}).get_json()
    assert [entry["ratio"] for entry in plain["files"]] == ["2:3"]

    pack = client.post(
        "/api/print/sets",
        json={"name": "Portrait pack", "mode": "chosen", "ratio_keys": ["2:3", "3:4", "4:5"], "quality": "basic"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["set"]

    two_three = next(r for r in client.get("/api/print/ratios").get_json()["ratios"] if r["key"] == "2:3")
    attached = client.patch(
        f"/api/print/ratios/{two_three['id']}",
        json={"default_set_id": pack["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert attached.status_code == 200
    assert attached.get_json()["ratio"]["default_set_id"] == pack["id"]

    # Now the same artwork, still naming no set, produces the whole package.
    packaged = export(client, {}).get_json()
    assert [entry["ratio"] for entry in packaged["files"]] == ["2:3", "3:4", "4:5"]
    assert packaged["quality"] == "basic"

    # A square artwork follows its own ratio's configuration, not this one.
    square = export(client, {"quality": "basic"}, size=(1000, 1000)).get_json()
    assert [entry["ratio"] for entry in square["files"]] == ["1:1"]

    # An explicit set still wins over the ratio's default.
    other = client.post(
        "/api/print/sets",
        json={"name": "Just one", "mode": "chosen", "ratio_keys": ["11:14"], "quality": "basic"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["set"]
    assert [e["ratio"] for e in export(client, {"set": other["id"]}).get_json()["files"]] == ["11:14"]


def test_a_print_file_can_be_looked_at_without_being_downloaded(tmp_path):
    """A print file is fifteen to twenty megabytes.

    A listing row, a gallery, a grid of six -- none of them can afford to pull
    the real thing just to show a thumbnail, so a small preview is made once
    and kept beside it.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)
    made = export(client, {"ratios": "2:3", "quality": "basic"}).get_json()
    name = made["files"][0]["file"]

    full = client.get(f"/print-outputs/{name}")
    preview = client.get(f"/print-outputs/{name}?preview=1")

    assert full.status_code == 200 and preview.status_code == 200
    assert len(preview.data) < len(full.data), "the preview is not smaller than the file"

    with Image.open(io.BytesIO(preview.data)) as shown:
        assert max(shown.size) <= 420
        # Still the artwork, not a placeholder.
        assert shown.getpixel((shown.width // 2, shown.height // 2)) != (255, 255, 255)

    # Made once: the second request serves what the first one wrote.
    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])
    previews = list(folder.glob("*.preview-*.jpg"))
    assert len(previews) == 1
    written_at = previews[0].stat().st_mtime
    assert client.get(f"/print-outputs/{name}?preview=1").status_code == 200
    assert previews[0].stat().st_mtime == written_at

    # A bigger one for a full-screen view, cached apart from the small one.
    # It is never larger than the source: a preview scales down and stops.
    larger = client.get(f"/print-outputs/{name}?preview=1200")
    assert larger.status_code == 200
    with Image.open(io.BytesIO(larger.data)) as wider:
        assert max(wider.size) <= 1200
        assert max(wider.size) >= max(shown.size)
    assert len(list(folder.glob("*.preview-*.jpg"))) == 2, "the two sizes share one cache file"

    # A note has nothing to show, and says so rather than failing oddly.
    assert client.get(f"/print-outputs/{made['guide']['file']}?preview=1").status_code == 415


def test_the_shop_wide_rule_decides_what_an_artwork_produces(tmp_path):
    """One control, not ten. A per-ratio setting nobody finishes wiring is a
    setting that leaves every automatic export at a single file."""
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    # Unwired: one file, the artwork's own shape.
    assert [e["ratio"] for e in export(client, {"quality": "basic"}).get_json()["files"]] == ["2:3"]
    assert client.get("/api/print/ratios").get_json()["default_set_id"] == ""

    pack = client.post(
        "/api/print/sets",
        json={"name": "House pack", "mode": "chosen", "ratio_keys": ["2:3", "3:4", "4:5"], "quality": "basic"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["set"]
    saved = client.put(
        "/api/print/settings",
        json={"default_set_id": str(pack["id"])},
        headers={"X-CSRF-Token": csrf},
    )
    assert saved.status_code == 200

    # Every shape now produces the pack, with nothing configured per ratio.
    assert [e["ratio"] for e in export(client, {}).get_json()["files"]] == ["2:3", "3:4", "4:5"]
    square = export(client, {}, size=(1000, 1000)).get_json()
    assert [e["ratio"] for e in square["files"]] == ["2:3", "3:4", "4:5"]
    assert client.get("/api/print/ratios").get_json()["default_set_id"] == str(pack["id"])

    # A ratio with its own answer overrides the shop-wide one -- the exception
    # the per-ratio setting exists for.
    only_square = client.post(
        "/api/print/sets",
        json={"name": "Square only", "mode": "chosen", "ratio_keys": ["1:1"], "quality": "basic"},
        headers={"X-CSRF-Token": csrf},
    ).get_json()["set"]
    one_to_one = next(r for r in client.get("/api/print/ratios").get_json()["ratios"] if r["key"] == "1:1")
    client.patch(
        f"/api/print/ratios/{one_to_one['id']}",
        json={"default_set_id": only_square["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert [e["ratio"] for e in export(client, {}, size=(1000, 1000)).get_json()["files"]] == ["1:1"]
    # ...and a shape without its own still follows the shop.
    assert [e["ratio"] for e in export(client, {}).get_json()["files"]] == ["2:3", "3:4", "4:5"]


def test_a_set_gets_every_size_for_every_artwork(tmp_path):
    """Three artworks sold as one listing need three artworks' worth of files.

    The export takes a single artwork, so a set runs it once per image -- but
    the packing has to see all of them at once. Eighteen files against five
    slots is a decision that cannot be made six at a time.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    made = []
    for index in range(3):
        answer = export(client, {"ratios": "2:3, 3:4", "quality": "basic"}, size=(1200 + index, 1800)).get_json()
        made += [entry["file"] for entry in answer["files"] if entry["success"]]
    assert len(made) == 6, "one artwork's files went missing"

    packed = client.post("/api/print/package", json={"files": made}).get_json()

    assert packed["success"] is True
    # Every file is delivered; none dropped to make the set fit.
    delivered = [name for entry in packed["deliverables"] for name in (entry.get("files") or [entry.get("file")])]
    if packed["delivery"] == "archives":
        assert sorted(name for name in delivered if name in made) == sorted(made)
    else:
        assert len([e for e in packed["deliverables"] if e["kind"] == "print"]) == 6
    assert packed["slots_used"] <= packed["limits"]["max_files"]


def test_packing_refuses_a_name_that_is_not_one_of_ours(tmp_path):
    client, _ = studio(tmp_path)
    assert client.post("/api/print/package", json={"files": ["../secrets.txt"]}).status_code == 400
    assert client.post("/api/print/package", json={"files": ["nope.jpg"]}).status_code == 404
    assert client.post("/api/print/package", json={"files": []}).status_code == 400


def test_a_set_does_not_deliver_three_files_under_one_name(tmp_path):
    """Stripping the batch id leaves a set's files all called the same thing.

    Three artworks at 2:3 all become "2x3_ratio_24x36_inch.jpg", and a zip
    with a repeated name hands the buyer one of them. Two thirds of what they
    paid for would simply not be in the download.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    small_ratios(client, csrf)

    made = []
    for index in range(3):
        answer = export(client, {"ratios": "2:3, 3:4", "quality": "basic"}, size=(1200 + index, 1800)).get_json()
        made += [entry["file"] for entry in answer["files"] if entry["success"]]

    # Force archiving: more files than the allowance holds as plain images.
    client.put("/api/print/settings", json={"etsy_max_files": "2"}, headers={"X-CSRF-Token": csrf})
    packed = client.post("/api/print/package", json={"files": made}).get_json()
    assert packed["delivery"] == "archives"

    folder = Path(client.application.config["PRINT_OUTPUT_FOLDER"])
    inside = []
    for entry in packed["deliverables"]:
        with zipfile.ZipFile(folder / entry["file"]) as bundle:
            names = bundle.namelist()
            assert len(names) == len(set(names)), f"{entry['file']} repeats a name"
            inside += names

    # Every one of the six files reached the buyer under a name of its own.
    assert len(inside) == len(set(inside)) == 6


def test_a_model_without_its_weights_is_not_offered(tmp_path, monkeypatch):
    """The binary is one thing; the model it is asked for is another.

    Real-ESRGAN takes any `.param`/`.bin` pair by name, so a second model is a
    file drop rather than a code change -- which also means the files can
    simply not be there. A quality offered in that state finds its GPU, prints
    its details and fails on a missing file, minutes into a render. So the
    check is for the pair, and an absent model is greyed out with the reason.
    """
    from services import print_export_service as export_service

    program = tmp_path / "realesrgan-ncnn-vulkan.exe"
    program.write_bytes(b"")
    models = tmp_path / "models"
    models.mkdir()
    for half in ("param", "bin"):
        (models / f"realesrgan-x4plus.{half}").write_bytes(b"")

    monkeypatch.setattr(export_service, "TOOL_CANDIDATES", {"realesrgan": (str(program),), "topaz": ()})
    monkeypatch.setattr(export_service.shutil, "which", lambda name: None)
    found = export_service.resolved_tools({})

    offered = {q["key"]: q for q in export_service.available_qualities(found)}
    assert offered["ai"]["available"] is True
    # Same binary, same everything -- only the weights are absent.
    assert offered["ultrasharp"]["available"] is False
    assert "4x-UltraSharp-fp16" in offered["ultrasharp"]["reason"]

    # Half a model is not a model: ncnn needs the network and the weights.
    (models / "4x-UltraSharp-fp16.param").write_bytes(b"")
    assert export_service.model_is_present(str(program), "4x-UltraSharp-fp16") is False
    (models / "4x-UltraSharp-fp16.bin").write_bytes(b"")

    offered = {q["key"]: q for q in export_service.available_qualities(found)}
    assert offered["ultrasharp"]["available"] is True
    assert offered["ultrasharp"]["reason"] == ""


def test_each_ai_quality_asks_the_upscaler_for_its_own_model(tmp_path, monkeypatch):
    """Two qualities, one binary -- what separates them is the `-n` name.

    Worth pinning: the model name used to be a literal inside the call, so a
    second model could be listed on the screen and still silently render
    through the first.
    """
    from PIL import Image as PillowImage

    from services import print_export_service as export_service

    asked: list[str] = []

    def fake_run(program, arguments, label):
        name = arguments[arguments.index("-n") + 1]
        asked.append(name)
        output = Path(arguments[arguments.index("-o") + 1])
        PillowImage.new("RGB", (400, 600), (10, 20, 30)).save(output, "PNG")

    monkeypatch.setattr(export_service, "_run_external", fake_run)
    source = PillowImage.new("RGB", (100, 150), (200, 90, 70))
    tools = {"realesrgan": str(tmp_path / "realesrgan-ncnn-vulkan.exe")}

    # Read from the table, so a model added later is covered here the day it
    # is added rather than the day someone remembers this test.
    models = [(q["key"], q["model"]) for q in export_service.QUALITIES if q.get("needs") == "realesrgan"]
    assert len(models) >= 3, "the ncnn qualities are no longer coming from QUALITIES"

    for quality, expected in models:
        result = export_service.scale(source, 200, 300, quality, tools)
        assert result.size == (200, 300)
        assert asked[-1] == expected, f"{quality} rendered through {asked[-1]}"


def test_a_checkpoint_of_another_architecture_is_refused_not_converted(tmp_path, monkeypatch):
    """A conversion that "succeeds" into noise is the expensive kind of wrong.

    The models are all the same network with different numbers, so the weights
    are written against a template param. Nothing in that process would notice
    a checkpoint of some other architecture -- it would write a file, the
    binary would load it, and the upscale would come back as garbage. Which
    nobody sees until a customer has the print. So the shapes are checked layer
    by layer and a mismatch stops it.
    """
    import torch

    from tools.convert_esrgan_to_ncnn import convert, state_dict, weight_order

    # The real checkpoint maps onto the template exactly: 351 convolutions.
    real = Path("tools/realesrgan/models/4x_NMKD-Siax_200k_transfered.pth")
    if real.is_file():
        assert len(weight_order(state_dict(real))) == 351

    # A network shaped like RRDBNet but two blocks deep, not twenty-three.
    small = {"conv_first.weight": torch.zeros(64, 3, 3, 3), "conv_first.bias": torch.zeros(64)}
    for block in range(2):
        for dense in (1, 2, 3):
            for conv in range(1, 6):
                stem = f"RRDB_trunk.{block}.RDB{dense}.conv{conv}"
                small[f"{stem}.weight"] = torch.zeros(32, 64, 3, 3)
                small[f"{stem}.bias"] = torch.zeros(32)
    checkpoint = tmp_path / "wrong.pth"
    torch.save(small, checkpoint)

    with pytest.raises(SystemExit, match="different architecture"):
        convert(checkpoint, "should-never-be-written")

    from tools.convert_esrgan_to_ncnn import MODELS
    assert not (MODELS / "should-never-be-written.bin").exists(), "a refused conversion still wrote a file"
