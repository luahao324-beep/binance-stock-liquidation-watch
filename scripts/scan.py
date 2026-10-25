"""Scan normalized, completed one-minute candles; no network or side effects beyond output."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import statistics


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Explicit timezone required')
    ms = int(parsed.timestamp() * 1000)
    if ms % 60000:
        raise ValueError('Window boundaries must align to minutes')
    return ms


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def scan(data, start, end, drop=2.0, multiple=5.0, minimum=100000.0, merge=30):
    if end <= start or start % 60000 or end % 60000:
        raise ValueError('Invalid window')
    if not (0 < drop <= 100 and multiple > 0 and minimum >= 0 and merge >= 0):
        raise ValueError('Invalid thresholds')
    if not data.get('symbols'):
        raise ValueError('No symbols')
    result = {'startUTC': iso(start), 'endUTC': iso(end), 'parameters': dict(dropPct=drop, volumeMultiple=multiple, minimumQuoteVolume=minimum, mergeMinutes=merge), 'symbols': {}}
    for symbol, raw in data['symbols'].items():
        rows = []
        for row in raw:
            if len(row) != 6 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in row):
                raise ValueError(f'{symbol}: expected six finite numeric fields')
            t, o, h, l, c, q = row
            if t % 60000 or not (0 < l <= min(o, c) <= max(o, c) <= h) or q < 0:
                raise ValueError(f'{symbol}: invalid candle')
            if start - 3600000 <= t < end:
                rows.append(row)
        rows.sort(key=lambda r: r[0])
        expected = (end - start) // 60000 + 60
        if len(rows) != expected or any(r[0] != start - 3600000 + i * 60000 for i, r in enumerate(rows)):
            raise ValueError(f'{symbol}: missing or duplicated minutes including warmup')
        events, signals, severe = [], [], []
        for i in range(60, len(rows)):
            r = rows[i]
            r5 = 100 * (r[4] / rows[i-5][4] - 1)
            r15 = 100 * (r[4] / rows[i-15][4] - 1)
            avg = sum(x[5] for x in rows[i-60:i]) / 60
            if r5 <= -5 or r15 <= -5:
                severe.append({'knownAtUTC': iso(r[0]+60000), 'return5mPct': r5, 'return15mPct': r15})
            if r5 > -drop or r[5] < minimum or r[5] < multiple * avg:
                continue
            signal = {'knownAtUTC': iso(r[0]+60000), 'close': r[4], 'return5mPct': r5, 'quoteVolume': r[5], 'volumeMultiple': r[5]/avg if avg else None, 'zeroVolumeBaseline': avg == 0}
            signals.append(signal)
            if events and r[0] - events[-1]['lastOpenTimeMs'] <= merge * 60000:
                events[-1]['lastOpenTimeMs'] = r[0]
                events[-1]['signalCount'] += 1
            else:
                events.append({'firstSignal': signal, 'baseline': statistics.median(x[4] for x in rows[i-15:i]), 'lastOpenTimeMs': r[0], 'signalCount': 1, 'liquidationEvidence': 'unconfirmed'})
        result['symbols'][symbol] = {'evaluatedMinutes': len(rows)-60, 'events': events, 'signals': signals, 'severePriceSignals': severe}
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--start', required=True, type=timestamp)
    p.add_argument('--end', required=True, type=timestamp)
    p.add_argument('--drop-pct', type=float, default=2)
    p.add_argument('--volume-multiple', type=float, default=5)
    p.add_argument('--minimum-quote-volume', type=float, default=100000)
    p.add_argument('--merge-minutes', type=int, default=30)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    if a.output.resolve() == a.input.resolve():
        p.error('Output must not overwrite input')
    try:
        result = scan(json.loads(a.input.read_text()), a.start, a.end, a.drop_pct, a.volume_multiple, a.minimum_quote_volume, a.merge_minutes)
    except (ValueError, TypeError, KeyError) as e:
        p.error(str(e))
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'output': str(a.output), 'events': {s: len(v['events']) for s,v in result['symbols'].items()}}))


if __name__ == '__main__':
    main()
