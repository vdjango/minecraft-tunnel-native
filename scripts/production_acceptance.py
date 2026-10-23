"""Public acceptance of both deployed nodes without persisting private profiles."""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from production_remote import connect
from production_release import ARTIFACT, remote_python, save

ROOT = Path(__file__).resolve().parents[1]


def run():
    nodes=[]
    for node, url in (('node1','https://81.68.225.236:18443'),('node2','https://43.241.50.19:18444')):
        with connect(node) as client:
            # The TLS certificate is fetched through the existing SSH trust.
            metadata=json.loads(remote_python(client, '''
                import configparser,hashlib,json,pathlib,ssl
                data=pathlib.Path('/var/lib/buggcraft-node')
                config=configparser.ConfigParser(interpolation=None);config.read(data/'server.conf')
                cert=(data/config['web']['tls_cert']).read_text()
                der=ssl.PEM_cert_to_DER_cert(cert)
                print(json.dumps(dict(name=json.loads((data/'node-state.json').read_text())['name'],
                    tls_sha256=hashlib.sha256(der).hexdigest(),
                    profile=json.loads((data/'relay-profile.json').read_text()))))
            '''))
        # Read the active profile through pinned SSH trust; a moved local
        # configuration directory must not select stale credentials.
        nodes.append(dict(**metadata,web_url=url))
    sys.path[:0]=[str(ROOT/'compatibility'),str(ROOT/'web')]
    with tempfile.TemporaryDirectory(prefix='buggcraft-deployment-acceptance-') as temporary:
        private=Path(temporary)/'nodes.json'
        private.write_text(json.dumps(dict(nodes=nodes)),encoding='utf-8')
        os.environ['BUGGCRAFT_TEST_DEPLOYED']='1'
        os.environ['BUGGCRAFT_DEPLOYED_CONFIG']=str(private)
        suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_deployed_nodes')
        stream=io.StringIO()
        result=unittest.TextTestRunner(verbosity=2,stream=stream).run(suite)
        evidence=stream.getvalue()
        for node in nodes:
            evidence=evidence.replace(node['profile']['token'],'[REDACTED]')
        (ARTIFACT/'public-acceptance.log').write_text(evidence,encoding='utf-8')
        print(evidence,end='')
        save('public-acceptance.json',dict(passed=result.wasSuccessful(),tests=result.testsRun,
             failures=len(result.failures),errors=len(result.errors),
             nodes=[{key:node[key] for key in ('name','web_url','tls_sha256')} for node in nodes]))
        return result.wasSuccessful()


if __name__=='__main__':
    raise SystemExit(0 if run() else 1)
