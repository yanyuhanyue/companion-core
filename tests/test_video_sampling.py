import math
import random
from dataclasses import asdict
from itertools import pairwise

import pytest
from pydantic import ValidationError

from companion.video_sampling import (
    DanmakuPoint,
    SubtitleSpan,
    VideoSamplingConfig,
    plan_segments,
)


def subtitle(at, text="A source-backed statement", source="subtitle-1", **kwargs):
    return SubtitleSpan(at - 1, at + 1, text, source, **kwargs)


def burst(at, count=4, prefix="audience"):
    return [DanmakuPoint(at + index / 20, f"观众意见 {prefix} {index}", f"{prefix}-{index}")
            for index in range(count)]


def evidence(plan):
    return [item for frame in plan.frames for item in frame.evidence]


def test_missing_cues_yield_only_bounded_uniform_previews():
    plan = plan_segments(100, [], [], VideoSamplingConfig())
    assert [frame.at_seconds for frame in plan.frames] == [12.5, 37.5, 62.5, 87.5]
    assert not plan.segments
    assert plan.partial and not plan.visual_observed and plan.segments_require_preview
    assert set(plan.warnings) == {"subtitle_text_missing", "audience_danmaku_missing"}
    assert all(frame.reasons == ("uniform_coverage",) and not frame.evidence
               for frame in plan.frames)


@pytest.mark.parametrize("duration", [0, -1, math.nan, math.inf, -math.inf, True, "30",
                                     86401, 10**1000])
def test_invalid_duration_fails_closed(duration):
    with pytest.raises(ValueError, match="duration"):
        plan_segments(duration, [], [], VideoSamplingConfig())


@pytest.mark.parametrize("changes", [
    {"initial_frames": 0}, {"initial_frames": True}, {"initial_frames": 1.5},
    {"max_segments": -1}, {"max_total_segment_seconds": -1},
    {"context_before_seconds": 0, "context_after_seconds": 0},
    {"max_segment_seconds": 5}, {"min_frame_gap_seconds": 0},
    {"danmaku_bin_seconds": 0}, {"local_window_seconds": 1},
    {"min_density_ratio": 1}, {"baseline_floor_per_second": 0},
    {"baseline_floor_per_second": 1e-300},
    {"context_after_seconds": math.inf}, {"focus_terms": [""]},
    {"focus_terms": ["!!!"]}, {"focus_terms": ["x" * 81]}, {"unknown_option": 1},
])
def test_configuration_rejects_unsafe_or_inconsistent_limits(changes):
    with pytest.raises(ValidationError):
        VideoSamplingConfig(**changes)


def test_configuration_has_serializable_typed_boundary():
    config = VideoSamplingConfig(focus_terms=("电池温度",), initial_frames=2)
    assert VideoSamplingConfig.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(TypeError, match="config"):
        plan_segments(100, [], [], {})
    with pytest.raises(TypeError, match="sequences"):
        plan_segments(100, iter([]), [], config)


def test_bad_source_rows_are_rejected_without_clamping_mismatched_timestamps():
    subtitles = [
        SubtitleSpan(-1, 2, "negative", "bad-1"),
        SubtitleSpan(5, 5, "zero span", "bad-2"),
        SubtitleSpan(4, 3, "reversed", "bad-3"),
        SubtitleSpan(9, 11, "wrong video length", "bad-4"),
        SubtitleSpan(False, 2, "boolean timestamp", "bad-5"),
        SubtitleSpan(math.nan, 2, "NaN", "bad-6"),
        SubtitleSpan(0, math.inf, "infinite", "bad-7"),
        SubtitleSpan(1, 2, "missing provenance", ""),
        SubtitleSpan(1, 2, "   ", "bad-9"),
        SubtitleSpan("1", 2, "string timestamp", "bad-10"),
        {"from": 1, "to": 2, "content": "unadapted native payload"},
        SubtitleSpan(8, 10, "有效字幕", "valid"),
    ]
    comments = [DanmakuPoint(10, "EOF is not a frame", "bad-end"),
                DanmakuPoint(-1, "negative", "bad-start"),
                DanmakuPoint(True, "boolean", "bad-bool"),
                DanmakuPoint(math.inf, "infinite", "bad-inf")]
    plan = plan_segments(10, subtitles, comments, VideoSamplingConfig())
    assert {item.source_id for item in evidence(plan)} == {"valid"}
    assert "subtitle_text_invalid" in plan.warnings
    assert "audience_danmaku_invalid" in plan.warnings
    assert plan.segments[0].end_seconds == 10
    assert "context_clipped_at_video_boundary" in plan.segments[0].reasons


