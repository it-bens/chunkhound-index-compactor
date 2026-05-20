from __future__ import annotations

import pytest

from chunkhound_index_compactor import human_size


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0.0 B"),
        (1024, "1.0 KiB"),
        (1024**2, "1.0 MiB"),
        (1024**3, "1.0 GiB"),
        (1024**4, "1.0 TiB"),
        (1024**5, "1.0 PiB"),
        pytest.param(1024**6, "1024.0 PiB", id="overflow_falls_off_table"),
    ],
)
def test_human_size(value: int, expected: str) -> None:
    assert human_size(value) == expected
