"""A saved region is usable only on the same screen topology and scale."""
from dataclasses import replace
import importlib.util

import pytest
from PySide6.QtCore import QRect


def region_api():
    assert importlib.util.find_spec('ctrlcctrlv.capture_region') is not None
    from ctrlcctrlv import capture_region
    return capture_region


@pytest.mark.parametrize('ratio', [1, 1.25, 1.5, 2])
def test_region_physical_crop_uses_local_coordinates_and_actual_dpr(ratio):
    api = region_api()
    result = api.physical_rect(QRect(20, 40, 41, 41), ratio)
    assert result == QRect(round(20 * ratio), round(40 * ratio), round(41 * ratio), round(41 * ratio))


def test_region_valid_for_same_topology_regardless_of_screen_enumeration_order():
    api = region_api()
    first = api.ScreenDescriptor('session-one', (-1920, 0, 1920, 1080), 1.0)
    second = api.ScreenDescriptor('session-two', (0, 0, 1280, 720), 1.5)
    region = api.CaptureRegion(first, (20, 30, 300, 200), (first, second))
    assert api.region_is_valid(region, [second, first])


@pytest.mark.parametrize('change', ['dpi', 'geometry', 'replug', 'missing', 'ambiguous', 'outside'])
def test_region_rejects_stale_or_ambiguous_screen_configuration(change):
    api = region_api()
    screen = api.ScreenDescriptor('screen-session', (-1920, 0, 1920, 1080), 1.0)
    region = api.CaptureRegion(screen, (20, 30, 300, 200), (screen,))
    current = [screen]
    if change == 'dpi': current = [replace(screen, dpr=1.5)]
    if change == 'geometry': current = [replace(screen, geometry=(0, 0, 1920, 1080))]
    if change == 'replug': current = [replace(screen, identity='new-session')]
    if change == 'missing': current = []
    if change == 'ambiguous': current = [screen, screen]
    if change == 'outside': region = replace(region, rect=(1900, 30, 300, 200))
    assert not api.region_is_valid(region, current)
