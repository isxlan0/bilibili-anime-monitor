import unittest

from bilibili import extract_bangumi_identifier


class BilibiliIdentifierTests(unittest.TestCase):
    def test_extract_from_ss_url(self) -> None:
        self.assertEqual(extract_bangumi_identifier("https://www.bilibili.com/bangumi/play/ss127870"), ("ss", "127870"))

    def test_extract_from_ep_url(self) -> None:
        self.assertEqual(extract_bangumi_identifier("https://www.bilibili.com/bangumi/play/ep2612898"), ("ep", "2612898"))

    def test_extract_from_plain_id(self) -> None:
        self.assertEqual(extract_bangumi_identifier("ss127870"), ("ss", "127870"))

    def test_reject_invalid_input(self) -> None:
        with self.assertRaises(ValueError):
            extract_bangumi_identifier("https://www.bilibili.com/video/BV1xx")
