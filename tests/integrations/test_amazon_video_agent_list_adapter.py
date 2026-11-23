from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from integrations.amazon_video_agent.importer import _parse_scenes, _parse_script
from integrations.amazon_video_agent.list_adapter import (
    _script_asset_map,
    _script_product_map,
    _stage_single,
    _write_staged_multi,
)
from scripts.render_piper_narrated_project import EDGE_VOICES, build_edit_decisions


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
                "## item_3\n[2] This is the third pick for travel. | product: 3 | assets: photo-3.jpg | p: 1\n"
                "## item_2\n[3] This is the second pick for compact use. | product: 2 | p: 1\n"
                "## item_1\n[4] This is my top overall pick. | product: 1 | p: 1\n"
            )
        else:
            script = (
                "# TITLE: Product comparison\n"
                "## intro\n[1] Here are the two products. | product: all | p: 1\n"
                "## product_1\n[2] First product details. | product: 1 | assets: photo-1.jpg | p: 1\n"
                "## product_2\n[3] Second product details. | product: 2 | p: 1\n"
                "## verdict\n[4] Here is who each product suits. | product: all | p: 1\n"
            )
        (project / "script.md").write_text(script, encoding="utf-8")
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
            self.assertEqual(scenes[1]["refs"][0], "P01__photo-1.jpg")
            self.assertTrue(scenes[2]["refs"][0].startswith("P02__"))
            self.assertEqual(len(list((staged / "assets").iterdir())), 4)

    def test_explicit_script_assets_are_parsed(self):
        with tempfile.TemporaryDirectory() as temp:
            source = self._make_multi_project(Path(temp), "list")
            _, sections = _parse_script(source / "script.md")
            mapping = _script_asset_map(source / "script.md", sections)
            self.assertEqual(mapping["2"], ["photo-3.jpg"])

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

    def test_edge_voice_options_match_ad_agent_four_voice_set(self):
        self.assertEqual(EDGE_VOICES, {
            "edge_female": ("en-US-AvaMultilingualNeural", "female"),
            "edge_female_2": ("en-US-EmmaMultilingualNeural", "female"),
            "edge_male": ("en-US-AndrewMultilingualNeural", "male"),
            "edge_male_2": ("en-US-BrianMultilingualNeural", "male"),
        })

    def test_manual_workflow_exposes_all_project_modes_and_edge_voices(self):
        workflow = (
            Path(__file__).resolve().parents[2]
            / ".github" / "workflows" / "amazon-video-agent-narrated.yml"
        ).read_text(encoding="utf-8")
        for voice in EDGE_VOICES:
            self.assertIn(voice, workflow)
        self.assertIn('"$SOURCE/compare.yaml"', workflow)
        self.assertIn('if [ -f "$SOURCE/list.yaml" ]', workflow)
        self.assertIn('VOICE: ${{ inputs.voice }}', workflow)
        self.assertEqual(workflow.count("default: false"), 2)
        self.assertIn("default: edge_female", workflow)

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
