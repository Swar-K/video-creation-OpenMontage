from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from integrations.amazon_video_agent.importer import MEDIA_TYPES, _parse_scenes, _parse_script
from integrations.amazon_video_agent.list_adapter import (
    _script_asset_map,
    _multi_config,
    _product_ids,
    _script_product_map,
    _stage_single,
    _write_staged_multi,
)
from scripts.render_piper_narrated_project import build_edit_decisions
from integrations.amazon_video_agent.tts_adapter import _split_text


class AmazonProjectAdapterTests(unittest.TestCase):
    def _make_multi_project(self, root: Path, kind: str = "list") -> Path:
        projects = root / "projects"
        name = "TOP3-EXAMPLE" if kind == "list" else "COMPARE-EXAMPLE"
        project = projects / name
        project.mkdir(parents=True)
        product_ids = ["B000000001", "B000000002", "B000000003"] if kind == "list" else ["B000000001", "B000000002"]
        config = {
            "title": "Top 3 sample products" if kind == "list" else "Product comparison",
            "products": ([{"id": x, "label": f"Pick {i}"} for i, x in enumerate(product_ids, 1)]
                         if kind == "list" else product_ids),
            "category": "sample category",
            "audience": "sample audience",
        }
        config_name = "list.yaml" if kind == "list" else "compare.yaml"
        (project / config_name).write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        if kind == "list":
            script = (
                "# TITLE: Top 3 sample products\n"
                "## hook\n[1] Here are three options worth considering. | product: all | p: 1\n"
                "## item_3\n[2] This is the third pick for travel. | product: 3 | assets: P3-A02 | p: 1\n"
                "## item_2\n[3] This is the second pick for compact use. | product: 2 | p: 1\n"
                "## item_1\n[4] This is my top overall pick. | product: 1 | p: 1\n"
            )
        else:
            script = (
                "# TITLE: Product comparison\n"
                "## intro\n[1] Here are the two products. | product: all | assets: X-01 | p: 1\n"
                "## product_1\n[2] First product details. | product: 1 | assets: P1-A02 | p: 1\n"
                "## product_2\n[3] Second product details. | product: 2 | p: 1\n"
                "## head_to_head\n[4] Here is a direct comparison. | assets: P1-A02,P2-A02 | p: 1\n"
            )
        (project / "script.md").write_text(script, encoding="utf-8")
        if kind == "compare":
            (project / "assets").mkdir()
            (project / "assets" / "shared.jpg").write_bytes(b"shared-image")
        for rank, product_id in enumerate(product_ids, 1):
            product = projects / product_id
            (product / "assets").mkdir(parents=True)
            (product / "assets" / f"photo-{rank}.jpg").write_bytes(b"image-fixture")
            (product / "assets" / f"clip-{rank}.mp4").write_bytes(b"video-fixture")
            (product / "facts.yaml").write_text(f"product_name: Product {rank}\n", encoding="utf-8")
        return project

    def test_list_adapter_stages_rank_specific_assets_and_scenes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._make_multi_project(root, "list")
            staged = _write_staged_multi(source, root / "stage" / "source")
            scenes = _parse_scenes(staged / "scenes.md")
            self.assertEqual(len(scenes), 4)
            self.assertEqual(scenes[1]["refs"][0], "P03__photo-3.jpg")
            self.assertTrue(scenes[2]["refs"][0].startswith("P02__"))
            self.assertTrue(scenes[3]["refs"][0].startswith("P01__"))
            self.assertEqual(len(list((staged / "assets").iterdir())), 6)
            self.assertTrue((source / "list.yaml").is_file())

    def test_compare_adapter_accepts_two_products_and_maps_product_sections(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._make_multi_project(root, "compare")
            staged = _write_staged_multi(source, root / "stage" / "source")
            scenes = _parse_scenes(staged / "scenes.md")
            self.assertEqual(len(scenes), 4)
            self.assertEqual(scenes[0]["refs"], ["X__shared.jpg"])
            self.assertEqual(scenes[1]["refs"][0], "P01__photo-1.jpg")
            self.assertTrue(scenes[2]["refs"][0].startswith("P02__"))
            self.assertEqual(scenes[3]["refs"], ["P01__photo-1.jpg", "P02__photo-2.jpg"])
            self.assertEqual(len(list((staged / "assets").iterdir())), 5)

    def test_missing_optional_shared_asset_id_falls_back_without_source_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._make_multi_project(Path(temp), "list")
            script = source / "script.md"
            script.write_text(
                "# TITLE: list\n## criteria\n[9] General advice. | assets: X-01 | p: 1\n",
                encoding="utf-8",
            )
            _, sections = _parse_script(script)
            mapping = _script_asset_map(script, sections, {})
            self.assertNotIn("9", mapping)

    def test_product_metadata_is_parsed(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._make_multi_project(Path(temp), "list")
            _, sections = _parse_script(source / "script.md")
            mapping = _script_product_map(source / "script.md", sections)
            self.assertIsNone(mapping["1"])
            self.assertEqual(mapping["2"], 3)
            self.assertEqual(mapping["3"], 2)
            self.assertEqual(mapping["4"], 1)

    def test_selected_voice_audio_is_staged_without_writing_to_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._make_multi_project(root, "list")
            voice = root / "selected-voice.wav"
            voice.write_bytes(b"test voice data")
            staged = _write_staged_multi(source, root / "stage" / "source", voice)
            self.assertEqual((staged / "out" / "voice.wav").read_bytes(), voice.read_bytes())
            self.assertFalse((source / "out").exists())

    def test_single_product_voice_is_staged_without_source_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "projects" / "B000000001"
            (source / "assets").mkdir(parents=True)
            (source / "facts.yaml").write_text("product_name: Sample\n", encoding="utf-8")
            (source / "script.md").write_text("# TITLE: Sample\n## intro\n[1] Hello.\n", encoding="utf-8")
            (source / "scenes.md").write_text("[s01] type: product | ref: photo.jpg | prompt: product\n", encoding="utf-8")
            (source / "assets" / "photo.jpg").write_bytes(b"image-fixture")
            voice = root / "voice.wav"
            voice.write_bytes(b"voice-fixture")
            staged = _stage_single(source, root / "staged", voice)
            self.assertEqual((staged / "out" / "voice.wav").read_bytes(), voice.read_bytes())
            self.assertFalse((source / "out").exists())

    def test_workflow_exposes_every_voice_configured_by_ad_agent(self):
        workflow = (
            Path(__file__).resolve().parents[2]
            / ".github" / "workflows" / "amazon-video-agent-narrated.yml"
        ).read_text(encoding="utf-8")
        configured = [
            "eleven_female", "eleven_female_2", "eleven_male", "eleven_male_2",
            "edge_female", "edge_female_2", "edge_male", "edge_male_2",
            "openai_female", "openai_male", "inworld_female", "inworld_male",
            "openrouter_voxtral",
        ]
        for voice in configured:
            self.assertIn(voice, workflow)
        config_path = Path("amazon-video-agent/config.yaml")
        if config_path.is_file():
            ad_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            configured_options = ad_config["voice"]["options"]
            for voice_key, voice_config in configured_options.items():
                self.assertIn(voice_key, workflow)
                self.assertIn(voice_config["provider"], {"elevenlabs", "edge", "openai", "inworld", "openrouter"})
        self.assertIn("confirm_paid_tts", workflow)
        self.assertIn("--voice-config ../amazon-video-agent/config.yaml", workflow)
        self.assertEqual(workflow.count("default: false"), 3)

    def test_tts_text_chunks_respect_provider_limit_and_preserve_text(self):
        source = ("This is sentence one. " * 20) + ("Unicode café text. " * 10)
        chunks = _split_text(source, 100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 100 for chunk in chunks))
        self.assertEqual(" ".join(chunks), " ".join(source.split()))

    def _checked_out_ad_agent_asset_index(self, source: Path) -> dict[str, Path]:
        _, config = _multi_config(source)
        index: dict[str, Path] = {}
        for rank, product_id in enumerate(_product_ids(config), 1):
            assets = source.parent / product_id / "assets"
            media = sorted(
                p for p in assets.iterdir()
                if p.is_file() and p.suffix.lower() in MEDIA_TYPES
                and MEDIA_TYPES[p.suffix.lower()] in {"image", "video"}
            )
            for number, path in enumerate(media, 1):
                index[f"P{rank}-A{number:02d}"] = path
        shared = source / "assets"
        if shared.is_dir():
            media = sorted(
                p for p in shared.iterdir()
                if p.is_file() and p.suffix.lower() in MEDIA_TYPES
                and MEDIA_TYPES[p.suffix.lower()] in {"image", "video"}
            )
            for number, path in enumerate(media, 1):
                index[f"X-A{number:02d}"] = path
                index[f"X-{number:02d}"] = path
        return index

    def test_real_ad_agent_compare_script_asset_ids_match_checked_out_media(self):
        root = Path("amazon-video-agent/projects")
        source = root / "B0DK5XXVTX-B08S3M2HKL"
        if not source.is_dir():
            self.skipTest("Ad Agent checkout is not available in this test environment")
        _, sections = _parse_script(source / "script.md")
        mapping = _script_asset_map(
            source / "script.md", sections, self._checked_out_ad_agent_asset_index(source)
        )
        self.assertTrue(mapping)
        self.assertTrue(all(path.is_file() for paths in mapping.values() for path in paths))
        self.assertEqual(len(mapping["14"]), 2)
        self.assertTrue(any(path.parent.parent.name == "B0DK5XXVTX" for path in mapping["14"]))
        self.assertTrue(any(path.parent.parent.name == "B08S3M2HKL" for path in mapping["14"]))

    def test_real_ad_agent_list_script_asset_ids_match_checked_out_media(self):
        root = Path("amazon-video-agent/projects")
        source = root / "B0DK5XXVTX-B00G7009LM-B00002NCJI"
        if not source.is_dir():
            self.skipTest("Ad Agent checkout is not available in this test environment")
        _, sections = _parse_script(source / "script.md")
        mapping = _script_asset_map(
            source / "script.md", sections, self._checked_out_ad_agent_asset_index(source)
        )
        self.assertTrue(mapping)
        self.assertTrue(all(path.is_file() for paths in mapping.values() for path in paths))
        ranks = {path.parent.parent.name for paths in mapping.values() for path in paths}
        self.assertTrue({"B0DK5XXVTX", "B00G7009LM", "B00002NCJI"}.issubset(ranks))

    def test_list_edit_uses_scene_product_image_not_round_robin_image(self):
        script = {
            "total_duration_seconds": 20.0,
            "sections": [{"id": "s02", "label": "item_2",
                          "start_seconds": 0.0, "end_seconds": 20.0}],
        }
        manifest = {"assets": [
            {"id": "p1", "type": "image", "path": "assets/images/P01__one.jpg"},
            {"id": "p2", "type": "image", "path": "assets/images/P02__two.jpg"},
        ]}
        plan = {"scenes": [{"script_section_id": "s02",
                            "required_assets": [{"type": "image", "description": "P02__two.jpg"}]}]}
        decisions = build_edit_decisions(script, manifest, plan, manifest["assets"], "TOP3")
        self.assertEqual(decisions["cuts"][0]["source"], "p2")
        self.assertEqual(decisions["overlays"][0]["text"], "Rank #2")


if __name__ == "__main__":
    unittest.main()
