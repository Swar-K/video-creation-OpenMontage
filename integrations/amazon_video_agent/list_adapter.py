"""Adapters for Amazon Video Agent Single, Compare and Top-N projects.

Multi-product projects reference existing product folders. Staging is isolated:
the Ad Agent checkout is read-only and never receives generated narration.
"""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from integrations.amazon_video_agent.importer import MEDIA_TYPES, _parse_script, import_project
from lib.paths import PROJECTS_DIR


PROJECT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,59}")


def _multi_config(source: Path) -> tuple[str, dict[str, Any]]:
    has_list = (source / "list.yaml").is_file()
    has_compare = (source / "compare.yaml").is_file()
    if has_list and has_compare:
        raise ValueError("A multi-product project cannot contain both list.yaml and compare.yaml")
    if not has_list and not has_compare:
        raise FileNotFoundError("Multi-product project requires list.yaml or compare.yaml")
    config_name = "list.yaml" if has_list else "compare.yaml"
    path = source / config_name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{config_name} must contain a YAML mapping")
    products = data.get("products")
    if not isinstance(products, list):
        raise ValueError(f"{config_name} must contain a products list")
    limits = (3, 9) if config_name == "list.yaml" else (2, 3)
    if not limits[0] <= len(products) <= limits[1]:
        raise ValueError(f"{config_name} must contain {limits[0]} to {limits[1]} products")
    return config_name, data


def _product_ids(config: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in config["products"]:
        value = (item.get("id") or item.get("asin")) if isinstance(item, dict) else item
        product_id = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,39}", product_id):
            raise ValueError(f"Invalid product folder id in project YAML: {product_id!r}")
        if product_id in result:
            raise ValueError(f"Duplicate product folder in project YAML: {product_id}")
        result.append(product_id)
    return result


def _script_product_map(script_path: Path, sections: list[dict[str, str]]) -> dict[str, int | None]:
    """Map Ad Agent's inline product:N metadata to the listed product rank."""
    wanted = {section["id"] for section in sections}
    result: dict[str, int | None] = {}
    line_re = re.compile(r"^\[(?P<id>[^\]]+)\]\s*(?P<body>.*)$")
    product_re = re.compile(r"\|\s*product\s*:\s*(?P<product>all|[1-9])(?:\s*\||\s*$)", re.I)
    for raw in script_path.read_text(encoding="utf-8").splitlines():
        match = line_re.match(raw.strip())
        if not match or match.group("id") not in wanted:
            continue
        product = product_re.search(match.group("body"))
        result[match.group("id")] = (
            int(product.group("product"))
            if product and product.group("product").lower() != "all"
            else None
        )
    return result


def _script_asset_map(
    script_path: Path, sections: list[dict[str, str]], asset_index: dict[str, Path]
) -> dict[str, list[Path]]:
    """Resolve Ad Agent's P1-A03 / P2-A04 / X-A01 IDs to original media files.

    The Axx index is assigned from the sorted supported files in each product
    assets/ directory before selection, so preserve that exact ordering.
    """
    wanted = {section["id"] for section in sections}
    result: dict[str, list[Path]] = {}
    line_re = re.compile(r"^\\[(?P<id>[^\\]]+)\\]\\s*(?P<body>.*)$")
    assets_re = re.compile(r"\\|\\s*assets\\s*:\\s*(?P<assets>[^|]+)", re.I)
    for raw in script_path.read_text(encoding="utf-8").splitlines():
        match = line_re.match(raw.strip())
        if not match or match.group("id") not in wanted:
            continue
        assets = assets_re.search(match.group("body"))
        if not assets:
            continue
        resolved = []
        for asset_id in [x.strip() for x in assets.group("assets").split(",") if x.strip()]:
            if asset_id not in asset_index:
                raise FileNotFoundError(
                    f"Script section {match.group('id')} references unknown Ad Agent asset ID {asset_id!r}"
                )
            resolved.append(asset_index[asset_id])
        if resolved:
            result[match.group("id")] = resolved
    return result


def _copy_product_assets(
    source: Path, stage_assets: Path, product_ids: list[str]
) -> tuple[dict[int, list[Path]], dict[str, Path]]:
    by_rank: dict[int, list[Path]] = {}
    asset_index: dict[str, Path] = {}
    for rank, product_id in enumerate(product_ids, 1):
        product_root = source.parent / product_id
        facts = product_root / "facts.yaml"
        assets = product_root / "assets"
        if not facts.is_file():
            raise FileNotFoundError(f"Product {product_id} is missing {facts}")
        if not assets.is_dir():
            raise FileNotFoundError(f"Product {product_id} is missing {assets}/")
        media_files = sorted(
            p for p in assets.iterdir()
            if p.is_file() and p.suffix.lower() in MEDIA_TYPES
            and MEDIA_TYPES[p.suffix.lower()] in {"image", "video"}
        )
        copied: list[Path] = []
        for asset_number, media in enumerate(media_files, 1):
            target = stage_assets / f"P{rank:02d}__{media.name}"
            shutil.copy2(media, target)
            copied.append(target)
            asset_index[f"P{rank}-A{asset_number:02d}"] = target
        if not copied:
            raise FileNotFoundError(f"Product {product_id} has no supported media in assets/")
        by_rank[rank] = copied
    return by_rank, asset_index


def _copy_shared_assets(source: Path, stage_assets: Path) -> tuple[list[Path], dict[str, Path]]:
    shared: list[Path] = []
    index: dict[str, Path] = {}
    shared_root = source / "assets"
    if not shared_root.is_dir():
        return shared, index
    media_files = sorted(
        p for p in shared_root.iterdir()
        if p.is_file() and p.suffix.lower() in MEDIA_TYPES
        and MEDIA_TYPES[p.suffix.lower()] in {"image", "video"}
    )
    for asset_number, media in enumerate(media_files, 1):
        target = stage_assets / f"X__{media.name}"
        shutil.copy2(media, target)
        shared.append(target)
        index[f"X-A{asset_number:02d}"] = target
    return shared, index

