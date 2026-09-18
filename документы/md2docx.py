#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
md2docx.py — собирает .docx для чтения из рабочего .md.

Зачем: Олеся не открывает .md. Рабочий файл — markdown, он показывает историю
изменений в git. .docx — производная для чтения, пересобирается после каждой правки.

Использование:
    python3 .tools/md2docx.py "Файл.md"        один файл
    python3 .tools/md2docx.py                  все .md в корне, кроме служебных

Работает на встроенном в macOS textutil, ставить ничего не нужно.
"""
import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Служебные файлы Агента в .docx не переводим — их читает не человек, а Агент.
SKIP = {
    "CLAUDE.md", "SOUL.md", "SOUL-coder.md", "SOUL-researcher.md",
    "SOUL-strategist.md", "MEMORY.md", "GOALS.md", "MISSION.md",
    "PROJECTS.md", "PREFERENCES.md", "USER.md", "LEARNED.md",
    "SERVICES.md", "README.md", "CHANGELOG-v2.1.0.md",
}

CSS = """
body { font-family: -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif;
       font-size: 11pt; line-height: 1.5; color: #1a1a1a; }
h1 { font-size: 22pt; margin: 24pt 0 10pt; }
h2 { font-size: 16pt; margin: 20pt 0 8pt; }
h3 { font-size: 13pt; margin: 16pt 0 6pt; }
h4 { font-size: 11.5pt; margin: 12pt 0 4pt; }
table { border-collapse: collapse; width: 100%; margin: 10pt 0; }
th, td { border: 1px solid #c8c8c8; padding: 5pt 7pt; text-align: left;
         vertical-align: top; font-size: 10pt; }
th { background: #f0f0f0; font-weight: bold; }
code { font-family: Menlo, Consolas, monospace; font-size: 9.5pt; background: #f4f4f4; }
pre  { font-family: Menlo, Consolas, monospace; font-size: 9.5pt;
       background: #f4f4f4; padding: 8pt; }
blockquote { margin: 8pt 0 8pt 16pt; padding-left: 10pt;
             border-left: 3px solid #c8c8c8; color: #444; }
hr { border: none; border-top: 1px solid #d0d0d0; margin: 16pt 0; }
li { margin: 2pt 0; }
"""


def inline(text: str) -> str:
    """Разметка внутри строки: жирный, курсив, код, ссылки."""
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def is_table_separator(line: str) -> bool:
    return bool(re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", line)) and "-" in line


def split_row(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def md_to_html(md: str) -> str:
    lines = md.split("\n")
    out, i = [], 0

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line:
            i += 1
            continue

        # Блок кода
        if line.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            i += 1
            out.append("<pre>" + "<br>".join(buf) + "</pre>")
            continue

        # Таблица: строка с | и следующая — разделитель
        if "|" in line and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
            header = split_row(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(split_row(lines[i]))
                i += 1
            cells = "".join(f"<th>{inline(c)}</th>" for c in header)
            body = ""
            for row in rows:
                body += "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>"
            out.append(f"<table><tr>{cells}</tr>{body}</table>")
            continue

        # Заголовок
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # Горизонтальная черта
        if re.match(r"^(---|\*\*\*|___)$", line):
            out.append("<hr>")
            i += 1
            continue

        # Цитата
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(inline(lines[i].strip().lstrip(">").strip()))
                i += 1
            out.append("<blockquote>" + "<br>".join(buf) + "</blockquote>")
            continue

        # Список: маркированный или нумерованный
        bullet = re.match(r"^[-*+]\s+(.*)$", line)
        number = re.match(r"^\d+[.)]\s+(.*)$", line)
        if bullet or number:
            tag = "ul" if bullet else "ol"
            pattern = r"^[-*+]\s+(.*)$" if bullet else r"^\d+[.)]\s+(.*)$"
            items = []
            while i < len(lines):
                cur = lines[i].strip()
                m2 = re.match(pattern, cur)
                if not m2:
                    # вложенная строка того же пункта
                    if cur and lines[i].startswith(("  ", "\t")) and items:
                        items[-1] += "<br>" + inline(cur)
                        i += 1
                        continue
                    break
                items.append(inline(m2.group(1)))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{x}</li>" for x in items) + f"</{tag}>")
            continue

        # Обычный абзац
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|```|---$|\*\*\*$|___$)", lines[i].strip()
        ) and not ("|" in lines[i] and i + 1 < len(lines) and is_table_separator(lines[i + 1])):
            buf.append(inline(lines[i].strip()))
            i += 1
        if buf:
            out.append("<p>" + "<br>".join(buf) + "</p>")

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>" + "".join(out) + "</body></html>"
    )


def convert(md_path: Path) -> None:
    docx_path = md_path.with_suffix(".docx")
    html_text = md_to_html(md_path.read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as fh:
        fh.write(html_text)
        tmp = fh.name
    # textutil при ошибке возвращает код 0 и пишет в stderr — проверяем сами,
    # иначе сбой пройдёт незамеченным и Олеся получит старую версию файла.
    res = subprocess.run(
        ["textutil", "-convert", "docx", "-output", str(docx_path), tmp],
        capture_output=True, text=True,
    )
    Path(tmp).unlink(missing_ok=True)
    if res.returncode != 0 or res.stderr.strip():
        print(f"✖ {docx_path.name}: {res.stderr.strip() or 'ошибка textutil'}")
        print("  Подсказка: textutil читает HTML только вне песочницы.")
        raise SystemExit(1)
    print(f"✔ {docx_path.name}")


def main() -> None:
    args = sys.argv[1:]
    if args:
        targets = [Path(a) if Path(a).is_absolute() else ROOT / a for a in args]
    else:
        targets = [p for p in sorted(ROOT.glob("*.md")) if p.name not in SKIP]
    if not targets:
        print("Нечего собирать")
        return
    for path in targets:
        if not path.exists():
            print(f"нет файла: {path}")
            continue
        convert(path)


if __name__ == "__main__":
    main()