def test_focus_terms_select_relevant_chinese_subtitle_without_visual_claim():
    rows = [subtitle(10, "今天讲解一些基础内容", "intro"),
            subtitle(50, "接下来列出一些常见项目", "list"),
            subtitle(90, "电池温度突然升高，先暂停充电检查散热", "temperature")]
    plan = plan_segments(120, rows, [], VideoSamplingConfig(
        initial_frames=1, focus_terms=("电池温度",)))
    assert plan.frames[0].at_seconds == 90
    assert "subtitle_focus_terms" in plan.frames[0].reasons
    assert evidence(plan)[0].kind == "subtitle_text"
    assert evidence(plan)[0].text == rows[-1].text
    assert not plan.visual_observed


def test_repeated_captions_do_not_create_repeated_focus_points():
    rows = [subtitle(10, "The battery needs cooling", "first"),
            subtitle(12, "Ｔｈｅ battery needs cooling!", "repeat"),
            subtitle(13, "The battery needs cooling now", "near-repeat"),
            subtitle(70, "The battery needs cooling", "later")]
    plan = plan_segments(100, rows, [], VideoSamplingConfig(
        initial_frames=4, min_frame_gap_seconds=1, subtitle_similarity=0.8))
    assert {item.source_id for item in evidence(plan)} == {"first", "later"}
    assert "subtitle_text_deduplicated" in plan.warnings


def test_repeated_danmaku_spam_and_isolated_comment_do_not_form_peak():
    rows = [DanmakuPoint(40 + index / 100, "  太好了!!!  ", f"spam-{index}")
            for index in range(60)]
    rows.append(DanmakuPoint(80, "只有一条独立评论", "isolated"))
    plan = plan_segments(120, [], rows, VideoSamplingConfig())
    assert not evidence(plan) and not plan.segments
    assert "audience_danmaku_deduplicated" in plan.warnings


def test_globally_high_uniform_density_is_not_a_local_peak():
    rows = [point for index in range(24) for point in burst(index * 5 + 1, 8, str(index))]
    plan = plan_segments(120, [], rows, VideoSamplingConfig())
    assert not evidence(plan) and not plan.segments


def test_local_normalization_finds_small_burst_in_quiet_section():
    # The first half has much larger raw counts but no local peak.
    rows = [point for index in range(40)
            for point in burst(index * 5 + 1, 10 if index < 20 else 4 if index == 30 else 1,
                               str(index))]
    plan = plan_segments(200, [], rows, VideoSamplingConfig(local_window_seconds=15))
    signals = [frame for frame in plan.frames if frame.evidence]
    assert len(signals) == 1
    assert 150 <= signals[0].at_seconds < 155
    assert signals[0].local_density_ratio == pytest.approx(4)
    assert {item.kind for item in signals[0].evidence} == {"audience_danmaku"}
    assert all(item.source_id.startswith("30-") for item in signals[0].evidence)
    assert all(item.partial for item in signals[0].evidence)


def test_single_density_bin_has_no_local_comparison_evidence():
    plan = plan_segments(3, [], burst(1, 5), VideoSamplingConfig())
    assert not evidence(plan) and not plan.segments


def test_different_cue_sources_near_same_time_are_retained_without_extra_frame():
    plan = plan_segments(100, [subtitle(51, "这一段讨论电池温度", "caption")],
                         burst(50, 5), VideoSamplingConfig(initial_frames=1))
    assert len(plan.frames) == 1
    assert {item.kind for item in evidence(plan)} == {"subtitle_text", "audience_danmaku"}
    assert "audience_local_density_peak" in plan.frames[0].reasons
    assert "subtitle_lexical_salience" in plan.frames[0].reasons


