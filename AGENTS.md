# AGENTS — AI Coding Agent Instructions

## Purpose
- Short guidance for AI coding agents to be immediately productive in this repository.

## Глобальная цель проекта

Разработать агентную систему на базе LLM, которая:

1. Принимает на вход PDF проектной спецификации (ОВ-лист) с оборудованием сторонних поставщиков.
2. Автоматически извлекает позиции воздуховодов, фасонных частей и клапанов.
3. Подбирает совместимые аналоги из производственной номенклатуры завода **ВОК-Регион**.
4. Формирует XML-файл для импорта в **1С Sofтвент**.
5. Исключает ручной ввод данных менеджером.

## Quick usage
- Run build and tests before making large changes:
  - `./gradlew build`
  - `./gradlew test`
  - Windows: `gradlew.bat build`
  - Quick run: `run.bat` (project root)

## Where to find essential project information
- Platform/server core: [mcp-bsl-platform-context/README.md](mcp-bsl-platform-context/README.md)
- Detailed docs: [mcp-bsl-platform-context/documentation](mcp-bsl-platform-context/documentation/)
- Project brief and tech context: [mcp-bsl-platform-context/memory-bank/projectbrief.md](mcp-bsl-platform-context/memory-bank/projectbrief.md), [mcp-bsl-platform-context/memory-bank/techContext.md](mcp-bsl-platform-context/memory-bank/techContext.md)
- Existing custom agent modes: [mcp-bsl-platform-context/custom_modes/](mcp-bsl-platform-context/custom_modes/)
- Python helpers and tests in repo root: [test_mcp.py](test_mcp.py), [test_mcp2.py](test_mcp2.py), `query_*.py`, `generate_order_xml.py`

## Architecture & conventions (short)
- Build: Gradle (Kotlin DSL) in `mcp-bsl-platform-context/`.
- Runtime modes: `stdio` (IDE integration) and `sse` (networked HTTP/SSE).
- Java runtime: JDK is included under `jdk-17.0.19+10/` for local runs.
- Python utilities: many one-off scripts in repo root for XML processing and 1C queries.

## Agent behaviour guidelines
- Link, don't embed: reference existing docs instead of copying them.
- Minimal by default: only add instructions that agents can't discover programmatically.
- Ask before large changes: open a short PR or propose changes in issue when unsure.
- Run unit/integration tests and linters where applicable before pushing changes.

## Common tasks and where to look
- Build & test: `mcp-bsl-platform-context/gradlew` and `gradlew.bat` (root)
- Server entrypoints and run modes: `mcp-bsl-platform-context/run.bat`, `Dockerfile.*` in that folder
- Docs & how-tos: many markdown files in repo root (XML import, order load guides) and `mcp-bsl-platform-context/documentation/`

## Next suggested customizations
- Add module-specific agent instructions for `mcp-bsl-platform-context/` to cover build, run, and test specifics.
- Create short instruction files for Python utilities in repo root describing their purpose and expected inputs.

## Contact / Feedback
- If any instruction here is unclear or missing, update this file with a short note and a link to the authoritative doc.
