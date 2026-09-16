from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import enrich_metadata  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = type("Headers", (), {"get_content_charset": lambda self: "utf-8"})()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._payload


class ZgncjjAbstractRecoveryTests(unittest.TestCase):
    @patch.object(enrich_metadata.urllib.request, "urlopen")
    def test_official_api_recovers_abstract_from_content_id_without_rewriting_identity(self, urlopen_mock) -> None:
        getter = getattr(enrich_metadata, "ajcass_content_metadata", None)
        self.assertTrue(callable(getter), "missing 中国农村经济 official metadata adapter")

        urlopen_mock.return_value = _FakeResponse(
            {
                "data": {
                    "issueContentInfoResult": {
                        "title": "深化农村集体产权制度改革中的三次分配体系构建研究",
                        "authors": "许中缘 肖佳欣 夏沁",
                        "abstract": "党的二十届四中全会强调完善收入分配制度，扎实推进全体人民共同富裕。本文围绕农村集体产权制度改革中的三次分配体系构建展开分析，并提供足够长度的官方摘要文本用于回归测试。",
                        "yearVolumeIssue": "2026年第9期",
                    }
                }
            }
        )
        record = {
            "journal": "中国农村经济",
            "title": "深化农村集体产权制度改革中的三次分配体系构建研究",
            "url": "https://zgncjj.ajcass.com/#/detail?contentId=123527",
            "first_seen": "2026-09-11T04:12:35+00:00",
            "official_date": "2026-09-11",
        }

        before_identity = (record["title"], record["url"], record["first_seen"], record["official_date"])
        metadata = getter(record, timeout=3)

        self.assertIn("共同富裕", metadata["abstract"])
        self.assertEqual(metadata["abstract_source"], "ajcass_official_api")
        self.assertEqual(before_identity, (record["title"], record["url"], record["first_seen"], record["official_date"]))

        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.ajcass.com/api/SiteWebApi/GetContentInfo")
        self.assertEqual(request.get_method(), "POST")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["contentId"], 123527)
        self.assertEqual(body["JournalID"], 201606270007)

    @patch.object(enrich_metadata, "ajcass_content_metadata", create=True)
    def test_abstract_retry_uses_official_api_for_zgncjj_record(self, api_mock) -> None:
        api_mock.return_value = {
            "abstract": "这是来自中国农村经济官方接口的完整摘要，长度足以通过系统的摘要完整性判断，并用于验证缺失摘要重试会消费官方元数据而不修改首见时间或日期证据。",
            "abstract_source": "ajcass_official_api",
        }
        record = {
            "journal": "中国农村经济",
            "title": "灵活就业对农户生计韧性的影响",
            "url": "https://zgncjj.ajcass.com/#/detail?contentId=123528",
            "first_seen": "2026-09-11T04:12:35+00:00",
            "official_date": "2026-09-11",
            "date_source": "file_upload_date",
            "date_confidence": "F",
            "authors": ["朱波", "周思彤", "商遥", "方能胜"],
        }

        changed, status = enrich_metadata.enrich_abstract_record(record, timeout=3)

        self.assertTrue(changed)
        self.assertEqual(status, "abstract-updated:ajcass-official-api")
        self.assertEqual(record["abstract_source"], "ajcass_official_api")
        self.assertEqual(record["first_seen"], "2026-09-11T04:12:35+00:00")
        self.assertEqual(record["official_date"], "2026-09-11")
        self.assertEqual(record["date_source"], "file_upload_date")
        self.assertEqual(record["date_confidence"], "F")

    def test_proven_zgncjj_direct_recovery_outranks_newer_generic_missing_abstract(self) -> None:
        proven_direct = {
            "journal": "中国农村经济",
            "url": "https://zgncjj.ajcass.com/#/detail?contentId=123527",
            "first_seen": "2026-09-11T05:33:53+00:00",
            "abstract": None,
        }
        newer_generic = {
            "journal": "Economic Modelling",
            "url": "https://www.sciencedirect.com/science/article/pii/S0264999326002385",
            "first_seen": "2026-09-16T09:21:48+00:00",
            "abstract": None,
        }

        self.assertLess(
            enrich_metadata.abstract_enrich_priority(proven_direct),
            enrich_metadata.abstract_enrich_priority(newer_generic),
        )


if __name__ == "__main__":
    unittest.main()