def _write_staged_multi(
    source: Path, stage: Path, narration_audio: Path | None = None
) -> Path:
    config_name, config = _multi_config(source)
    product_ids = _product_ids(config)
    script_path = source / "script.md"
    if not script_path.is_file():
        raise FileNotFoundError(f"Multi-product project is missing {script_path}")

    stage.mkdir(parents=True, exist_ok=True)
    (stage / ".openmontage-multi-stage").write_text("generated\n", encoding="utf-8")
    assets_dir = stage / "assets"
    assets_dir.mkdir()
    product_assets, asset_index = _copy_product_assets(source, assets_dir, product_ids)
    shared_assets, shared_index = _copy_shared_assets(source, assets_dir)
    asset_index.update(shared_index)

    title, sections = _parse_script(script_path)
    product_map = _script_product_map(script_path, sections)
    asset_map = _script_asset_map(script_path, sections, asset_index)
    scene_lines = []
    for index, section in enumerate(sections, 1):
        rank = product_map.get(section["id"])
        if rank is None:
            label_match = re.fullmatch(r"(?:item|product)_(\d+)", section["label"], re.I)
            rank = int(label_match.group(1)) if label_match else None

        if rank is not None and rank not in product_assets:
            raise ValueError(
                f"Script section {section['id']} references product {rank}, "
                f"but {config_name} contains only {len(product_ids)} products"
            )
        if rank is not None:
            candidates = product_assets[rank]
        else:
            # Intro, comparison, recap and CTA sections can show representative
            # media from each product, plus optional shared project footage.
            candidates = [files[0] for files in product_assets.values()]
            candidates.extend(shared_assets)

        requested_assets = asset_map.get(section["id"], [])
        if requested_assets:
            if rank is not None:
                wrong_product = [
                    path.name for path in requested_assets
                    if path.name.startswith("P") and not path.name.startswith(f"P{rank:02d}__")
                ]
                if wrong_product:
                    raise ValueError(
                        f"Script section {section['id']} is tagged product: {rank} "
                        f"but references media from another product: {wrong_product}"
                    )
            chosen = requested_assets
        else:
            videos = [p for p in candidates if MEDIA_TYPES.get(p.suffix.lower()) == "video"]
            images = [p for p in candidates if MEDIA_TYPES.get(p.suffix.lower()) == "image"]
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
            f"prompt: {section['label']} section; use only the referenced supplied product media."
        )

    facts = {
        "asin": source.name,
        "product_name": str(config.get("title") or title),
        "category": str(config.get("category") or ""),
        "audience": str(config.get("audience") or ""),
        "affiliate_url": "",
        "manufacturer_claims": [],
        "angle": "top-n list" if config_name == "list.yaml" else "product comparison",
    }
    (stage / "facts.yaml").write_text(
        yaml.safe_dump(facts, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    shutil.copy2(script_path, stage / "script.md")
    (stage / "scenes.md").write_text("\n".join(scene_lines) + "\n", encoding="utf-8")
    shutil.copy2(source / config_name, stage / config_name)

    selected_audio = narration_audio or (source / "out" / "voice.wav")
    if selected_audio.is_file():
        (stage / "out").mkdir(exist_ok=True)
        shutil.copy2(selected_audio, stage / "out" / "voice.wav")
    return stage


def _stage_single(source: Path, stage: Path, narration_audio: Path | None) -> Path:
    stage.mkdir(parents=True, exist_ok=True)
    for name in ("facts.yaml", "script.md", "scenes.md"):
        shutil.copy2(source / name, stage / name)
    assets = source / "assets"
    if not assets.is_dir():
        raise FileNotFoundError(f"Single-product project is missing {assets}/")
    (stage / "assets").symlink_to(assets.resolve(), target_is_directory=True)
    selected_audio = narration_audio or (source / "out" / "voice.wav")
    if selected_audio.is_file():
        (stage / "out").mkdir(exist_ok=True)
        shutil.copy2(selected_audio, stage / "out" / "voice.wav")
    return stage


def import_selected_project(
    amazon_project: Path,
    project_id: str,
    *,
    projects_dir: Path | None = None,
    media_mode: str = "copy",
    narration_audio: Path | None = None,
) -> Path:
    """Import Single, Compare or List without writing into the Ad Agent checkout."""
    source = amazon_project.expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Amazon Video Agent project not found: {source}")
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError("OpenMontage project ID must contain 3-60 safe letters, digits, hyphens or underscores")

    is_multi = (source / "list.yaml").is_file() or (source / "compare.yaml").is_file()
    if not is_multi and narration_audio is None:
        project = import_project(
            source, project_id, projects_dir=projects_dir, media_mode=media_mode
        )
        return project

    project_root = projects_dir or PROJECTS_DIR
    project_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openmontage-amazon-stage-") as temp:
        staged_root = Path(temp) / "source"
        if is_multi:
            staged = _write_staged_multi(source, staged_root, narration_audio)
        else:
            staged = _stage_single(source, staged_root, narration_audio)
        project = import_project(
            staged, project_id, projects_dir=project_root, media_mode="copy"
        )

    # import_project points amazon_source at the temporary staging directory;
    # restore the permanent, read-only Ad Agent project reference.
    source_link = project / "amazon_source"
    if source_link.is_symlink():
        source_link.unlink()
    source_link.symlink_to(source, target_is_directory=True)
    return project
