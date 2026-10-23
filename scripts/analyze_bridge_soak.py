"""Summarize retained bridge soak reports, resource samples and player journals."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics


def analyze(root):
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    resources = [json.loads(line) for line in (root / 'report.resources.jsonl').read_text(encoding='utf-8').splitlines()]
    progress = [json.loads(line.split('NATIVE_SOAK_PROGRESS ', 1)[1])
                for line in (root / 'console.log').read_text(encoding='utf-8').splitlines()
                if 'NATIVE_SOAK_PROGRESS ' in line]
    events = Counter()
    closure_flags = Counter()
    compression = Counter()
    max_backpressure_ms = 0
    health = dict(log_dropped=0, log_write_errors=0)
    log_bytes = log_files = malformed = 0
    player_folders = set()
    for path in root.glob('report-logs/players-*/*/*/*.jsonl'):
        log_files += 1
        log_bytes += path.stat().st_size
        player_folders.add(str(path.parent.relative_to(root)))
        with path.open(encoding='utf-8') as source:
            for line in source:
                try:
                    row = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                events[row.get('event', 'unknown')] += 1
                if row.get('event') == 'stream_closed' and 'clean_eof' in row:
                    status = 'clean' if row['clean_eof'] else 'eof_not_confirmed'
                    lifetime = 'long' if row.get('duration_ms', 0) > 1000 else 'short'
                    closure_flags[status + '_' + lifetime] += 1
                if row.get('event') == 'compression_summary':
                    for key in ('raw_bytes', 'wire_bytes', 'compressed_blocks'):
                        compression[key] += row.get(key, 0)
                if row.get('event') == 'backpressure_end':
                    max_backpressure_ms = max(max_backpressure_ms, row.get('duration_ms', 0))
                for key in health:
                    health[key] = max(health[key], row.get(key, 0))
    throughput = [sum(player['mib_s'] for player in row['players']) for row in progress]
    warm = [row['native_rss_bytes'] / 1024**2 for row in resources if 10 <= row['elapsed_seconds'] <= 60]
    tail = [row['native_rss_bytes'] / 1024**2 for row in resources
            if row['elapsed_seconds'] >= report.get('duration_seconds', 0) - 60 and row['native_rss_bytes']]
    players = report.get('players', [])
    return dict(passed=report.get('passed', False), duration_seconds=report['duration_seconds'],
                measured_load_seconds=report.get('elapsed_seconds'),
                clients=report.get('clients'), bulk_streams_per_client=report.get('bulk_streams_per_client'),
                path=report.get('path'), game_protocol=report.get('game_protocol'),
                bulk_rate_per_stream=report.get('bulk_rate_per_stream'),
                payload_echoed_gib=round(sum(player['mib'] for player in players) / 1024, 3),
                payload_echoed_average_mib_s=round(sum(player['mib'] for player in players) / report['duration_seconds'], 3),
                total_forwarded_payload_gib=round(2 * sum(player['mib'] for player in players) / 1024, 3),
                probe_rtt_p99_max_ms=max((player['rtt_p99_ms'] for player in players), default=None),
                probe_rtt_max_ms=max((player['rtt_max_ms'] for player in players), default=None),
                bulk_max_gap_ms=max((player['bulk_max_gap_ms'] for player in players), default=None),
                bulk_gaps_over_100_ms=sum(player['bulk_gaps_over_100_ms'] for player in players),
                bulk_gaps_over_250_ms=sum(player['bulk_gaps_over_250_ms'] for player in players),
                churn_connections=report.get('churn_connections'),
                interval_throughput_min_mib_s=min(throughput, default=None),
                interval_throughput_max_mib_s=max(throughput, default=None),
                native_rss_warmup_median_mib=round(statistics.median(warm), 3) if warm else None,
                native_rss_final_minute_median_mib=round(statistics.median(tail), 3) if tail else None,
                native_rss_peak_mib=report.get('peak_native_processes_rss_mib'),
                test_tree_rss_peak_mib=report.get('peak_test_tree_rss_mib'),
                sampled_test_tree_cpu_seconds=report.get('sampled_test_tree_cpu_seconds'),
                resource_sample_count=len(resources), progress_interval_count=len(progress),
                player_log_files=log_files, player_log_folders=len(player_folders),
                player_log_mib=round(log_bytes / 1024**2, 3), malformed_log_rows=malformed,
                journal_health=health, bridge_closure_flags=dict(closure_flags),
                compression_totals=dict(compression), max_backpressure_ms=round(max_backpressure_ms, 3),
                events=dict(events), binary_sha256=report.get('binary_sha256'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifact', type=Path)
    args = parser.parse_args()
    summary = analyze(args.artifact)
    (args.artifact / 'analysis.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
