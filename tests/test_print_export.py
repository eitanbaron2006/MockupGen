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

    # The qualities say what this machine can actually deliver.
    qualities = {quality["key"]: quality for quality in offered["qualities"]}
    assert qualities["bicubic"]["available"] is True
    assert qualities["ai"]["available"] is False
    assert qualities["ai"]["reason"]


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
