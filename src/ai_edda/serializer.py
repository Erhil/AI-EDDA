from math import ceil, isfinite

from .timing import final_time_sec
from .validation import validate_chart


def _check_chart(chart, grid, duration=float("inf")):
    errors = [d.code for d in validate_chart(chart, grid, duration) if d.severity == "error"]
    if errors:
        raise ValueError(", ".join(errors))


def serialize_difficulty(chart, grid):
    _check_chart(chart, grid)
    base = grid.segments[0].bpm_quarter / 60
    notes = sorted(
        (final_time_sec(grid, e.time_ref) * base, n.lane) for e in chart.events for n in e.notes
    )
    return {
        "_version": "1",
        "_customData": {
            "_time": 0,
            "_BPMChanges": [
                {
                    "_time": s.start_time_sec * base,
                    "_BPM": s.bpm_quarter,
                    "_beatsPerBar": s.grid_division,
                    "_metronomeOffset": s.grid_division,
                }
                for s in grid.segments
            ],
            "_bookmarks": [],
        },
        "_events": [],
        "_notes": [
            {"_time": t, "_lineIndex": lane, "_lineLayer": 1, "_type": 0, "_cutDirection": 1}
            for t, lane in notes
        ],
        "_obstacles": [],
    }


def serialize_info(metadata, grid, duration_sec, selected_charts):
    if not isfinite(duration_sec) or duration_sec < 0:
        raise ValueError("invalid duration")
    charts = list(selected_charts)
    if not 1 <= len(charts) <= 3:
        raise ValueError("select one to three charts")
    if len({chart.id for chart in charts}) != len(charts):
        raise ValueError("duplicate selected chart id")
    for chart in charts:
        rank, speed = chart.metadata.rank, chart.metadata.speed
        if (
            rank is None
            or not 1 <= rank <= 10
            or speed is None
            or not isfinite(speed)
            or speed <= 0
        ):
            raise ValueError("invalid rank or speed")
        _check_chart(chart, grid, duration_sec)
    charts.sort(key=lambda c: c.metadata.rank)
    difficulties = []
    for slot, chart in zip(("Easy", "Normal", "Hard"), charts):
        difficulties.append(
            {
                "_difficulty": slot,
                "_difficultyRank": chart.metadata.rank,
                "_beatmapFilename": slot + ".dat",
                "_noteJumpMovementSpeed": chart.metadata.speed,
                "_noteJumpStartBeatOffset": 0,
                "_customData": {
                    "_editorOldOffset": 0,
                    "_editorGridSpacing": 2,
                    "_editorGridDivision": 4,
                    "_warnings": [],
                    "_information": [],
                    "_suggestions": [],
                    "_requirements": [],
                },
            }
        )
    return {
        "_version": "1",
        "_songName": metadata.title,
        "_songSubName": metadata.subtitle,
        "_songAuthorName": metadata.artist,
        "_levelAuthorName": metadata.mapper,
        "_explicit": str(metadata.explicit).lower(),
        "_beatsPerMinute": grid.segments[0].bpm_quarter,
        "_shuffle": 0,
        "_shufflePeriod": 0.5,
        "_previewStartTime": 0,
        "_previewDuration": 0,
        "_songApproximativeDuration": ceil(duration_sec),
        "_songFilename": "song.ogg",
        "_coverImageFilename": "cover.jpg",
        "_environmentName": "Midgard",
        "_songTimeOffset": 0,
        "_customData": {
            "_contributors": [],
            "_editors": {"AI-EDDA": {"version": "0.1.0"}, "_lastEditedBy": "AI-EDDA"},
        },
        "_difficultyBeatmapSets": [
            {"_beatmapCharacteristicName": "Standard", "_difficultyBeatmaps": difficulties}
        ],
    }
