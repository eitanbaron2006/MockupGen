import json
import sys
from pathlib import Path

import pytest
from PIL import Image, features

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from test_mockup_api import build_client, image_bytes  # noqa: E402


def write_room_template(
    templates_folder: Path,
    template_id: str,
    *,
    canvas: tuple[int, int] = (200, 300),
    area: tuple[int, int, int, int] = (40, 60, 80, 120),
    orientation: str = "portrait",
) -> dict:
    """A room-sized classic template, big enough to be a real render."""
    template_folder = templates_folder / template_id
    template_folder.mkdir(parents=True)
    width, height = canvas
    x, y, area_width, area_height = area
    Image.new("RGBA", canvas, (210, 205, 195, 255)).save(template_folder / "background.png")
    Image.new("RGBA", canvas, (210, 205, 195, 255)).save(template_folder / "preview.png")
    manifest = {
        "template_id": template_id,
        "name": template_id,
        "canvas_width": width,
        "canvas_height": height,
        "artwork_area": {"x": x, "y": y, "width": area_width, "height": area_height},
        "fit_mode": "cover",
        "background": "background.png",
        "preview": "preview.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "orientation": orientation,
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def register(client, manifest: dict, category_name: str) -> dict:
    """Put a template on a catalog shelf, which is what makes it MAIN or not."""
    from flask import current_app

    with client.application.app_context():
        catalog = current_app.extensions["catalog_service"]
        category = catalog.get_or_create_category(category_name)
        record = catalog.create_template(
            {
                "template_id": manifest["template_id"],
                "name": manifest["name"],
                "category_id": category["id"],
                "status": "active",
                "canvas_width": manifest["canvas_width"],
                "canvas_height": manifest["canvas_height"],
                "artwork_area": manifest["artwork_area"],
                "fit_mode": manifest["fit_mode"],
                "orientation": manifest["orientation"],
            }
        )
        return {**record, "category_id": category["id"]}


def catalog_of(client):
    from flask import current_app

    with client.application.app_context():
        return current_app.extensions["catalog_service"]


def studio(tmp_path):
    return build_client(tmp_path, ADMIN_PASSWORD="test-admin", SECRET_KEY="test-secret")


def post_bundle(client, spec: dict | None = None, *, size: tuple[int, int] = (400, 600)):
    data: dict = {"artwork": (image_bytes(size, (20, 90, 160, 255)), "art.png")}
    if spec is not None:
        data["spec"] = json.dumps(spec)
    return client.post(
        "/api/mockups/listing-bundle", data=data, content_type="multipart/form-data"
    )


def admin_login(client) -> str:
    response = client.post("/api/admin/login", json={"password": "test-admin"})
    assert response.status_code == 200
    return response.get_json()["csrf_token"]


def two_shelves(client, paths) -> tuple[dict, dict]:
    """One MAIN mockup and one ordinary one, which is the whole distinction."""
    main = register(client, write_room_template(paths["TEMPLATES_FOLDER"], "room_main"), "Main Vertical")
    plain = register(client, write_room_template(paths["TEMPLATES_FOLDER"], "room_plain"), "Vertical Wall Art")
    return main, plain


def mockups_of(payload: dict) -> list[dict]:
    return [item for item in payload["items"] if item["kind"] == "mockup"]


def guide_of(payload: dict) -> dict | None:
    return next((item for item in payload["items"] if item["kind"] == "size_guide"), None)


def upload_guide(client, csrf, *, ratio="2:3", size=(400, 500)):
    return client.post(
        "/api/admin/size-guides",
        data={"guide": (image_bytes(size, (250, 249, 246, 255)), "guide.png"), "ratio": ratio},
        headers={"X-CSRF-Token": csrf},
        content_type="multipart/form-data",
    )


def test_auto_bundle_keeps_main_mockups_for_the_main_image(tmp_path):
    """MAIN is the picture Etsy shows in search, so it leads and never fills in.

    Ranking scores aspect-ratio fit alone and would happily spend a MAIN mockup
    on a filler slot; the rule is applied on top of that ranking.
    """
    client, paths = studio(tmp_path)
    main, plain = two_shelves(client, paths)
    register(client, write_room_template(paths["TEMPLATES_FOLDER"], "room_third"), "Vertical Wall Art")

    payload = post_bundle(client).get_json()
    mockups = mockups_of(payload)

    assert mockups[0]["hero"] is True
    assert mockups[0]["template_id"] == main["template_id"]
    assert all(not item["hero"] for item in mockups[1:])
    assert main["template_id"] not in {item["template_id"] for item in mockups[1:]}
    assert guide_of(payload) is not None
    # The picture is named after the mockup it came from, and nothing else.
    assert mockups[0]["label"] == main["name"]


def test_a_saved_set_decides_which_mockups_a_listing_gets(tmp_path):
    client, paths = studio(tmp_path)
    main, plain = two_shelves(client, paths)
    second = register(
        client, write_room_template(paths["TEMPLATES_FOLDER"], "room_two"), "Vertical Wall Art"
    )
    listing_set = catalog_of(client).create_listing_set(
        {
            "name": "Vertical listing",
            "orientation": "portrait",
            "product_type": "Printable Wall Art",
            "items": [
                {"kind": "mockup", "hero": True, "template_id": main["template_id"]},
                {"kind": "mockup", "template_id": plain["template_id"]},
                {"kind": "mockup", "template_id": second["template_id"]},
            ],
        }
    )

    payload = post_bundle(client, {"set": listing_set["id"]}).get_json()

    assert payload["success"] is True
    assert [item["template_id"] for item in payload["items"]] == [
        main["template_id"],
        plain["template_id"],
        second["template_id"],
    ]
    # The main image leads, whatever order the set was built in.
    assert payload["items"][0]["hero"] is True


def test_a_set_can_draw_several_mockups_from_a_category(tmp_path):
    client, paths = studio(tmp_path)
    for index in range(4):
        register(
            client,
            write_room_template(paths["TEMPLATES_FOLDER"], f"room_{index}"),
            "Vertical Wall Art",
        )
    catalog = catalog_of(client)
    category = catalog.get_or_create_category("Vertical Wall Art")
    listing_set = catalog.create_listing_set(
        {
            "name": "Three rooms",
            "items": [{"kind": "mockup", "category_id": category["id"], "count": 3}],
        }
    )

    payload = post_bundle(client, {"set": listing_set["id"]}).get_json()

    assert payload["success"] is True
    assert len(payload["items"]) == 3
    assert len({item["template_id"] for item in payload["items"]}) == 3


def test_only_a_main_mockup_can_be_the_main_image(tmp_path):
    client, paths = studio(tmp_path)
    main, plain = two_shelves(client, paths)
    csrf = admin_login(client)

    def save(name, items):
        return client.post(
            "/api/admin/listing-sets",
            json={"name": name, "items": items},
            headers={"X-CSRF-Token": csrf},
        )

    filler = save("Wrong", [{"kind": "mockup", "template_id": main["template_id"]}])
    assert filler.status_code == 400
    assert "hero" in filler.get_json()["error"].lower()

    wrong_hero = save("Also wrong", [{"kind": "mockup", "hero": True, "template_id": plain["template_id"]}])
    assert wrong_hero.status_code == 400
    assert "main" in wrong_hero.get_json()["error"].lower()

    accepted = save(
        "Right",
        [
            {"kind": "mockup", "hero": True, "template_id": main["template_id"]},
            {"kind": "mockup", "template_id": plain["template_id"]},
            {"kind": "size_guide"},
        ],
    )
    assert accepted.status_code == 201
    assert accepted.get_json()["set"]["items"][0]["hero"] is True


def test_a_listing_has_one_main_image_one_chart_and_at_most_eighteen_mockups():
    from services.listing_set_service import MAX_MOCKUPS, ListingSetError, normalize_items

    def check(items, main_ids=()):
        return normalize_items(
            items,
            is_main_template=lambda template_id: template_id in main_ids,
            is_main_category=lambda _category_id: False,
        )

    with pytest.raises(ListingSetError, match="one hero"):
        check(
            [
                {"kind": "mockup", "hero": True, "template_id": "a"},
                {"kind": "mockup", "hero": True, "template_id": "b"},
            ],
            main_ids={"a", "b"},
        )

    with pytest.raises(ListingSetError, match="one size guide"):
        check([{"kind": "size_guide"}, {"kind": "size_guide"}])

    with pytest.raises(ListingSetError, match=str(MAX_MOCKUPS)):
        check([{"kind": "mockup", "template_id": f"t{n}"} for n in range(MAX_MOCKUPS + 1)])

    assert len(check([{"kind": "mockup", "template_id": f"t{n}"} for n in range(MAX_MOCKUPS)])) == MAX_MOCKUPS


def test_the_size_guide_comes_from_the_library_matched_to_the_artwork(tmp_path):
    """The ratio says which way round the chart is drawn, so one lookup answers both."""
    client, paths = studio(tmp_path)
    two_shelves(client, paths)
    csrf = admin_login(client)
    assert upload_guide(client, csrf, ratio="3:2").status_code == 201
    portrait = upload_guide(client, csrf, ratio="2:3")
    assert portrait.status_code == 201
    guide_id = portrait.get_json()["guide"]["id"]

    guide = guide_of(post_bundle(client, size=(400, 600)).get_json())

    assert guide["success"] is True
    assert guide["guide_id"] == guide_id
    assert guide["source"] == "upload"
    assert (paths["OUTPUT_FOLDER"] / guide["output_url"].rsplit("/", 1)[-1]).is_file()


def test_a_landscape_artwork_gets_the_landscape_chart(tmp_path):
    client, paths = studio(tmp_path)
    register(
        client,
        write_room_template(
            paths["TEMPLATES_FOLDER"], "room_wide", canvas=(300, 200),
            area=(60, 40, 120, 80), orientation="landscape",
        ),
        "Main Horizontal",
    )
    csrf = admin_login(client)
    assert upload_guide(client, csrf, ratio="2:3").status_code == 201
    landscape = upload_guide(client, csrf, ratio="3:2")

    guide = guide_of(post_bundle(client, size=(600, 400)).get_json())

    assert guide["guide_id"] == landscape.get_json()["guide"]["id"]


def test_a_missing_size_guide_costs_one_picture_not_the_listing(tmp_path):
    client, paths = studio(tmp_path)
    two_shelves(client, paths)

    response = post_bundle(client)

    assert response.status_code == 207
    payload = response.get_json()
    assert guide_of(payload)["success"] is False
    assert "library" in guide_of(payload)["error"].lower()
    assert all(item["success"] for item in mockups_of(payload))


def test_size_guide_library_needs_only_a_ratio(tmp_path):
    """A 3:2 chart is a landscape chart -- a second field could only disagree."""
    client, paths = studio(tmp_path)
    csrf = admin_login(client)
    guide = upload_guide(client, csrf, ratio="3:2").get_json()["guide"]
    assert guide["orientation"] == "landscape"

    listing = client.get("/api/admin/size-guides").get_json()
    assert [entry["id"] for entry in listing["guides"]] == [guide["id"]]
    assert "3:2" in listing["ratios"] and "2:3" in listing["ratios"]

    refused = upload_guide(client, csrf, ratio="banana")
    assert refused.status_code == 400

    asset = client.get(f"/api/admin/size-guides/{guide['id']}/asset")
    assert asset.status_code == 200
    assert asset.data[:4] == b"\x89PNG"
    # Windows will not delete a file the served response still holds open.
    asset.close()

    removed = client.delete(
        f"/api/admin/size-guides/{guide['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert removed.status_code == 200
    assert client.get("/api/admin/size-guides").get_json()["guides"] == []
    assert not list(Path(client.application.config["SIZE_GUIDES_FOLDER"]).glob("guide_*"))


def test_listing_sets_are_created_listed_renamed_and_deleted(tmp_path):
    client, paths = studio(tmp_path)
    _, plain = two_shelves(client, paths)
    csrf = admin_login(client)

    created = client.post(
        "/api/admin/listing-sets",
        json={
            "name": "Portrait listing",
            "orientation": "portrait",
            "product_type": "Printable Wall Art",
            "items": [{"kind": "mockup", "template_id": plain["template_id"]}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201
    set_id = created.get_json()["set"]["id"]

    duplicate = client.post(
        "/api/admin/listing-sets",
        json={"name": "Portrait listing", "items": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert duplicate.status_code == 409

    listing = client.get("/api/admin/listing-sets?orientation=portrait").get_json()
    assert len(listing["sets"]) == 1
    # The product types are the shop's, not the shelves the mockups sit on.
    assert "Printable Wall Art" in listing["product_types"]
    assert "Lightroom Presets" in listing["product_types"]
    assert client.get("/api/admin/listing-sets?orientation=landscape").get_json()["sets"] == []

    renamed = client.patch(
        f"/api/admin/listing-sets/{set_id}",
        json={"name": "Renamed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert renamed.get_json()["set"]["name"] == "Renamed"

    assert client.delete(
        f"/api/admin/listing-sets/{set_id}", headers={"X-CSRF-Token": csrf}
    ).status_code == 200
    assert client.get("/api/admin/listing-sets").get_json()["sets"] == []
    assert client.patch(
        f"/api/admin/listing-sets/{set_id}", json={"name": "x"}, headers={"X-CSRF-Token": csrf}
    ).status_code == 404


def test_size_family_follows_the_artwork_shape():
    from services.listing_bundle_service import guide_ratio_key, size_family_for_ratio

    assert size_family_for_ratio(400 / 600)[0] == "2:3"
    assert size_family_for_ratio(800 / 1000)[0] == "4:5"
    assert size_family_for_ratio(1.0)[0] == "1:1"
    # A landscape piece belongs to the same family, turned round.
    name, _, sizes = size_family_for_ratio(600 / 400)
    assert name == "2:3"
    assert all(size.width > size.height for size in sizes)
    # ...and asks the library for the chart drawn that way round.
    assert guide_ratio_key(400 / 600) == "2:3"
    assert guide_ratio_key(600 / 400) == "3:2"
    assert guide_ratio_key(1.0) == "1:1"


@pytest.mark.skipif(not features.check("avif"), reason="Pillow build without AVIF")
def test_saved_avif_is_really_avif(tmp_path):
    from services.simple_mockup_service import save_render_image

    name = save_render_image(Image.new("RGBA", (8, 8), (10, 20, 30, 255)), tmp_path, "avif", 80)

    assert name.endswith(".avif")
    with Image.open(tmp_path / name) as saved:
        assert saved.format == "AVIF"


def test_bundle_honours_the_requested_output_format(tmp_path):
    client, paths = studio(tmp_path)
    two_shelves(client, paths)

    payload = post_bundle(client, {"format": "webp"}).get_json()

    for item in mockups_of(payload):
        assert item["output_url"].endswith(".webp")
        with Image.open(paths["OUTPUT_FOLDER"] / item["output_url"].rsplit("/", 1)[-1]) as saved:
            assert saved.format == "WEBP"

    assert post_bundle(client, {"format": "tiff"}).status_code == 400


def test_a_bundle_names_no_uploaded_file(tmp_path):
    client, paths = studio(tmp_path)
    two_shelves(client, paths)

    payload = post_bundle(client).get_json()

    assert payload["artwork_ratio"] == pytest.approx(400 / 600, rel=1e-3)
    body = json.dumps(payload)
    assert str(paths["UPLOAD_FOLDER"]) not in body
    assert "art.png" not in body


def test_missing_artwork_and_unknown_set_are_refused(tmp_path):
    client, paths = studio(tmp_path)
    two_shelves(client, paths)

    assert client.post(
        "/api/mockups/listing-bundle", data={}, content_type="multipart/form-data"
    ).status_code == 400
    assert post_bundle(client, {"set": 9999}).status_code == 404
