"""Common utilities shared across spec-to-1c modules.

This module contains domain-specific helpers that are used by both the
specification parser and the XML generator to avoid duplication.
"""

from __future__ import annotations

from typing import Optional


def material_to_code(material: Optional[str]) -> str:
    """Convert a human-readable material description to a 1C material code.

    Codes (as used by 1С function НахождениеМатериалаПоТипу):
        1 — galvanized steel (оцинкованная сталь)
        2 — stainless steel (нержавеющая сталь)
        3 — black steel (чёрная сталь)

    Direct numeric codes are returned as-is. Defaults to "1" when unknown.
    """
    text = str(material or "").lower()
    text = text.replace("ё", "е")

    if text.strip() in ("1", "2", "3"):
        return text.strip()

    # Order matters: check stainless/black before galvanized because
    # "нержавеющая" and "чёрная" may contain fragments that could be
    # misclassified otherwise.
    if "нерж" in text:
        return "2"
    if "черн" in text:
        return "3"
    if "оц" in text or "оцинк" in text:
        return "1"

    return "1"


def connection_to_code(conn: Optional[str], section: str) -> str:
    """Convert a connection description to a 1C connection code.

    1С takes the first character of the XML value, so the returned code is
    always a single digit.

    Round connections:
        1 = bandage (бандаж)
        2 = none (без соединения)
        3 = nipple (ниппель)
        4 = flange (фланец)

    Rectangular connections:
        5 = flange (фланец)
        6 = rail/angle (шина/уголок)
        7 = rail (рейка)
        8 = none (без соединения)

    0 means the side is unused.
    """
    conn = str(conn or "").strip().lower()

    if conn in ("0", ""):
        return "0"

    if conn in ("1", "2", "3", "4", "5", "6", "7", "8"):
        return conn

    normalized = conn.replace("/", " ").replace("\\", " ")
    normalized = " ".join(normalized.split())

    if section == "round":
        mapping = {
            "бандаж": "1",
            "без соединения": "2",
            "без соед": "2",
            "без": "2",
            "б/с": "2",
            "ниппель": "3",
            "фланец круглый": "4",
            "фланец кр": "4",
            "фланец (кр)": "4",
            "фланец": "4",
        }
    else:
        mapping = {
            "фланец прямоугольный": "5",
            "фланец пр": "5",
            "фланец (пр)": "5",
            "фланец": "5",
            "шина уголок": "6",
            "шина / уголок": "6",
            "шина": "6",
            "уголок": "6",
            "рейка": "7",
            "без соединения": "8",
            "без соед": "8",
            "без": "8",
            "б/с": "8",
        }

    if normalized in mapping:
        return mapping[normalized]
    return mapping.get(conn, "0")
