"""Keep small, reproducible evidence with the source, excluding private profiles."""
import json
from pathlib import Path
import shutil
import statistics

ROOT = Path(__file__).resolve().parents[1]
destination = ROOT / 'docs/measurements'
destination.mkdir(parents=True, exist_ok=True)
ubuntu = ROOT / 'validation-artifacts/ubuntu-20261023'
for source, name in [('benchmark-ubuntu-final.json', 'ubuntu-aes-gcm.json'),
                     ('benchmark-repeat1.json', 'ubuntu-repeat1.json'),
                     ('benchmark-repeat2.json', 'ubuntu-repeat2.json')]:
    shutil.copyfile(ubuntu / source, destination / name)
vanilla = ROOT / 'validation-artifacts/vanilla-20261023-011827'
report = json.loads((vanilla / 'report.json').read_text(encoding='utf-8'))
report['clients'] = [dict(name=client['name'], rejoined='rejoined_pid' in client) for client in report['clients']]
(destination / 'vanilla-1.21.8.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
shutil.copyfile(vanilla / 'gui-four-clients.png', destination / 'vanilla-four-clients.png')
trials = []
for name in ('ubuntu-aes-gcm.json', 'ubuntu-repeat1.json', 'ubuntu-repeat2.json'):
    trials.extend(t for t in json.loads((destination / name).read_text())['trials'] if t['flows'] == 32)
summary = {}
for engine in ('python', 'native'):
    rows = [trial for trial in trials if trial['engine'] == engine]
    fields = ['wall_seconds', 'server_cpu_seconds', 'payload_egress_mib_s',
              'peak_server_rss_mib', 'rtt_median_ms', 'rtt_p95_ms']
    summary[engine] = {field: dict(median=round(statistics.median(r[field] for r in rows), 4),
        minimum=min(r[field] for r in rows), maximum=max(r[field] for r in rows)) for field in fields}
(destination / 'ubuntu-32-stream-median.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
