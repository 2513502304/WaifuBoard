import unittest


class PackageLayoutTests(unittest.TestCase):
    def test_site_clients_keep_legacy_and_root_imports_compatible(self):
        from waifuboard import DanbooruClient, SafebooruClient, YandereClient
        from waifuboard.danbooru import DanbooruClient as LegacyDanbooruClient
        from waifuboard.moebooru import YandereClient as LegacyYandereClient
        from waifuboard.safebooru import SafebooruClient as LegacySafebooruClient
        from waifuboard.sites.danbooru import DanbooruClient as SiteDanbooruClient
        from waifuboard.sites.moebooru import YandereClient as SiteYandereClient
        from waifuboard.sites.safebooru import (
            SafebooruClient as SiteSafebooruClient,
        )

        self.assertIs(DanbooruClient, SiteDanbooruClient)
        self.assertIs(DanbooruClient, LegacyDanbooruClient)
        self.assertIs(SafebooruClient, SiteSafebooruClient)
        self.assertIs(SafebooruClient, LegacySafebooruClient)
        self.assertIs(YandereClient, SiteYandereClient)
        self.assertIs(YandereClient, LegacyYandereClient)

    def test_path_helper_keeps_utils_compatibility_export(self):
        from waifuboard.paths import normalize_filepath
        from waifuboard.utils import normalize_filepath as legacy_normalize_filepath

        self.assertIs(normalize_filepath, legacy_normalize_filepath)
        self.assertEqual(normalize_filepath('a<b>:c?.jpg'), "abc.jpg")
