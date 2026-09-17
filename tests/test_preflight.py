import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('preflight', Path(__file__).parents[1] / 'scripts/check-system.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def capable():
    return {'system': 'Linux', 'machine': 'x86_64', 'ram_gib': 63, 'disk_free_gib': 100,
            'gpus': [{'name': 'RTX 4080 SUPER', 'total_mib': 16376, 'free_mib': 14000,
                      'driver': '590.1', 'compute_capability': 8.9}],
            'tools': {'uv': True}, 'media_support': True, 'user_services': True}


def failed(info):
    return {c['check'] for c in preflight.assess(info) if c['status'] == 'fail'}


def test_supported_profile_and_busy_gpu():
    info = capable()
    assert not failed(info)
    info['gpus'][0]['free_mib'] = 4000
    assert not failed(info)
    assert any(c['status'] == 'warn' for c in preflight.assess(info))


def test_unsuitable_gpu_even_with_enough_total_vram():
    for changes in ({'driver': '570.1'}, {'total_mib': 12000}, {'compute_capability': 6.1}):
        info = capable()
        info['gpus'][0].update(changes)
        assert 'NVIDIA GPU and driver' in failed(info)
    info = capable()
    info['gpus'] = []
    assert 'NVIDIA GPU and driver' in failed(info)


def test_missing_requirements_are_reported_together():
    info = capable()
    info.update(system='Darwin', machine='arm64', ram_gib=16, disk_free_gib=10,
                tools={'uv': False}, media_support=False, user_services=False)
    assert failed(info) == {'Platform', 'System memory', 'Free project disk space', 'uv', 'FFmpeg features', 'User services'}
