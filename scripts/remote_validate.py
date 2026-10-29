"""Isolated Ubuntu build/validation using the workspace's existing SSH trust."""
import argparse
import importlib.util
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.path.insert(0, str(WORKSPACE / '.test-tools'))

def connect():
    from domain_ops import connect as trusted_connect
    client = trusted_connect('81.68.225.236')
    client.get_transport().set_keepalive(20)
    return client

def execute(client, command, timeout=45):
    _, out, err = client.exec_command(command, timeout=timeout)
    output, error = out.read().decode(errors='replace'), err.read().decode(errors='replace')
    print(output, end='')
    if error:
        print(error, end='', file=sys.stderr)
    status = out.channel.recv_exit_status()
    if status:
        raise SystemExit(status)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['inspect', 'upload', 'run', 'build'])
    parser.add_argument('--command')
    parser.add_argument('--command-file')
    args = parser.parse_args()
    with connect() as client:
        if args.action == 'inspect':
            execute(client, "uname -a\ncat /etc/os-release\ncommand -v cmake\ncommand -v g++\n"
                    "python3 --version\nopenssl version\nls /usr/include/openssl/ssl.h\n"
                    "nproc\nfree -m\nls -d /opt/buggcraft* /tmp/buggcraft* 2>/dev/null || true")
        elif args.action == 'upload':
            archive = ROOT / 'build' / 'ubuntu-source.tar.gz'
            with tarfile.open(archive, 'w:gz', compresslevel=1) as output:
                for name in ('core', 'bridge', 'include', 'bindings', 'web', 'site', 'scripts', 'tests', 'compatibility', 'cmake', 'CMakeLists.txt',
                             'LICENSE', 'licenses', 'THIRD_PARTY_NOTICES.md', 'requirements.txt', '.deps/libssh-0.12.2', '.deps/json', '.deps/libdatachannel', '.deps/zstd', '.deps/zlib-1.3.2'):
                    output.add(ROOT / name, arcname=name,
                               filter=lambda info: None if '__pycache__' in info.name or '.git' in Path(info.name).parts or info.name.startswith('compatibility/src') else info)
            with client.open_sftp() as sftp:
                sftp.put(str(archive), '/tmp/buggcraft-native-source.tar.gz')
            execute(client, 'mkdir -p /tmp/buggcraft-native-validation\n'
                    'tar xzf /tmp/buggcraft-native-source.tar.gz -C /tmp/buggcraft-native-validation')
        elif args.action == 'build':
            command = """cd /tmp/buggcraft-native-validation && nohup sh -c 'cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DWITH_SYMBOL_VERSIONING=ON > build-configure.log 2>&1 && cmake --build build --parallel 2 > build-compile.log 2>&1; echo $? > build.status' > build-launch.log 2>&1 < /dev/null &"""
            execute(client, command)
        else:
            command = Path(args.command_file).read_text(encoding='utf-8-sig') if args.command_file else args.command
            if not command:
                parser.error('--command is required')
            execute(client, command, timeout=55)
