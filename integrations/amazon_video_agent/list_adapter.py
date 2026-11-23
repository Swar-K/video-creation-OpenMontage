"""Adapter for Amazon Video Agent Top-N list projects.

A list project references ordinary product folders. This adapter stages their
supplied media into a temporary, namespaced project without modifying Ad Agent.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from integrations.amazon_video_agent.importer import _parse_script, import_project
from lib.paths import PROJECTS_DIR
from integrations.amazon_video_agent.importer import MEDIA_TYPES


def _list_config(source: Path) -> dict[str, Any]:
    data = yaml.safe_load((source / "list.yaml").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("list.yaml must contain a YAML mapping")
    products = data.get("products")
    if not isinstance(products, list) or not 3 <= len(products) <= 9:
        raise ValueError("list.yaml must contain 3 to 9 products")
    return data


def _product_ids(config: dict[str, Any]) -> list[str]:
    result = []
    for item in config["products"]:
        value = item.get("id") or item.get("asin") if isinstance(item, dict) else item
        product_id = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,39}", product_id):
            raise ValueError(f"Invalid product folder id in list.yaml: {product_id!r}")
        if product_id in result:
            raise ValueError(f"Duplicate product folder in list.yaml: {product_id}")
        result.append(product_id)
    return result


def _script_product_map(script_path: Path, sections: list[dict[str, str]]) -> dict[str, int | None]:
    """Read Ad Agent's inline product:N marker; keep general/all lines unassigned."""
    wanted = {section["id"] for section in sections}
    result: dict[str, int | None] = {}
    line_re = re.compile(r"^\[(?P<id>[^\]]+)\]\s*(?P<body>.*)$")
    product_re = re.compile(r"\|\s*product\s*:\s*(?P<product>all|[1-9])(?:\s*\||\s*$)", re.I)
    for raw in script_path.read_text(encoding="utf-8").splitlines():
        match = line_re.match(raw.strip())
        if not match or match.group("id") not in wanted:
            continue
        product = product_re.search(match.group("body"))
        if product and product.group("product").lower() != "all":
            result[match.group("id")] = int(product.group("product"))
        else:
            result[match.group("id")] = None
    return result


def _copy_product_assets(source: Path, stage_assets: Path, product_ids: list[str]) -> dict[int, list[Path]]:
    by_rank: dict[int, list[Path]] = {}
    for rank, product_id in enumerate(product_ids, 1):
        product_root = source.parent / product_id
        facts = product_root / "facts.yaml"
        assets = product_root / "assets"
        if not facts.is_file():
            raise FileNotFoundError(f"List product {product_id} is missing {facts}")
        if not assets.is_dir():
            raise FileNotFoundError(f"List product {product_id} is missing {assets}/")
        copied = []
        for media in sorted(assets.iterdir()):
            if not media.is_file() or media.suffix.lower() not in MEDIA_TYPES:
                continue
            target = stage_assets / f"P{rank:02d}__{media.name}"
            shutil.copy2(media, target)
            copied.append(target)
        if not copied:
            raise FileNotFoundError(f"List product {product_id} has no supported files in assets/")
        by_rank[rank] = copied
    return by_rank