def test_overlapping_contexts_merge_and_count_union_only():
    config = VideoSamplingConfig(initial_frames=2, min_frame_gap_seconds=2,
                                 context_before_seconds=4, context_after_seconds=4,
                                 max_segment_seconds=15, max_total_segment_seconds=12,
                                 max_segments=1)
    plan = plan_segments(50, [subtitle(10, "太阳升起", "a"),
                              subtitle(14, "实验失败", "b")], [], config)
    assert len(plan.segments) == 1
    clip = plan.segments[0]
    assert (clip.start_seconds, clip.end_seconds) == (6, 18)
    assert clip.frame_times == (10, 14)
    assert {item.source_id for item in clip.evidence} == {"a", "b"}


def test_segment_cap_does_not_trim_away_context_or_leave_overlapping_clips():
    config = VideoSamplingConfig(initial_frames=2, min_frame_gap_seconds=2,
                                 context_before_seconds=4, context_after_seconds=4,
                                 max_segment_seconds=8)
    plan = plan_segments(50, [subtitle(10, "太阳升起", "a"),
                              subtitle(14, "实验失败", "b")], [], config)
    assert len(plan.segments) == 1
    assert plan.segments[0].end_seconds - plan.segments[0].start_seconds == 8
    assert "overlapping_context_exceeds_segment_limit" in plan.warnings


def test_total_seconds_limit_preserves_whole_context_windows():
    rows = [subtitle(10, "早上的风景", "a"), subtitle(40, "运行实验", "b"),
            subtitle(70, "结果总结", "c")]
    config = VideoSamplingConfig(initial_frames=3, context_before_seconds=5,
                                 context_after_seconds=5, max_total_segment_seconds=20)
    plan = plan_segments(100, rows, [], config)
    assert len(plan.segments) == 2
    assert sum(item.end_seconds - item.start_seconds for item in plan.segments) == 20
    assert "segment_budget_exhausted" in plan.warnings
    tiny_budget = plan_segments(100, rows, [], VideoSamplingConfig(
        max_total_segment_seconds=2))
    assert tiny_budget.frames and not tiny_budget.segments
    assert "segment_budget_exhausted" in tiny_budget.warnings


def test_total_limit_uses_durations_without_timestamp_cancellation():
    # Adding a distant absolute end before subtracting its start can round the
    # aggregate down across this budget boundary (13.9 + 500 - 486 < 13.9 + 14).
    config = VideoSamplingConfig(initial_frames=2, focus_terms=("重点",),
                                 max_total_segment_seconds=math.nextafter(27.9, 0))
    plan = plan_segments(1000, [subtitle(5.9, "重点电池", "first"),
                                subtitle(492, "其他话题", "second")], [], config)
    assert len(plan.segments) == 1
    assert math.fsum(item.end_seconds - item.start_seconds for item in plan.segments
                     ) <= config.max_total_segment_seconds


@pytest.mark.parametrize("limits", [{"max_segments": 0}, {"max_total_segment_seconds": 0}])
def test_clip_stage_can_be_disabled_without_disabling_initial_frames(limits):
    plan = plan_segments(100, [subtitle(50)], [], VideoSamplingConfig(**limits))
    assert evidence(plan) and not plan.segments


def test_start_end_and_very_short_video_never_produce_eof_frames():
    config = VideoSamplingConfig(min_frame_gap_seconds=0.1)
    plan = plan_segments(100, [SubtitleSpan(0, 1, "开头", "start"),
                               SubtitleSpan(99, 100, "结尾", "end")], [], config)
    assert plan.segments[0].start_seconds == 0
    assert plan.segments[-1].end_seconds == 100
    assert all(0 <= frame.at_seconds < 100 for frame in plan.frames)
    short = plan_segments(0.001, [], [], config)
    assert len(short.frames) == 1 and 0 <= short.frames[0].at_seconds < 0.001


