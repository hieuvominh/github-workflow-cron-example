import sys
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from responsive_images import make_responsive_variants


class ResponsiveImageTests(unittest.TestCase):
    def make_image(self, size, image_format="JPEG"):
        output = BytesIO()
        Image.new("RGB", size, (24, 120, 210)).save(output, format=image_format)
        return output.getvalue()

    def test_generates_widths_without_upscaling_and_keeps_aspect_ratio(self):
        variants = make_responsive_variants(self.make_image((1400, 900)))
        self.assertEqual([480, 768, 1280, 1400], [v[0] for v in variants])
        self.assertTrue(all(extension == "webp" for _, _, extension in variants))
        for expected_width, data, _ in variants:
            with Image.open(BytesIO(data)) as result:
                self.assertEqual(expected_width, result.width)
                self.assertEqual(round(900 * expected_width / 1400), result.height)

    def test_small_image_is_not_upscaled(self):
        variants = make_responsive_variants(self.make_image((240, 160)))
        self.assertEqual([240], [v[0] for v in variants])

    def test_animated_gif_is_preserved_as_original(self):
        output = BytesIO()
        frames = [Image.new("RGB", (120, 80), color) for color in ("red", "blue")]
        frames[0].save(
            output,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
        original = output.getvalue()
        variants = make_responsive_variants(original)
        self.assertEqual([(120, original, "gif")], variants)


if __name__ == "__main__":
    unittest.main()
