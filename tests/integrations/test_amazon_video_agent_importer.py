import tempfile
import unittest
from pathlib import Path

from integrations.amazon_video_agent.importer import _parse_script, _parse_scenes, _validate_project_id


class ImporterParsingTests(unittest.TestCase):
    def test_parse_amazon_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.md"
            path.write_text(
                "# TITLE: Example Product\n\n## hook\n"
                "[1] First narration sentence. | assets: A01 | p: 1\n"
                "## verdict\n[O1] Final call to action. | p: 1\n",
                encoding="utf-8",
            )
            title, sections = _parse_script(path)
        self.assertEqual(title, "Example Product")
        self.assertEqual([x["id"] for x in sections], ["1", "O1"])
        self.assertEqual([x["label"] for x in sections], ["hook", "verdict"])
        self.assertEqual(sections[0]["text"], "First narration sentence.")

    def test_parse_scene_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.md"
            path.write_text(
                "[s01] type: product | ref: product_image_11.jpg | prompt: Slow camera push.\n",
                encoding="utf-8",
            )
            scenes = _parse_scenes(path)
        self.assertEqual(scenes[0]["id"], "s01")
        self.assertEqual(scenes[0]["ref"], "product_image_11.jpg")
        self.assertEqual(scenes[0]["prompt"], "Slow camera push.")


    def test_parse_multiple_existing_scene_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.md"
            path.write_text(
                "[s02] type: product | ref: clip.mp4; product_image.jpg | prompt: Existing footage.\n",
                encoding="utf-8",
            )
            scenes = _parse_scenes(path)
        self.assertEqual(scenes[0]["ref"], "clip.mp4")
        self.assertEqual(scenes[0]["refs"], ["clip.mp4", "product_image.jpg"])

    def test_parse_scene_without_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.md"
            path.write_text(
                "[s03] type: mood | ref: none | prompt: No source media.\n",
                encoding="utf-8",
            )
            scenes = _parse_scenes(path)
        self.assertEqual(scenes[0]["refs"], [])

    def test_rejects_path_traversal_project_id(self):
        with self.assertRaises(ValueError):
            _validate_project_id("../outside")


if __name__ == "__main__":
    unittest.main()