def test_float_rounding_never_selects_exact_eof_or_an_empty_clip():
    duration = 100.0
    plan = plan_segments(duration, [SubtitleSpan(math.nextafter(duration, 0), duration,
                                                 "最末字幕", "last")], [], VideoSamplingConfig())
    assert all(point.at_seconds < duration for point in plan.frames)
    tiny = math.nextafter(0.0, 1.0)
    plan = plan_segments(tiny, [SubtitleSpan(0, tiny, "极短字幕", "tiny")], [],
                         VideoSamplingConfig(context_after_seconds=0))
    assert plan.frames[0].at_seconds == 0 and not plan.segments
    assert "empty_context_at_numeric_boundary" in plan.warnings


def test_bounded_input_text_and_evidence_report_partial_selection():
    rows = [subtitle(20, "很长的字幕" * 20, "first"),
            subtitle(50, "another source", "second"),
            subtitle(80, "重点", "beyond-limit")]
    config = VideoSamplingConfig(max_subtitles=2, max_danmaku=3, max_text_chars=10,
                                 max_evidence_per_selection=1, focus_terms=("重点",))
    comments = [DanmakuPoint(40 + index / 20, f"{index} 独立的观众意见", f"dm-{index}")
                for index in range(8)]
    plan = plan_segments(100, rows, comments, config)
    assert "subtitle_text_input_limit" in plan.warnings
    assert "audience_danmaku_input_limit" in plan.warnings
    assert "subtitle_text_partial" in plan.warnings
    assert "selection_evidence_limit" in plan.warnings
    assert all(len(item.text) <= 10 for item in evidence(plan))
    assert all(item.source_id != "beyond-limit" for item in evidence(plan))
    assert all(len(frame.evidence) <= 1 for frame in plan.frames)


def test_input_order_and_repeated_calls_are_deterministic_without_mutation():
    rows = [subtitle(20, "今天讨论电池温度", "first"),
            subtitle(70, "再观察散热系统", "second")]
    comments = burst(60, 5)
    before = [asdict(row) for row in rows], [asdict(row) for row in comments]
    config = VideoSamplingConfig()
    expected = plan_segments(100, rows, comments, config)
    assert plan_segments(100, rows[::-1], comments[::-1], config) == expected
    assert plan_segments(100, rows, comments, config) == expected
    assert before == ([asdict(row) for row in rows], [asdict(row) for row in comments])


def test_generated_inputs_respect_caps_spacing_and_provenance():
    randomizer = random.Random(712)
    for _ in range(30):
        duration = randomizer.uniform(1, 1000)
        config = VideoSamplingConfig(initial_frames=randomizer.randint(1, 8),
                                     min_frame_gap_seconds=randomizer.uniform(0.1, 30),
                                     max_segments=randomizer.randint(0, 5),
                                     max_total_segment_seconds=randomizer.uniform(0, 60))
        rows = [SubtitleSpan(at, min(duration, at + randomizer.uniform(0.01, 15)),
                             f"字幕 topic {index}", f"sub-{index}")
                for index in range(60) for at in [randomizer.random() * duration]]
        comments = [DanmakuPoint(randomizer.random() * duration, f"comment {index}",
                                f"dm-{index}") for index in range(120)]
        plan = plan_segments(duration, rows, comments, config)
        assert 1 <= len(plan.frames) <= config.initial_frames
        assert all(0 <= point.at_seconds < duration for point in plan.frames)
        assert all(right.at_seconds - left.at_seconds >= config.min_frame_gap_seconds
                   for left, right in pairwise(plan.frames))
        assert len(plan.segments) <= config.max_segments
        assert math.fsum(item.end_seconds - item.start_seconds for item in plan.segments
                         ) <= config.max_total_segment_seconds
        assert all(0 <= item.start_seconds < item.end_seconds <= duration
                   and item.end_seconds - item.start_seconds <= config.max_segment_seconds
                   for item in plan.segments)
        assert all(left.end_seconds < right.start_seconds
                   for left, right in pairwise(plan.segments))
        source_ids = {row.source_id for row in [*rows, *comments]}
        assert all(item.source_id in source_ids for item in evidence(plan))
