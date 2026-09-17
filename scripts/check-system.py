#!/usr/bin/env python3
"""Read-only preflight for the bundled Linux/NVIDIA installation profile."""
import argparse
import csv
import io
import json
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3


def run(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def collect():
    ram = 0
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
                ram = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    gpus = []
    output = run(['nvidia-smi', '--query-gpu=name,memory.total,memory.free,driver_version,compute_cap', '--format=csv,noheader,nounits'])
    for row in csv.reader(io.StringIO(output or '')):
        try:
            name, total, free, driver, capability = [v.strip() for v in row]
            gpus.append({'name': name, 'total_mib': float(total), 'free_mib': float(free),
                         'driver': driver, 'compute_capability': float(capability)})
        except (ValueError, TypeError):
            continue
    encoders = run(['ffmpeg', '-hide_banner', '-encoders']) or ''
    filters = run(['ffmpeg', '-hide_banner', '-filters']) or ''
    tools = {name: bool(shutil.which(name)) for name in
             ('git', 'curl', 'sha256sum', 'uv', 'ffmpeg', 'ffprobe', 'ollama', 'systemctl', 'systemd-run')}
    return {'system': platform.system(), 'machine': platform.machine(), 'ram_gib': ram / GIB,
            'disk_free_gib': shutil.disk_usage(ROOT).free / GIB, 'gpus': gpus, 'tools': tools,
            'media_support': all(word in encoders for word in ('libx264', ' aac ')) and 'drawtext' in filters,
            'user_services': run(['systemctl', '--user', 'show', '-p', 'Version', '--value']) is not None}


def assess(info):
    checks = []
    def add(name, ok, detail, warning=False):
        checks.append({'check': name, 'status': 'pass' if ok else 'warn' if warning else 'fail', 'detail': detail})
    add('Platform', info['system'] == 'Linux' and info['machine'] in ('x86_64', 'AMD64'),
        f"{info['system']} {info['machine']}. Bundled installer targets Linux x86_64 with NVIDIA; other platforms need a separate validated setup.")
    add('System memory', info['ram_gib'] >= 30,
        f"{info['ram_gib']:.1f} GiB visible. Installation policy: 32 GB installed RAM or more; 64 GB tested.")
    add('Free project disk space', info['disk_free_gib'] >= 40,
        f"{info['disk_free_gib']:.1f} GiB available. Allow at least 40 GiB for installation headroom, plus growing media storage. Check Ollama's model volume separately.")
    suitable = []
    for gpu in info['gpus']:
        try:
            driver_ok = int(gpu['driver'].split('.')[0]) >= 580
        except ValueError:
            driver_ok = False
        if gpu['total_mib'] >= 16000 and gpu['compute_capability'] >= 7.5 and driver_ok:
            suitable.append(gpu)
    add('NVIDIA GPU and driver', bool(suitable),
        '; '.join(f"{g['name']}: {g['total_mib']:.0f} MiB VRAM, driver {g['driver']}, compute {g['compute_capability']}" for g in info['gpus'])
        or 'No usable NVIDIA report. Check nvidia-smi and the driver. This profile requires 16 GB VRAM, compute capability 7.5+, and driver 580+.')
    if info['gpus'] and not suitable:
        checks[-1]['detail'] += '. This profile requires 16 GB VRAM, compute capability 7.5+, and driver 580+.'
    if suitable:
        add('GPU headroom', any(g['free_mib'] >= 12000 for g in suitable),
            'Aim for 12 GiB free before loading YuE2; an already loaded runtime can account for occupied memory.', warning=True)
        if len(info['gpus']) > 1:
            add('GPU selection', False, 'Multiple GPUs detected. Confirm PyTorch selects a suitable device before starting music.', warning=True)
    for name, available in info['tools'].items():
        add(name, available, 'Available on PATH.' if available else 'Missing. See INSTALL.md for its official download source.')
    add('FFmpeg features', info['media_support'], 'Packaging requires libx264, AAC, and the drawtext filter.')
    add('User services', info['user_services'], 'The launcher needs a working systemd user session; run as the desktop user, not root.')
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Return machine-readable results for installation agents.')
    args = parser.parse_args()
    info = collect()
    checks = assess(info)
    ready = not any(check['status'] == 'fail' for check in checks)
    report = {'ready': ready, 'profile': 'linux-nvidia-int8', 'system': info, 'checks': checks,
              'limits': 'Preflight checks prerequisites, not model inference, audio quality, cloud access, or 24/7 reliability. Only the RTX 4080 SUPER / 64 GB configuration has been benchmarked here.'}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for check in checks:
            print(f"[{check['status'].upper()}] {check['check']}: {check['detail']}")
        print('\n' + ('Prerequisites passed.' if ready else 'Resolve failed checks before downloading the runtime.'))
        print(report['limits'])
    return 0 if ready else 1


if __name__ == '__main__':
    raise SystemExit(main())
