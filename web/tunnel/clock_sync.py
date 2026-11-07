"""Installer setup and HTTPS fallback for the host's normal NTP service."""
from pathlib import Path
import hashlib
import subprocess
import sys


NTP_SERVICES = ('chrony.service', 'chronyd.service', 'ntpsec.service',
                'ntp.service', 'systemd-timesyncd.service')
SOURCES = ('https://www.aliyun.com', 'https://www.qq.com')


def systemctl(*args):
    return subprocess.run(['systemctl', *args], capture_output=True, text=True, timeout=20)


def configure():
    """Keep an existing NTP provider; do not replace administrator configuration."""
    current = next((name for name in NTP_SERVICES
                    if systemctl('is-active', '--quiet', name).returncode == 0), None)
    if current:
        systemctl('enable', current)
    else:
        for name in NTP_SERVICES:
            if systemctl('show', '--property=LoadState', '--value', name).stdout.strip() == 'loaded':
                if systemctl('enable', '--now', name).returncode == 0:
                    current = name
                    break
    if not current:
        print('No NTP provider could be enabled; HTTPS fallback remains available.', flush=True)
    # htpdate's distribution daemon may have been started by apt. Only replace
    # a pristine configuration; preserve a deliberately configured time service.
    defaults = Path('/etc/default/htpdate')
    if defaults.is_file():
        result = subprocess.run(['dpkg-query', '-W', '-f=${Conffiles}', 'htpdate'],
                                capture_output=True, text=True, timeout=10)
        digest = hashlib.md5(defaults.read_bytes()).hexdigest()
        if any(line.split()[:2] == [str(defaults), digest] for line in result.stdout.splitlines()):
            systemctl('disable', '--now', 'htpdate.service')
    result = systemctl('enable', '--now', 'buggcraft-clock-sync.timer')
    if result.returncode:
        raise RuntimeError('Could not enable automatic clock synchronization: ' + result.stderr.strip())
    result = systemctl('start', '--no-block', 'buggcraft-clock-sync.service')
    if result.returncode:
        raise RuntimeError('Could not schedule initial clock synchronization: ' + result.stderr.strip())
    print('Automatic clock synchronization configured; network requests run in the background.', flush=True)


def synchronize():
    synchronized = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                                  capture_output=True, text=True, timeout=10)
    if synchronized.returncode == 0 and synchronized.stdout.strip() == 'yes':
        print('System NTP is synchronized; HTTPS fallback is idle.', flush=True)
        return
    if systemctl('is-active', '--quiet', 'htpdate.service').returncode == 0:
        print('Using the existing htpdate daemon; no second clock adjustment started.', flush=True)
        return
    # htpdate checks multiple reference servers. TLS verification is mandatory.
    # A failed network check never prevents the node service from starting.
    subprocess.run(['htpdate', '-s', '-c', '-4', '-n', '-p', '1', *SOURCES],
                   check=True, timeout=45)


if __name__ == '__main__':
    if sys.argv[1:] == ['--configure']:
        configure()
    elif not sys.argv[1:]:
        synchronize()
    else:
        raise SystemExit('Usage: clock_sync.py [--configure]')
