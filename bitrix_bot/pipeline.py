"""Оркестрация пайплайна PDF → заказ 1С (реализация — ниже в этом файле, Task 4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PipelineResult:
    """Результат обработки одного PDF для отчёта бота."""

    file_name: str
    order_number: Optional[str] = None
    loaded: List[Dict[str, Any]] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    errors_1c: List[str] = field(default_factory=list)
    warnings_1c: List[str] = field(default_factory=list)
    raw_text: str = ""
