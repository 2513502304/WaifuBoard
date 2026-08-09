import importlib.util
import unittest


class PackageLayoutTests(unittest.TestCase):
    def test_site_clients_are_exported_from_root_and_sites_package(self):
        from waifuboard import DanbooruClient, SafebooruClient, YandereClient
        from waifuboard.sites import (
            DanbooruClient as SiteDanbooruClient,
            SafebooruClient as SiteSafebooruClient,
            YandereClient as SiteYandereClient,
        )

        self.assertIs(DanbooruClient, SiteDanbooruClient)
        self.assertIs(SafebooruClient, SiteSafebooruClient)
        self.assertIs(YandereClient, SiteYandereClient)

    def test_top_level_site_compatibility_modules_are_removed(self):
        for module_name in (
            "waifuboard.danbooru",
            "waifuboard.moebooru",
            "waifuboard.safebooru",
        ):
            with self.subTest(module_name=module_name):
                self.assertIsNone(importlib.util.find_spec(module_name))

    def test_path_helper_keeps_utils_compatibility_export(self):
        from waifuboard.paths import normalize_filepath
        from waifuboard.utils import normalize_filepath as legacy_normalize_filepath

        self.assertIs(normalize_filepath, legacy_normalize_filepath)
        self.assertEqual(normalize_filepath('a<b>:c?.jpg'), "abc.jpg")
