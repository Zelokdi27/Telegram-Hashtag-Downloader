"""Preview pipeline tests · Жизненный цикл PreviewThumbPipeline"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app.preview_core import PreviewThumbPipeline


def test_preview_pipeline_closes_executor_once(tmp_path):
    client = MagicMock()
    pipeline = PreviewThumbPipeline(client, tmp_path / "cache")

    pipeline.close()
    pipeline.close()

    assert pipeline._executor_closed is True