import json

from pipeline.usage_dictionary import ROOT


def test_reviewed_legacy_dropdown_components():
    counts = {"招军榜": 0, "三路夹攻": 0}
    for level in range(1, 7):
        path = ROOT / f"content/chinese/sanguoyanyi/hsk{level}.annotations.json"
        doc = json.loads(path.read_text())
        for chapter in doc["chapters"]:
            for segment in chapter["segments"]:
                parts = segment.get("subsegments", [])
                for part in parts:
                    assert segment["text"][part["start"]:part["end"]] == part["text"]
                if segment["text"] == "招军榜" and parts:
                    assert [p["text"] for p in parts] == ["招军", "榜"]
                    counts["招军榜"] += 1
                if segment["text"] == "三路夹攻" and parts:
                    assert [p["text"] for p in parts] == ["三路", "夹攻"]
                    assert "both sides" not in parts[-1]["meaning_en"]
                    counts["三路夹攻"] += 1
                if segment["text"] == "自有公论":
                    assert all(p["meaning_en"] != "official judgment" for p in parts)
                if segment["text"] == "有没有":
                    assert all("is there not" not in p["meaning_en"] for p in parts)
    assert counts == {"招军榜": 4, "三路夹攻": 1}
