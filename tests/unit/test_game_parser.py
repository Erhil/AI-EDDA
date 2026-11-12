import pytest

from ai_edda.domain import (
    AudioTimeRef,
    Chart,
    ChartMetadata,
    ChartNote,
    HitEvent,
    SongMetadata,
    TempoSegment,
    TimingGrid,
)
from ai_edda.game_import import parse_game
from ai_edda.serializer import serialize_difficulty, serialize_info


def fixture():
    grid = TimingGrid(
        segments=(
            TempoSegment(start_time_sec=0, start_beat_q=0, bpm_quarter=120, grid_division=4),
            TempoSegment(start_time_sec=2, start_beat_q=4, bpm_quarter=90, grid_division=6),
        )
    )
    chart = Chart(
        metadata=ChartMetadata(rank=3, speed=15),
        events=(
            HitEvent(
                time_ref=AudioTimeRef(kind="audio", time_sec=2.25), notes=(ChartNote(lane=1),)
            ),
        ),
    )
    metadata = SongMetadata(
        title="Song", subtitle="Sub", artist="Artist", mapper="Mapper", explicit=True
    )
    return (
        metadata,
        grid,
        serialize_info(metadata, grid, 10, [chart]),
        serialize_difficulty(chart, grid),
    )


def test_roundtrip():
    metadata, grid, info, difficulty = fixture()
    result, parsed_grid, charts = parse_game(info, {"Easy.dat": difficulty})
    assert result == metadata
    assert charts[0].events[0].time_ref.time_sec == pytest.approx(2.25, abs=1e-6)
    assert (
        serialize_difficulty(charts[0], parsed_grid)["_customData"]["_BPMChanges"]
        == difficulty["_customData"]["_BPMChanges"]
    )


@pytest.mark.parametrize("field,value", [("_lineIndex", 4), ("_time", float("nan"))])
def test_invalid_note(field, value):
    _, _, info, difficulty = fixture()
    difficulty["_notes"][0][field] = value
    with pytest.raises(ValueError):
        parse_game(info, {"Easy.dat": difficulty})


@pytest.mark.parametrize(
    "target,path,value",
    [
        ("info", ("_version",), "2"),
        ("info", ("_environmentName",), "Other"),
        ("characteristic", ("_beatmapCharacteristicName",), "Lawless"),
        ("entry", ("_noteJumpStartBeatOffset",), 1),
        ("difficulty", ("_version",), "2"),
        ("difficulty", ("_events",), [{"_time": 1}]),
        ("difficulty", ("_obstacles",), [{"_time": 1}]),
        ("note", ("_type",), 3),
        ("note", ("_lineLayer",), 0),
        ("note", ("_cutDirection",), 0),
    ],
)
def test_unsupported_gameplay_profile(target, path, value):
    _, _, info, difficulty = fixture()
    if target == "info":
        info[path[0]] = value
    elif target == "characteristic":
        info["_difficultyBeatmapSets"][0][path[0]] = value
    elif target == "entry":
        info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"][0][path[0]] = value
    elif target == "difficulty":
        difficulty[path[0]] = value
    else:
        difficulty["_notes"][0][path[0]] = value
    with pytest.raises(ValueError):
        parse_game(info, {"Easy.dat": difficulty})


def test_grid_mismatch():
    import copy

    _, _, info, difficulty = fixture()
    other = copy.deepcopy(difficulty)
    other["_customData"]["_BPMChanges"][0]["_BPM"] = 100
    entry = copy.deepcopy(info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"][0])
    entry["_beatmapFilename"] = "other.dat"
    info["_difficultyBeatmapSets"][0]["_difficultyBeatmaps"].append(entry)
    with pytest.raises(ValueError):
        parse_game(info, {"Easy.dat": difficulty, "other.dat": other})
