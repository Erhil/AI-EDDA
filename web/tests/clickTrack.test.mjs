import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { renderClickTrack as render } from '../src/clickTrack.ts';
const levels = { metronome: 1, hits: 1 };
test('adjacent fragments preserve tails at nonzero origin', () => {
  const events = [{timeSec: 0.1, kind: 'beat'}, {timeSec: 0.11, kind: 'hit', lane: 2}];
  const whole = render(events, 105, 30, 1000, levels);
  assert.deepEqual(new Float32Array([...render(events, 105, 10, 1000, levels), ...render(events, 115, 20, 1000, levels)]), whole);
  assert.ok(whole.some(x => x !== 0));
});
test('simultaneous hits add and mute is silent', () => {
  const event = {timeSec: 0, kind: 'hit', lane: 1};
  const one = render([event], 0, 20, 1000, levels);
  const two = render([event, event], 0, 20, 1000, levels);
  assert.deepEqual(two, Float32Array.from(one, x => x * 2));
  assert.ok(render([event], 0, 20, 1000, {metronome: 0, hits: 0}).every(x => x === 0));
  assert.ok(render([], 0, 20, 1000, levels).every(x => x === 0));
});
test('rejects invalid inputs', () => {
  for (const event of [{timeSec: -1, kind: 'beat'}, {timeSec: 0, kind: 'bad'}, {timeSec: 0, kind: 'hit', lane: 4}]) assert.throws(() => render([event], 0, 1, 1000, levels));
  assert.throws(() => render([], -1, 1, 1000, levels));
  assert.throws(() => render([], 0, 1, 0, levels));
  assert.throws(() => render([], 0, 1, 1000, {metronome: 2, hits: 0}));
});
