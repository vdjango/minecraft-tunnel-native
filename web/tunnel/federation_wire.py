"""Compact refreshes reconstruct the original signed record before verification."""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def state_digest(body):
    return digest({key: value for key, value in body.items() if key != 'issued'})


def versions(records):
    return {node: {'state': state_digest(record['body']), 'issued': record['body']['issued']}
            for node, record in records.items()}


def compact(records, known):
    if not isinstance(known, dict) or len(known) > 32:
        return list(records.values())
    result = []
    for node, record in records.items():
        previous = known.get(node)
        state = state_digest(record['body'])
        if not isinstance(previous, dict) or previous.get('state') != state:
            result.append(record)
        elif previous.get('issued') != record['body']['issued']:
            result.append({'refresh': {'id': node, 'issued': record['body']['issued'], 'state': state},
                           'proof': record['proof'], 'signature': record['signature']})
    return result


def expand(record, records):
    if 'refresh' not in record:
        return record
    refresh = record['refresh']
    previous = records.get(refresh['id'])
    if previous is None or state_digest(previous['body']) != refresh['state']:
        raise ValueError('Unknown federation refresh base')
    return {'body': dict(previous['body'], issued=refresh['issued']),
            'proof': record['proof'], 'signature': record['signature']}
