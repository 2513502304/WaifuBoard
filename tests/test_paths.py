import unittest

from waifuboard.paths import normalize_filepath


class PathNormalizationTests(unittest.TestCase):
    def test_normalization_produces_portable_non_empty_names(self):
        self.assertEqual(normalize_filepath("?*"), "unnamed")
        self.assertEqual(normalize_filepath("CON.txt"), "_CON.txt")
        self.assertEqual(normalize_filepath("COM\u00b9.log"), "_COM\u00b9.log")
        self.assertEqual(normalize_filepath("image.jpg. "), "image.jpg")


if __name__ == "__main__":
    unittest.main()
