import unittest

from waifuboard.paths import normalize_filepath


class PathNormalizationTests(unittest.TestCase):
    def test_normalization_produces_portable_non_empty_names(self):
        self.assertEqual(normalize_filepath("?*"), "unnamed")
        self.assertEqual(normalize_filepath("CON.txt"), "_CON.txt")
        self.assertEqual(normalize_filepath("COM\u00b9.log"), "_COM\u00b9.log")
        self.assertEqual(normalize_filepath("image.jpg. "), "image.jpg")

    def test_normalization_removes_ascii_control_characters(self):
        # NUL 移除后仍需继续执行 Windows 保留设备名规则
        self.assertEqual(normalize_filepath("NUL\x00.txt"), "_NUL.txt")
        self.assertEqual(normalize_filepath("report\x1f.txt"), "report.txt")


if __name__ == "__main__":
    unittest.main()
