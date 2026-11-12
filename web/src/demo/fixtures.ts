import type { Chart, ProjectView } from '../generated/ui-contract';

const id = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, '0')}`;
const DEMO_MEDIA_ID = '00000000-0000-4000-8000-000000000777';
const AUDIO_HASH = '5896f231d1940c4a3bb5b3ce3a7370186eae9262a24759da9cceffc29835d107';
const COVER_HASH = 'ac476388f7e58d7fdcb4b90975813de17f94c57e99ab8c625ad29267e5f347f9';

function project(n: number, title: string): ProjectView {
  const charts: Chart[] = [0, 1].map((index) => ({
    id: id(n * 100 + index + 10), revision: 1, grid_revision: 1,
    metadata: { name: index ? 'Advanced' : 'Practice', rank: index ? 5 : 2, speed: 10 },
    review: 'approved', provenance: { created_by: 'manual' },
    events: [0, 1, 2, 3].map((lane) => ({
      id: id(n * 1000 + index * 100 + lane + 1),
      time_ref: { kind: 'beat', beat_q: lane * 2 + 2 },
      provenance: { created_by: 'manual' },
      notes: (lane === 0 ? [0, 1] : [lane]).map((noteLane) => ({
        id: id(n * 10000 + index * 1000 + lane * 10 + noteLane + 1), lane: noteLane,
        provenance: { created_by: 'manual' },
      })),
    })),
  }));
  return {
    project: { id: id(n), revision: 1, schema_version: 1,
      metadata: { title, artist: 'Demo Artist', mapper: 'Demo Mapper', subtitle: '', explicit: false },
      // The hash identifies the deterministic canonicalPCM WAV signal. The OGG-shaped client path has no downloadable audio file.
      audio: { path: `audio/${DEMO_MEDIA_ID}/song.ogg`, sha256: AUDIO_HASH, sample_rate: 48000, channels: 1, frames: 1440000, duration_sec: 30, subtype: 'PCM_16' },
      cover: { path: `assets/${DEMO_MEDIA_ID}/cover.jpg`, sha256: COVER_HASH, width: 256, height: 256 },
      analysis: { id: id(n * 100 + 90), revision: 1 }, plan_inputs: null,
      grid_revision: 1, chart_heads: charts.map((chart) => ({ id: chart.id!, revision: chart.revision! })),
      export_presets: [], provenance: { created_by: 'manual' } },
    grid: { revision: 1, segments: [{ start_time_sec: 0, start_beat_q: 0, bpm_quarter: 120, grid_division: 4, origin: 'manual', review: 'approved' }], meter: { numerator: 4, denominator: 4 }, review: 'approved', anchors: [] },
    charts,
  };
}

/** Fixed synthetic project views. Each call returns independent objects. */
export function createFixtures(): ProjectView[] {
  return [project(1, 'Northern Lights'), project(2, 'Ocean Drive')];
}
