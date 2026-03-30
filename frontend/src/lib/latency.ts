'use client';

type TimingMap = Record<string, number>;

export function createRequestId(prefix = 'req'): string {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

export function markTime(timings: TimingMap, label: string): number {
  const timestamp = performance.now();
  timings[label] = timestamp;
  return timestamp;
}

export function elapsedMs(timings: TimingMap, startLabel: string, endLabel: string): number | null {
  const start = timings[startLabel];
  const end = timings[endLabel];
  if (start === undefined || end === undefined) {
    return null;
  }
  return end - start;
}

export function logLatencyTrace(traceName: string, requestId: string, timings: TimingMap, extra: Record<string, unknown> = {}) {
  const sortedTimings = Object.entries(timings)
    .sort((a, b) => a[1] - b[1])
    .reduce<Record<string, number>>((acc, [label, value]) => {
      acc[label] = Number(value.toFixed(2));
      return acc;
    }, {});

  console.info(`[latency] ${traceName}`, {
    requestId,
    timings: sortedTimings,
    deltasMs: {
      recordToEncode: elapsedMs(timings, 'record_stop', 'audio_encoded'),
      encodeToUpload: elapsedMs(timings, 'audio_encoded', 'request_sent'),
      requestToHeaders: elapsedMs(timings, 'request_sent', 'response_headers'),
      requestToMetadata: elapsedMs(timings, 'request_sent', 'stream_metadata'),
      requestToFirstAudio: elapsedMs(timings, 'request_sent', 'first_audio_chunk'),
      requestToPlayback: elapsedMs(timings, 'request_sent', 'audio_playback_started'),
      requestToDone: elapsedMs(timings, 'request_sent', 'stream_done'),
    },
    ...extra,
  });
}
