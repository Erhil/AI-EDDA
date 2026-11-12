from math import isfinite

from .domain import (
    AudioTimeRef,
    Chart,
    ChartMetadata,
    ChartNote,
    HitEvent,
    Provenance,
    SongMetadata,
    TempoSegment,
    TimingGrid,
)


def _number(value, name, positive=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or (positive and value <= 0)
    ):
        raise ValueError(f"invalid {name}")
    return value


def parse_game(info: dict, difficulties: dict[str, dict]):
    if info.get("_version") != "1":
        raise ValueError("unsupported info version")
    bpm = _number(info["_beatsPerMinute"], "BPM", True)
    if _number(info.get("_songTimeOffset", 0), "offset") != 0:
        raise ValueError("unsupported offset")
    explicit = info.get("_explicit", "false")
    if explicit not in ("true", "false"):
        raise ValueError("invalid explicit string")
    metadata = SongMetadata(
        title=info["_songName"],
        subtitle=info.get("_songSubName", ""),
        artist=info["_songAuthorName"],
        mapper=info["_levelAuthorName"],
        explicit=explicit == "true",
    )
    entries = [
        entry for group in info["_difficultyBeatmapSets"] for entry in group["_difficultyBeatmaps"]
    ]
    if any(
        group.get("_beatmapCharacteristicName") != "Standard"
        for group in info["_difficultyBeatmapSets"]
    ):
        raise ValueError("unsupported characteristic")
    if info.get("_environmentName") != "Midgard":
        raise ValueError("unsupported environment")
    names = [entry["_beatmapFilename"] for entry in entries]
    if not 1 <= len(entries) <= 3 or len(set(names)) != len(names):
        raise ValueError("expected one to three unique difficulty filenames")
    imported = Provenance(created_by="imported")
    charts, grids = [], []
    for entry in entries:
        if _number(entry.get("_noteJumpStartBeatOffset", 0), "difficulty offset") != 0:
            raise ValueError("unsupported offset")
        rank = entry["_difficultyRank"]
        if type(rank) is not int or not 1 <= rank <= 10:
            raise ValueError("invalid rank")
        speed = _number(entry["_noteJumpMovementSpeed"], "speed", True)
        data = difficulties[entry["_beatmapFilename"]]
        if data.get("_version") != "1":
            raise ValueError("unsupported difficulty version")
        if data.get("_events", []) or data.get("_obstacles", []):
            raise ValueError("unsupported events or obstacles")
        markers = data.get("_customData", {}).get("_BPMChanges", [])
        segments = []
        for marker in markers:
            t = _number(marker["_time"], "marker time") * 60 / bpm
            mbpm = _number(marker["_BPM"], "marker BPM", True)
            division = marker["_beatsPerBar"]
            if type(division) is not int or division <= 0:
                raise ValueError("invalid subdivision")
            q = 0
            if segments:
                prev = segments[-1]
                exact = prev.start_beat_q + (t - prev.start_time_sec) * prev.bpm_quarter / 60
                q = round(exact)
                if abs(q - exact) > 1e-6 or q <= prev.start_beat_q:
                    raise ValueError("unsupported phase jump")
            segments.append(
                TempoSegment(
                    start_time_sec=t,
                    start_beat_q=q,
                    bpm_quarter=mbpm,
                    grid_division=division,
                    origin="imported",
                )
            )
        if not segments:
            segments = [
                TempoSegment(
                    start_time_sec=0,
                    start_beat_q=0,
                    bpm_quarter=bpm,
                    grid_division=4,
                    origin="imported",
                )
            ]
        grid = TimingGrid(segments=tuple(segments))
        grids.append(grid)
        grouped = {}
        for note in data["_notes"]:
            if any(
                note.get(key) != value
                for key, value in (("_type", 0), ("_lineLayer", 1), ("_cutDirection", 1))
            ):
                raise ValueError("unsupported note attributes")
            t = _number(note["_time"], "note time") * 60 / bpm
            lane = note["_lineIndex"]
            if type(lane) is not int or not 0 <= lane <= 3:
                raise ValueError("invalid lane")
            grouped.setdefault(t, []).append(ChartNote(lane=lane, provenance=imported))
        events = tuple(
            HitEvent(
                time_ref=AudioTimeRef(kind="audio", time_sec=t),
                notes=tuple(notes),
                provenance=imported,
            )
            for t, notes in sorted(grouped.items())
        )
        charts.append(
            Chart(
                metadata=ChartMetadata(name=entry.get("_difficulty", ""), rank=rank, speed=speed),
                provenance=imported,
                events=events,
            )
        )
    if any(grid != grids[0] for grid in grids[1:]):
        raise ValueError("difficulty grid mismatch")
    return metadata, grids[0], tuple(charts)