def _write_staged_list(source: Path, stage: Path) -> Path:
    config = _list_config(source)
    product_ids = _product_ids(config)
    if not (source / "script.md").is_file():
        raise FileNotFoundError(f"List project is missing {source / 'script.md'}")

    stage.mkdir(parents=True, exist_ok=True)
    (stage / ".openmontage-list-stage").write_text("generated\n", encoding="utf-8")
    assets_dir = stage / "assets"
    assets_dir.mkdir()
    product_assets = _copy_product_assets(source, assets_dir, product_ids)

    # Optional shared footage in the list folder is kept separate from product media.
    shared_assets = []
    source_shared = source / "assets"
    if source_shared.is_dir():
        for media in sorted(source_shared.iterdir()):
            if media.is_file() and media.suffix.lower() in MEDIA_TYPES:
                target = assets_dir / f"X__{media.name}"
                shutil.copy2(media, target)
                shared_assets.append(target)

    title, sections = _parse_script(source / "script.md")
    product_map = _script_product_map(source / "script.md", sections)
    scene_lines = []
    for index, section in enumerate(sections, 1):
        rank = product_map.get(section["id"])
        if rank is None:
            rank_match = re.fullmatch(r"item_(\d+)", section["label"], re.I)
            rank = int(rank_match.group(1)) if rank_match else None

        if rank is not None:
            if rank not in product_assets:
                raise ValueError(
                    f"Script section {section['id']} references product {rank}, "
                    f"but list.yaml contains only {len(product_ids)} products"
                )
            candidates = product_assets[rank]
        else:
            # General intro/recap sections may show one representative image per product.
            candidates = [files[0] for files in product_assets.values()]
            candidates.extend(shared_assets[:1])

        videos = [p for p in candidates if MEDIA_TYPES.get(p.suffix.lower()) == "video"]
        images = [p for p in candidates if MEDIA_TYPES.get(p.suffix.lower()) == "image"]
        # Rotate source shots across narration sections so a product's first
        # image is not repeated for every line in its list entry.
        chosen = []
        if videos:
            chosen.append(videos[(index - 1) % len(videos)])
        if images:
            chosen.append(images[(index - 1) % len(images)])
        if not chosen:
            chosen = candidates[:1]
        refs = "; ".join(p.name for p in chosen)
        scene_lines.append(
            f"[s{index:02d}] type: product | ref: {refs or 'none'} | "
            f"prompt: List section {section['label']}; use only the referenced product footage."
        )

    facts = {
        "asin": source.name,
        "product_name": str(config.get("title") or title),
        "category": str(config.get("category") or ""),
        "audience": str(config.get("audience") or ""),
        "affiliate_url": "",
        "manufacturer_claims": [],
        "angle": "list",
    }
    (stage / "facts.yaml").write_text(
        yaml.safe_dump(facts, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    shutil.copy2(source / "script.md", stage / "script.md")
    (stage / "scenes.md").write_text("\n".join(scene_lines) + "\n", encoding="utf-8")
    voice = source / "out" / "voice.wav"
    if voice.is_file():
        (stage / "out").mkdir(exist_ok=True)
        shutil.copy2(voice, stage / "out" / "voice.wav")
    shutil.copy2(source / "list.yaml", stage / "list.yaml")
    return stage


def import_selected_project(
    amazon_project: Path,
    project_id: str,
    *,
    projects_dir: Path | None = None,
    media_mode: str = "copy",
) -> Path:
    source = amazon_project.expanduser().resolve()
    if not (source / "list.yaml").is_file():
        return import_project(
            source, project_id, projects_dir=projects_dir, media_mode=media_mode
        )

    project_root = projects_dir or PROJECTS_DIR
    project_root.mkdir(parents=True, exist_ok=True)
    if media_mode == "copy":
        with tempfile.TemporaryDirectory(prefix="openmontage-list-") as temp:
            staged = _write_staged_list(source, Path(temp) / "source")
            project = import_project(
                staged, project_id, projects_dir=project_root, media_mode="copy"
            )
    else:
        stage_root = project_root / ".amazon-list-staging"
        stage = stage_root / project_id
        if stage.exists():
            marker = stage / ".openmontage-list-stage"
            if not marker.is_file():
                raise FileExistsError(f"Refusing to replace unrecognized staging directory: {stage}")
            shutil.rmtree(stage)
        staged = _write_staged_list(source, stage)
        project = import_project(
            staged, project_id, projects_dir=project_root, media_mode="symlink"
        )

    source_link = project / "amazon_source"
    if source_link.is_symlink():
        source_link.unlink()
    source_link.symlink_to(source, target_is_directory=True)
    return project
