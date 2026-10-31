"""Repetition signals preserve context for human review and never gate a reply."""

import json

import pytest

from scripts import evaluate_expression_repetition as review


def dialogue(*pairs, current="今天聊点什么？"):
    return [*[m for user, reply in pairs for m in (
        {"role": "user", "content": user}, {"role": "assistant", "content": reply})],
        {"role": "user", "content": current}]


def test_repeated_openings_and_literal_memes_are_evidence_for_review():
    messages = dialogue(("今天买了个蛋糕", "好家伙这个，CPU烧了，哪种口味？"),
                        ("猫钻进鞋盒了", "好家伙这个，CPU烧了，盒子还挺小。"))
    result = review.assess(messages, "好家伙这个，CPU烧了，真有意思。",
                           meme_phrases=["CPU烧了"])
    assert all(signal["shared_opening_characters"] == 5 for signal in result["signals"])
    assert all(signal["literal_meme_matches"] == ["CPU烧了"] for signal in result["signals"])
    assert result["ratings"] == {axis: None for axis in review.AXES}


@pytest.mark.parametrize("current", ["再讲一遍刚才那个视频吧", "今天有什么新东西？"])
def test_repeated_story_keeps_request_context_instead_of_assuming_it_is_bad(current):
    story = "今天读到恒星颜色与温度有关，蓝色的恒星通常更热。"
    result = review.assess(dialogue(("看了什么？", story), current=current), story)
    assert result["signals"][0]["bigram_jaccard"] == 1
    assert "恒星颜色与温度有关" in result["signals"][0]["longest_shared_passage"]
    assert result["current_text"] == current
    assert result["ratings"]["story_repetition"] is None
    assert "主人要求复述" in "".join(result["review_rules"])


def test_natural_callback_and_stable_voice_are_not_automatic_rejections():
    messages = dialogue(("今天和编译器吵架了", "编译器今天又赢了。"),
                        current="哈哈，用刚才编译器赢了那个梗再接一句")
    result = review.assess(messages, "编译器今天又赢了，不过你已经追回一分。",
                           meme_phrases=["编译器今天又赢了"])
    assert result["signals"][0]["literal_meme_matches"]
    assert "主人邀请接梗" in "".join(result["review_rules"])
    assert set(result["ratings"].values()) == {None}
    assert not {"blocked", "rewrite", "accepted", "verdict"} & set(result)


def test_paraphrased_old_story_remains_visible_to_reviewer_even_with_low_overlap():
    result = review.assess(dialogue(("昨天的天文视频呢？", "那颗蓝星很热，颜色反映温度。")),
                           "有趣的是炽热的恒星呈现偏蓝的光。")
    assert result["recent_turns"][0]["assistant"] == "那颗蓝星很热，颜色反映温度。"
    assert result["ratings"]["story_repetition"] is None
    assert "低重合不能证明故事新" in "".join(result["review_rules"])


def test_unscorable_or_empty_outputs_never_count_as_good_diversity():
    assert not review.assess([], "")["scorable"]
    assert not review.assess([], "不完整的生成", scorable=False)["scorable"]


def test_review_bounds_history_and_cli_keeps_output_inside_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "ROOT", tmp_path)
    messages = dialogue(*[(str(i), f"过去的回复{i}") for i in range(30)])
    result = review.assess(messages, "现在的回复")
    assert len(result["recent_turns"]) == review.MAX_RECENT
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"messages": messages, "reply": "现在的回复"}), "utf-8")
    target = tmp_path / ".local/review.json"
    review.main(["--input", str(path), "--output", str(target)])
    assert json.loads(target.read_text("utf-8")) == result
    with pytest.raises(FileExistsError):
        review.main(["--input", str(path), "--output", str(target)])
    with pytest.raises(SystemExit):
        review.main(["--input", str(path), "--output", str(tmp_path / "outside.json")])
