#!/usr/bin/env python3
"""history-research: Markdown-first history report builder.

Commands:
  init        scaffold a report directory
  build       compile report.md -> report.html + report.pdf
  check       verify image provenance, citations, and md/html/pdf sync
  review-kit  generate context-free reviewer prompts and a coverage matrix

The Markdown file is the single source of truth. HTML and PDF are generated
artifacts; report.build.json records the source hash so `check` can detect a
stale build.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_lib
import io
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from markdown_it import MarkdownIt
except Exception:  # pragma: no cover - reported at runtime
    MarkdownIt = None

try:
    from jinja2 import Template
except Exception:  # pragma: no cover
    Template = None

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
USER_AGENT = "history-research/1.0 (+https://github.com/Xingkai98/kai-skills)"
MAX_IMAGE_BYTES = 30 * 1024 * 1024
IMAGE_RE = re.compile(
    r'^!\[(?P<alt>[^\]]*)\]\((?:(?P<angle><[^>]+>)|(?P<url>[^\s)]+))(?:\s+"(?P<title>[^"]*)")?\)\s*$'
)
SOURCE_RE = re.compile(
    r"^\s*[*_]{1,2}\s*(?:图源|图片来源|来源|Source)\s*[:：]\s*(?P<body>.+?)\s*[*_]{1,2}\s*$",
    re.IGNORECASE,
)
LINK_RE = re.compile(r"\[([^\]]+)\]\((?:<([^>]+)>|(https?://[^)\s]+))\)")
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class BuildError(RuntimeError):
    pass


@dataclass
class ReportImage:
    alt: str
    url: str
    title: str
    source_label: str
    source_url: str
    source_note: str
    line_no: int
    source_line_no: int
    body_line_index: int = 0
    body_source_index: int = 0
    local_path: str | None = None
    content_type: str = ""
    byte_size: int = 0
    sha256: str = ""
    width: int = 0
    height: int = 0
    warning: str = ""
    placeholder: bool = False

    @property
    def caption(self) -> str:
        return self.title or self.alt or "图片"


@dataclass
class BuildContext:
    md_path: Path
    report_dir: Path
    meta: dict[str, Any]
    body: str
    images: list[ReportImage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_front_matter(text: str) -> tuple[dict[str, Any], str, int]:
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text, 0
    raw = m.group(1)
    if yaml is not None:
        try:
            meta = yaml.safe_load(raw) or {}
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"invalid YAML front matter: {exc}") from exc
    else:
        meta = {}
        for line in raw.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip('"').strip("'")
    if not isinstance(meta, dict):
        raise BuildError("front matter must be a YAML mapping (key: value), not a list or scalar")
    line_offset = text[: m.end()].count("\n")
    return meta, text[m.end() :], line_offset


def strip_first_h1(body: str) -> tuple[str, str | None, int]:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[i + 1 :]), line[2:].strip(), i + 1
        if line.strip():
            break
    return body, None, 0


USER_IMAGE_LABELS = ("用户提供", "用户拍摄", "作者拍摄", "自摄", "作者自绘", "作者绘制", "User-provided", "Author photo")


def parse_source_line(line: str, line_no: int) -> tuple[str, str, str]:
    m = SOURCE_RE.match(line.strip())
    require(
        m is not None,
        f"line {line_no}: expected a source line like `*图源：[页面](https://...) · 许可*` "
        "or `*图源：用户提供 · 拍摄者/时间/地点*`",
    )
    assert m is not None
    body = m.group("body").strip()
    links = LINK_RE.findall(body)
    if links:
        label, angle_url, plain_url = links[0]
        url = angle_url or plain_url
        note = LINK_RE.sub("", body).strip(" ·-—|,，;；")
        return label.strip(), url.strip(), note

    for prefix in USER_IMAGE_LABELS:
        if body.startswith(prefix):
            note = body[len(prefix) :].strip(" ·-—|,，;；")
            require(
                bool(note),
                f"line {line_no}: user-provided image needs a note after `{prefix}`: "
                "who took it, when, and where (or say 拍摄时间不详).",
            )
            return prefix, "", note

    raise BuildError(
        f"line {line_no}: image source must be an http(s) page link, or start with one of "
        f"{' / '.join(USER_IMAGE_LABELS)} and include who/when/where. "
        "Example: `*图源：用户提供 · 2026-10-28 摄于某地现场*`"
    )


def parse_images(body: str, line_offset: int = 0) -> tuple[list[ReportImage], dict[int, ReportImage]]:
    lines = body.splitlines()
    images: list[ReportImage] = []
    image_by_line: dict[int, ReportImage] = {}
    in_fence = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = IMAGE_RE.match(stripped)
        if not m:
            continue
        image_url = m.group("angle") or m.group("url")
        image_url = image_url.strip("<>")
        j = i + 1
        # Allow one blank line between the image and its source line.
        while j < len(lines) and not lines[j].strip() and j <= i + 1:
            j += 1
        require(
            j < len(lines),
            f"line {i + 1 + line_offset}: image `{m.group('url')}` has no source line directly below it",
        )
        label, source_url, note = parse_source_line(lines[j], j + 1 + line_offset)
        image = ReportImage(
            alt=m.group("alt").strip(),
            url=image_url,
            title=(m.group("title") or "").strip(),
            source_label=label,
            source_url=source_url,
            source_note=note,
            line_no=i + 1 + line_offset,
            source_line_no=j + 1 + line_offset,
            body_line_index=i,
            body_source_index=j,
        )
        images.append(image)
        image_by_line[i] = image
    return images, image_by_line


def find_unmanaged_images(body: str, managed_lines: set[int], line_offset: int = 0) -> list[int]:
    """Return 1-based body line numbers that look like images but were not parsed.

    A managed image must be a standalone line matching IMAGE_RE and be followed
    by a source line. Anything else is a hotlink that would bypass provenance
    checks, so the build fails instead of silently rendering it.
    """
    bad: list[int] = []
    in_fence = False
    for i, line in enumerate(body.splitlines()):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "![" in line and i not in managed_lines:
            bad.append(i + 1 + line_offset)
    return bad


def looks_like_image(data: bytes) -> bool:
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return True
    if data.startswith(b"BM"):
        return True
    head = data[:8192].lstrip()
    return head.startswith(b"<svg") or b"<svg" in head


def strip_fenced_code(text: str) -> str:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return "\n".join(lines)


def image_extension(content_type: str, url: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
        "image/bmp": ".bmp",
    }
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in mapping:
        return mapping[ctype]
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in set(mapping.values()) else ".img"


def fetch_url(url: str, timeout: int = 30) -> tuple[bytes, str]:
    if requests is not None:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            stream=True,
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise BuildError(f"image is larger than {MAX_IMAGE_BYTES // 1024 // 1024} MB: {url}")
            chunks.append(chunk)
        return b"".join(chunks), ctype

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        ctype = resp.headers.get_content_type()
        data = resp.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise BuildError(f"image is larger than {MAX_IMAGE_BYTES // 1024 // 1024} MB: {url}")
        return data, ctype


def placeholder_svg(image: ReportImage, reason: str) -> bytes:
    text = html_lib.escape(f"图片未能下载：{reason}")
    alt = html_lib.escape(image.alt or image.caption)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675">'
        '<rect width="1200" height="675" fill="#f3f0e8"/>'
        '<rect x="24" y="24" width="1152" height="627" fill="none" stroke="#c9c1b0" stroke-width="2" stroke-dasharray="10 8"/>'
        f'<text x="600" y="300" text-anchor="middle" font-family="Noto Sans CJK SC, sans-serif" font-size="34" fill="#5d6b76">{alt}</text>'
        f'<text x="600" y="360" text-anchor="middle" font-family="Noto Sans CJK SC, sans-serif" font-size="24" fill="#9a6a32">{text}</text>'
        '<text x="600" y="420" text-anchor="middle" font-family="Noto Sans CJK SC, sans-serif" font-size="20" fill="#7a7468">请修复图片地址后重新 build</text>'
        "</svg>"
    ).encode("utf-8")


def download_images(
    images: list[ReportImage],
    report_dir: Path,
    allow_missing: bool,
) -> list[str]:
    warnings: list[str] = []
    img_dir = report_dir / "assets" / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    for image in images:
        key = sha256_bytes(image.url.encode("utf-8"))[:16]
        try:
            parsed = urllib.parse.urlparse(image.url)
            if parsed.scheme in ("http", "https"):
                data, ctype = fetch_url(image.url)
            else:
                if parsed.scheme:
                    raise BuildError(f"unsupported image URL scheme: {parsed.scheme}")
                local = (report_dir / image.url).resolve()
                if not local.is_relative_to(report_dir.resolve()):
                    raise BuildError(
                        f"local image must stay inside the report directory: {image.url}"
                    )
                if not local.exists():
                    raise BuildError(f"local image not found: {image.url}")
                if local.stat().st_size > MAX_IMAGE_BYTES:
                    raise BuildError(
                        f"local image is larger than {MAX_IMAGE_BYTES // 1024 // 1024} MB: {image.url}"
                    )
                data = local.read_bytes()
                ctype = mimetypes.guess_type(local.name)[0] or ""
            if not looks_like_image(data):
                raise BuildError(
                    f"file does not look like a supported image (JPEG/PNG/GIF/WebP/AVIF/BMP/SVG): {image.url}"
                )
            try:
                from PIL import Image

                with Image.open(io.BytesIO(data)) as im:
                    image.width, image.height = im.size
            except Exception:  # noqa: BLE001
                match = re.search(rb'<svg[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"', data[:4096])
                if match:
                    image.width, image.height = int(match.group(1)), int(match.group(2))
            ext = image_extension(ctype, image.url)
            target = img_dir / f"{key}{ext}"
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            image.local_path = str(target.relative_to(report_dir)).replace("\\", "/")
            image.content_type = ctype
            image.byte_size = len(data)
            image.sha256 = sha256_bytes(data)
        except Exception as exc:  # noqa: BLE001
            cached = sorted(img_dir.glob(f"{key}.*"))
            if cached:
                target = cached[0]
                image.local_path = str(target.relative_to(report_dir)).replace("\\", "/")
                image.content_type = mimetypes.guess_type(target.name)[0] or ""
                image.byte_size = target.stat().st_size
                image.sha256 = sha256_file(target)
                image.warning = f"reused cached image after fetch failure: {exc}"
                warnings.append(f"image line {image.line_no}: reused cached copy ({exc})")
                continue
            if not allow_missing:
                raise BuildError(
                    f"could not download image at line {image.line_no}: {image.url}\n"
                    f"  {exc}\n"
                    "  Fix the URL, or use --allow-missing-images for a draft build. "
                    "A final build must not contain missing images."
                ) from exc
            ext = ".svg"
            target = img_dir / f"{key}{ext}"
            target.write_bytes(placeholder_svg(image, str(exc)))
            image.local_path = str(target.relative_to(report_dir)).replace("\\", "/")
            image.content_type = "image/svg+xml"
            image.byte_size = target.stat().st_size
            image.sha256 = sha256_file(target)
            image.warning = str(exc)
            image.placeholder = True
            warnings.append(f"image line {image.line_no} missing: {image.url}")
    return warnings


def figure_html(image: ReportImage, idx: int) -> str:
    src = image.local_path or image.url
    caption_text = image.caption
    if not re.match(rf"^图\s*{idx}\s*[:：·]", caption_text):
        caption_text = f"图 {idx} · {caption_text}"
    caption = html_lib.escape(caption_text)
    source_label = html_lib.escape(image.source_label)
    source_url = html_lib.escape(image.source_url, quote=True)
    note = html_lib.escape(image.source_note)
    note_html = f'<span class="fig-note">{note}</span>' if note else ""
    if image.source_url:
        source_html = f'<a href="{source_url}" target="_blank" rel="noopener">{source_label}</a>'
    else:
        source_html = source_label
    wide = " wide-figure" if image.width and image.height and image.width / image.height >= 1.4 else ""
    return (
        '<figure class="report-figure%s" id="fig-%d" data-source-url="%s">\n'
        '  <img src="%s" alt="%s">\n'
        '  <figcaption>\n'
        '    <span class="fig-caption">%s</span>\n'
        '    <span class="fig-source">图源：%s</span>%s\n'
        "  </figcaption>\n"
        "</figure>"
        % (
            wide,
            idx,
            source_url,
            html_lib.escape(src, quote=True),
            html_lib.escape(image.alt, quote=True),
            caption,
            source_html,
            note_html,
        )
    )


def inject_figures(
    body: str,
    images: list[ReportImage],
    image_by_line: dict[int, ReportImage],
    report_dir: Path,
) -> tuple[str, dict[int, str]]:
    lines = body.splitlines()
    source_line_numbers = {image.body_source_index for image in images}
    index_by_id = {id(image): i + 1 for i, image in enumerate(images)}
    figures: dict[int, str] = {}
    out: list[str] = []
    for i, line in enumerate(lines):
        if i in source_line_numbers:
            continue
        image = image_by_line.get(i)
        if image is not None:
            idx = index_by_id[id(image)]
            figures[idx] = figure_html(image, idx)
            out.append(f"HISTORYRESEARCHFIGURE{idx}")
            continue
        out.append(line)
    return "\n".join(out), figures


def replace_figure_placeholders(content: str, figures: dict[int, str]) -> str:
    for idx, figure in figures.items():
        token = f"HISTORYRESEARCHFIGURE{idx}"
        content = re.sub(rf"<p>\s*{token}\s*</p>", figure, content)
        content = content.replace(token, figure)
    return content


def render_markdown(body: str) -> tuple[str, list[dict[str, Any]]]:
    require(MarkdownIt is not None, "missing dependency: markdown-it-py (pip install -r requirements.txt)")
    require(BeautifulSoup is not None, "missing dependency: beautifulsoup4 (pip install -r requirements.txt)")
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    md = MarkdownIt("gfm-like", {"html": False, "linkify": False, "typographer": True})
    rendered = md.render(body)
    soup = BeautifulSoup(rendered, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("http://", "https://")):
            a["target"] = "_blank"
            a["rel"] = "noopener"
    for table in soup.find_all("table"):
        classes = table.get("class", [])
        if isinstance(classes, str):
            classes = [classes]
        if "data-table" not in classes:
            classes.append("data-table")
        table["class"] = classes
    toc: list[dict[str, Any]] = []
    for level in (2, 3):
        for h in soup.find_all(f"h{level}"):
            text = h.get_text(" ", strip=True)
            if not text:
                continue
            hid = f"sec-{len(toc) + 1}"
            h["id"] = hid
            toc.append({"level": level, "text": text, "id": hid})
    return str(soup), toc


def find_stray_images(html_fragment: str) -> list[str]:
    require(BeautifulSoup is not None, "missing dependency: beautifulsoup4")
    soup = BeautifulSoup(html_fragment, "html.parser")
    stray: list[str] = []
    for img in soup.find_all("img"):
        if img.find_parent("figure", class_="report-figure") is None:
            stray.append(img.get("src", "<no src>"))
    return stray


def toc_html(toc: list[dict[str, Any]]) -> str:
    if not toc:
        return ""
    parts = ['<nav class="toc" aria-label="目录">', "<h2>目录</h2>", '<ol class="toc-list">']
    for item in toc:
        cls = "toc-h2" if item["level"] == 2 else "toc-h3"
        parts.append(f'<li class="{cls}"><a href="#{item["id"]}">{html_lib.escape(item["text"])}</a></li>')
    parts.append('<li class="toc-h2"><a href="#image-credits">图片出处</a></li>')
    parts.extend(["</ol>", "</nav>"])
    return "\n".join(parts)


def image_credits_html(images: list[ReportImage]) -> str:
    if not images:
        return '<p class="muted">本报告未使用图片。</p>'
    rows = [
        "<table class=\"data-table credits-table\">",
        "<thead><tr><th>图</th><th>说明</th><th>图片地址</th><th>来源页面</th><th>备注</th></tr></thead>",
        "<tbody>",
    ]
    for i, image in enumerate(images, 1):
        if image.url.startswith(("http://", "https://")):
            image_cell = (
                f'<a href="{html_lib.escape(image.url, quote=True)}" '
                'target="_blank" rel="noopener">原图</a>'
            )
        else:
            image_cell = f"<code>{html_lib.escape(image.local_path or image.url)}</code>"
        if image.source_url:
            source_cell = (
                f'<a href="{html_lib.escape(image.source_url, quote=True)}" '
                f'target="_blank" rel="noopener">{html_lib.escape(image.source_label)}</a>'
            )
        else:
            source_cell = html_lib.escape(image.source_label)
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{html_lib.escape(image.caption)}</td>"
            f"<td>{image_cell}</td>"
            f"<td>{source_cell}</td>"
            f"<td>{html_lib.escape(image.source_note)}</td>"
            "</tr>"
        )
    rows.extend(["</tbody>", "</table>"])
    return "\n".join(rows)


def build_html_document(
    ctx: BuildContext,
    content_html: str,
    toc: list[dict[str, Any]],
    md_hash: str,
) -> str:
    require(Template is not None, "missing dependency: jinja2 (pip install -r requirements.txt)")
    template_text = (TEMPLATES / "report.html.j2").read_text(encoding="utf-8")
    css = (TEMPLATES / "style.css").read_text(encoding="utf-8")
    template = Template(template_text, autoescape=True)
    meta = ctx.meta
    title = meta.get("title") or ctx.md_path.stem
    subtitle = meta.get("subtitle", "")
    date = meta.get("date", "")
    scope = meta.get("scope", "")
    sources_cutoff = meta.get("sources_cutoff", date)
    author = meta.get("author", "")
    lang = meta.get("lang", "zh-CN")
    description = meta.get("description", subtitle or title)
    return template.render(
        lang=lang,
        title=title,
        subtitle=subtitle,
        date=date,
        scope=scope,
        sources_cutoff=sources_cutoff,
        author=author,
        description=description,
        css=css,
        content=content_html,
        toc=toc_html(toc),
        image_credits=image_credits_html(ctx.images),
        image_count=len(ctx.images),
        md_hash=md_hash,
        build_stamp=md_hash[:12],
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


def write_pdf(html_path: Path, pdf_path: Path, title: str, md_hash: str, engine: str) -> str:
    errors: list[str] = []
    if engine in ("auto", "playwright"):
        try:
            from playwright.sync_api import sync_playwright

            footer = (
                '<div style="width:100%;font-size:8px;color:#8a857a;text-align:center;'
                'font-family:sans-serif;padding:0 12mm;">'
                f"{html_lib.escape(title)} · 第 <span class=\"pageNumber\"></span> / "
                '<span class="totalPages"></span> 页 · '
                f"源文件 {md_hash[:12]}</div>"
            )
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page()

                    def _local_only(route: Any) -> None:
                        url = route.request.url
                        if url.startswith(("file://", "data:")):
                            route.continue_()
                        else:
                            route.abort()

                    page.route("**/*", _local_only)
                    page.goto(html_path.as_uri(), wait_until="load", timeout=60000)
                    page.emulate_media(media="print")
                    page.evaluate("document.fonts.ready")
                    page.pdf(
                        path=str(pdf_path),
                        format="A4",
                        print_background=True,
                        display_header_footer=True,
                        header_template="<div></div>",
                        footer_template=footer,
                        margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
                    )
                finally:
                    browser.close()
            return "playwright"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"playwright: {exc}")
            if engine == "playwright":
                raise BuildError("Playwright PDF failed:\n" + "\n".join(errors)) from exc

    if engine in ("auto", "chrome"):
        found_chrome = False
        for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
            exe = shutil.which(name)
            if not exe:
                continue
            found_chrome = True
            try:
                subprocess.run(
                    [
                        exe,
                        "--headless",
                        "--disable-gpu",
                        "--no-sandbox",
                        f"--print-to-pdf={pdf_path}",
                        html_path.as_uri(),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=180,
                )
                return f"chrome:{name}"
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
        if not found_chrome:
            errors.append("no chromium/chrome binary found in PATH")
        if engine == "chrome":
            raise BuildError("Chrome PDF failed:\n" + "\n".join(errors))

    if engine in ("auto", "weasyprint"):
        try:
            import weasyprint  # type: ignore

            weasyprint.HTML(filename=str(html_path)).write_pdf(str(pdf_path))
            return "weasyprint"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"weasyprint: {exc}")
            if engine == "weasyprint":
                raise BuildError("WeasyPrint PDF failed:\n" + "\n".join(errors)) from exc

    raise BuildError(
        "no PDF engine available. Install Playwright Chromium:\n"
        "  python3 -m pip install playwright\n"
        "  python3 -m playwright install chromium\n"
        "or install Chrome/Chromium/WeasyPrint. Details:\n" + "\n".join(errors)
    )


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.dir).resolve()
    if dest.exists() and any(dest.iterdir()) and not args.force:
        die(f"{dest} is not empty; pass --force to overwrite the scaffold")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "assets" / "img").mkdir(parents=True, exist_ok=True)
    (dest / "assets" / "img" / ".gitkeep").touch()
    (dest / ".build").mkdir(exist_ok=True)
    (dest / ".gitignore").write_text(".build/\n", encoding="utf-8")

    title = args.title
    subtitle = args.subtitle or "一份有出处的历史长文"
    scope = args.scope or "时间范围、地理范围、核心问题"
    author = args.author or ""
    date = args.date or datetime.now().strftime("%Y-%m-%d")
    template = (TEMPLATES / "report.md").read_text(encoding="utf-8")
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    report = (
        template.replace("{{TITLE}}", quote(title))
        .replace("{{SUBTITLE}}", quote(subtitle))
        .replace("{{SCOPE}}", quote(scope))
        .replace("{{AUTHOR}}", quote(author))
        .replace("{{DATE}}", quote(date))
    )
    (dest / "report.md").write_text(report, encoding="utf-8")
    (dest / "sources.tsv").write_text(
        "claim\tsource_url\tsource_title\tquote\tconfidence\tnotes\n",
        encoding="utf-8",
    )
    review_rows = "\n".join(
        f"| 1 |  | {label} |  |  |  |  |  |" for _, label, _ in REVIEW_ANGLES
    )
    (dest / "review.md").write_text(
        "# 审阅记录\n\n"
        "| 轮次 | 审阅 agent | 角度 | 覆盖章节 | 结论 | P0 | P1 | 备注 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        f"{review_rows}\n",
        encoding="utf-8",
    )
    print(f"initialized {dest}")
    print("next: fill report.md and sources.tsv, then run:")
    print(f"  python3 {Path(__file__).resolve()} review-kit {dest / 'report.md'}")
    print(f"  python3 {Path(__file__).resolve()} build {dest / 'report.md'}")
    return 0


def load_context(md_path: Path) -> BuildContext:
    require(md_path.exists(), f"report not found: {md_path}")
    try:
        text = md_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BuildError(f"report is not valid UTF-8: {md_path}") from exc
    except OSError as exc:
        raise BuildError(f"could not read report: {md_path}: {exc}") from exc
    meta, body, front_offset = read_front_matter(text)
    body, h1, removed_lines = strip_first_h1(body)
    line_offset = front_offset + removed_lines
    if "title" not in meta and h1:
        meta["title"] = h1
    ctx = BuildContext(md_path=md_path, report_dir=md_path.parent, meta=meta, body=body)
    images, image_by_line = parse_images(body, line_offset)
    unmanaged = find_unmanaged_images(body, set(image_by_line), line_offset)
    if unmanaged:
        lines = ", ".join(str(n) for n in unmanaged[:8])
        raise BuildError(
            "unmanaged image syntax at report body line(s) "
            f"{lines}. Every image must be a standalone line followed by a source line; "
            "if the URL contains parentheses, wrap it as `![alt](<https://.../x_(1).png>)`."
        )
    ctx.images = images
    # Stash the line map for inject_figures without widening the dataclass.
    ctx.__dict__["_image_by_line"] = image_by_line
    return ctx


def cmd_build(args: argparse.Namespace) -> int:
    md_path = Path(args.report).resolve()
    ctx = load_context(md_path)
    image_by_line: dict[int, ReportImage] = ctx.__dict__["_image_by_line"]
    if not ctx.images:
        ctx.warnings.append("no images found; a long history report should use at least 3 sourced images")
    if not (ctx.report_dir / "sources.tsv").exists():
        ctx.warnings.append("sources.tsv missing; the evidence ledger should accompany the report")
    warnings = download_images(ctx.images, ctx.report_dir, args.allow_missing_images)
    ctx.warnings.extend(warnings)

    body_with_figures, figures = inject_figures(ctx.body, ctx.images, image_by_line, ctx.report_dir)
    content_html, toc = render_markdown(body_with_figures)
    content_html = replace_figure_placeholders(content_html, figures)
    stray = find_stray_images(content_html)
    if stray:
        raise BuildError(
            "rendered HTML contains unmanaged <img> element(s): "
            + ", ".join(stray[:5])
            + ". Use the managed image syntax with a source line."
        )
    md_hash = sha256_file(md_path)
    html_doc = build_html_document(ctx, content_html, toc, md_hash)

    html_path = ctx.report_dir / "report.html"
    pdf_path = ctx.report_dir / "report.pdf"
    html_path.write_text(html_doc, encoding="utf-8")
    engine = write_pdf(html_path, pdf_path, str(ctx.meta.get("title") or md_path.stem), md_hash, args.pdf_engine)

    manifest = {
        "version": 1,
        "md": md_path.name,
        "md_sha256": md_hash,
        "html": html_path.name,
        "html_sha256": sha256_file(html_path),
        "pdf": pdf_path.name,
        "pdf_sha256": sha256_file(pdf_path),
        "pdf_engine": engine,
        "images": [
            {
                "line": image.line_no,
                "caption": image.caption,
                "image_url": image.url,
                "source_label": image.source_label,
                "source_url": image.source_url,
                "source_note": image.source_note,
                "local_path": image.local_path,
                "content_type": image.content_type,
                "bytes": image.byte_size,
                "sha256": image.sha256,
                "width": image.width,
                "height": image.height,
                "warning": image.warning,
                "placeholder": image.placeholder,
            }
            for image in ctx.images
        ],
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (ctx.report_dir / "report.build.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"built {html_path}")
    print(f"built {pdf_path} ({engine})")
    print(f"  md   sha256 {md_hash[:16]}…")
    print(f"  html sha256 {manifest['html_sha256'][:16]}…")
    print(f"  pdf  sha256 {manifest['pdf_sha256'][:16]}…")
    print(f"  images {len(ctx.images)}")
    for warning in ctx.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def section_link_warnings(body: str) -> list[str]:
    warnings: list[str] = []
    body = strip_fenced_code(body)
    body = "\n".join(
        line
        for line in body.splitlines()
        if not SOURCE_RE.match(line.strip()) and not IMAGE_RE.match(line.strip())
    )
    matches = list(SECTION_RE.finditer(body))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        title = match.group(1).strip()
        section = body[start:end]
        if title in ("参考资料", "图片出处", "参考", "资料来源"):
            continue
        if len(section.strip()) < 200:
            continue
        if not re.search(r"\[[^\]]+\]\(https?://", section):
            warnings.append(f'section "{title}" has no inline source link')
    return warnings


def normalize_for_quote(text: str) -> str:
    return re.sub(r"[\s　“”‘’·,，。；;：:、—…\.\-\(\)（）\[\]【】]+", "", text)


def fetch_page_text(url: str, timeout: int = 20) -> str | None:
    try:
        if requests is not None:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").lower()
            if "html" not in ctype and "text" not in ctype:
                return None
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding
            html_text = resp.text
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                ctype = resp.headers.get_content_type()
                if "html" not in ctype and "text" not in ctype:
                    return None
                html_text = resp.read().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    except Exception:  # noqa: BLE001
        return None


def url_status(url: str, timeout: int = 12) -> tuple[int | str, str]:
    try:
        if requests is not None:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
                stream=True,
                allow_redirects=True,
            )
            return resp.status_code, resp.url
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return getattr(resp, "status", 200), resp.geturl()
    except Exception as exc:  # noqa: BLE001
        return "ERR", str(exc)


def extract_review_metadata(text: str) -> dict[str, Any] | None:
    for match in re.finditer(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL):
        try:
            data = json.loads(match.group(1))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and "angle" in data:
            return data
    return None


def review_gate_errors(report_dir: Path, sections: list[str], md_hash: str) -> list[str]:
    errors: list[str] = []
    review_dir = report_dir / "review"
    prompts = sorted(review_dir.glob("*.prompt.md"))
    if len(prompts) < 7:
        errors.append(
            f"review/ has {len(prompts)} prompt files; run review-kit and keep all 7 angles"
        )
    sources_path = report_dir / "sources.tsv"
    sources_hash = sha256_file(sources_path) if sources_path.exists() else ""
    expected = {path.name.removesuffix(".prompt.md") for path in prompts}
    out_dir = review_dir / "out"
    outputs = {path.name.removesuffix(".out.md"): path for path in out_dir.glob("*.out.md")}
    for key in sorted(expected):
        path = outputs.get(key)
        if path is None or path.stat().st_size == 0:
            errors.append(f"review/out/{key}.out.md missing or empty; run the {key} reviewer")
            continue
        text = path.read_text(encoding="utf-8")
        if not ("Verdict" in text or "结论" in text):
            errors.append(f"review/out/{key}.out.md has no Verdict/结论 section")
        if not ("Coverage" in text or "覆盖" in text):
            errors.append(f"review/out/{key}.out.md has no Coverage/覆盖 section")
        meta = extract_review_metadata(text)
        if meta is None:
            errors.append(f"review/out/{key}.out.md has no machine-readable review JSON block")
            continue
        if meta.get("angle") != key:
            errors.append(f"review/out/{key}.out.md metadata angle is {meta.get('angle')!r}, expected {key!r}")
        if meta.get("md_sha256") != md_hash:
            errors.append(
                f"review/out/{key}.out.md reviewed a different report.md version "
                f"({meta.get('md_sha256')!r}); rebuild and re-review"
            )
        expected_review_id = review_manifest_id(md_hash, sources_hash)
        review_id = meta.get("review_id")
        if review_id and review_id != expected_review_id:
            errors.append(
                f"review/out/{key}.out.md review_id does not match the current report + sources"
            )
        if meta.get("sources_sha256") != sources_hash:
            errors.append(
                f"review/out/{key}.out.md reviewed a different sources.tsv version; re-review"
            )
        if meta.get("verdict") not in ("PASS", "NEEDS WORK"):
            errors.append(
                f"review/out/{key}.out.md verdict must be PASS or NEEDS WORK, got {meta.get('verdict')!r}"
            )
        try:
            p0 = int(meta.get("p0", 0))
            p1 = int(meta.get("p1", 0))
        except (TypeError, ValueError):
            errors.append(f"review/out/{key}.out.md p0/p1 must be integers")
            p0 = p1 = 1
        if p0 or p1:
            errors.append(f"review/out/{key}.out.md reports {p0} P0 and {p1} P1 issue(s)")
        coverage = meta.get("coverage") or []
        if not isinstance(coverage, list):
            errors.append(f"review/out/{key}.out.md coverage must be a list")
        else:
            for section in sections:
                if section not in coverage:
                    errors.append(f"review/out/{key}.out.md did not cover section {section!r}")

    coverage_path = review_dir / "coverage.md"
    if not coverage_path.exists():
        errors.append("review/coverage.md missing; run review-kit")
    else:
        coverage = coverage_path.read_text(encoding="utf-8")
        for section in sections:
            if section not in coverage:
                errors.append(f'coverage.md does not include section "{section}"')
        if "☐" in coverage:
            errors.append("coverage.md still has unchecked cells (☐); every section needs every angle")
        if "☑" not in coverage:
            errors.append("coverage.md has no checked cells (☑); the review is not recorded")

    review_md = report_dir / "review.md"
    if not review_md.exists():
        errors.append("review.md missing; record each review round and its P0/P1 counts")
    else:
        rows = []
        for line in review_md.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 8:
                continue
            if cells[0] in ("轮次", "") or set(cells[0]) <= set("-: "):
                continue
            if all(not cell for cell in cells[1:]):
                continue
            rows.append(cells)
        if len(rows) < 7:
            errors.append(
                f"review.md has {len(rows)} filled review rows; record all 7 angles (one per row)"
            )
        complete = sum(1 for row in rows if row[1] and row[2] and row[4])
        if complete < 7:
            errors.append(
                "review.md needs reviewer, angle, and conclusion in columns 2/3/5 for each of the 7 rows"
            )

    return errors


def cmd_check(args: argparse.Namespace) -> int:
    md_path = Path(args.report).resolve()
    ctx = load_context(md_path)
    report_dir = ctx.report_dir
    manifest_path = report_dir / "report.build.json"
    html_path = report_dir / "report.html"
    pdf_path = report_dir / "report.pdf"
    errors: list[str] = []
    warnings: list[str] = []

    if not manifest_path.exists():
        errors.append("report.build.json missing; run build first")
    if not html_path.exists():
        errors.append("report.html missing; run build first")
    if not pdf_path.exists():
        errors.append("report.pdf missing; run build first")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise BuildError(f"report.build.json is not valid JSON: {exc}") from exc
    manifest_images = {
        (item.get("line"), item.get("image_url")): item
        for item in manifest.get("images", [])
    }
    for image in ctx.images:
        built = manifest_images.get((image.line_no, image.url))
        if built:
            image.local_path = built.get("local_path")
            image.content_type = built.get("content_type", "")
            image.byte_size = int(built.get("bytes") or 0)
            image.sha256 = built.get("sha256", "")
            image.warning = built.get("warning", "")
            image.placeholder = bool(built.get("placeholder"))
    md_hash = sha256_file(md_path)
    if manifest.get("md_sha256") != md_hash:
        errors.append("report.md changed after the last build; rebuild to sync HTML/PDF")
    if manifest.get("html_sha256") != sha256_file(html_path):
        errors.append("report.html was modified or is stale; rebuild from report.md")
    if manifest.get("pdf_sha256") != sha256_file(pdf_path):
        errors.append("report.pdf was modified or is stale; rebuild from report.md")
    if pdf_path.stat().st_size < 20_000:
        errors.append("report.pdf is suspiciously small (<20 KB); check the PDF engine")
    if manifest.get("pdf_engine") not in (None, "playwright"):
        print(
            f"note: PDF engine is {manifest.get('pdf_engine')}; the per-page footer and source hash "
            "are only guaranteed with Playwright",
            file=sys.stderr,
        )

    raster_images = [image for image in ctx.images if "svg" not in image.content_type.lower()]
    if raster_images:
        try:
            import fitz  # type: ignore

            pdf_doc = fitz.open(pdf_path)
            embedded = sum(len(page.get_images(full=True)) for page in pdf_doc)
            if embedded == 0:
                warnings.append("PDF contains no embedded raster images; check figure rendering")
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not inspect PDF images: {exc}")

    require(BeautifulSoup is not None, "missing dependency: beautifulsoup4")
    html_text = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_text, "html.parser")
    stray = find_stray_images(html_text)
    if stray:
        errors.append("HTML contains unmanaged <img> element(s): " + ", ".join(stray[:5]))
    figures = soup.select("figure.report-figure")
    if len(figures) != len(ctx.images):
        errors.append(f"HTML has {len(figures)} figures but Markdown has {len(ctx.images)} images")
    for i, image in enumerate(ctx.images, 1):
        if image.source_url:
            if not image.source_url.startswith(("http://", "https://")):
                errors.append(f"image {i} source is not an http(s) URL: {image.source_url}")
            if image.source_url == image.url:
                warnings.append(f"image {i} source page is the image file itself; link the original page instead")
        else:
            if image.source_label not in USER_IMAGE_LABELS:
                errors.append(
                    f"image {i} has no http(s) source and is not labeled as user-provided "
                    f"({image.source_label!r})"
                )
            if not image.source_note:
                errors.append(f"image {i} is user-provided but has no who/when/where note")
        if not image.source_note:
            warnings.append(f"image {i} has no author/license note after the source link")
        if not image.local_path:
            errors.append(f"image {i} has no local build copy; rebuild")
        elif not (report_dir / image.local_path).exists():
            errors.append(f"image {i} local copy missing: {image.local_path}")
        elif image.sha256 and sha256_file(report_dir / image.local_path) != image.sha256:
            errors.append(f"image {i} local copy does not match the build manifest: {image.local_path}")
        if image.placeholder:
            errors.append(f"image {i} is a placeholder: {image.warning}")
        elif image.warning:
            warnings.append(f"image {i}: {image.warning}")
        anchor = soup.find("figure", attrs={"id": f"fig-{i}"})
        if anchor is None:
            errors.append(f"figure {i} missing from HTML")
        else:
            if anchor.get("data-source-url") != image.source_url:
                errors.append(f"figure {i} source URL mismatch in HTML")
            src = anchor.find("img", src=True)
            if src is None or not src.get("src"):
                errors.append(f"figure {i} has no <img src>")
            elif image.local_path and src.get("src") != image.local_path:
                errors.append(
                    f"figure {i} src is {src.get('src')!r}, expected the local copy {image.local_path!r}"
                )
        credits = soup.select_one(".image-credits")
        if credits is not None:
            credit_urls = {
                html_lib.unescape(a.get("href", ""))
                for a in credits.select("a[href]")
            }
            if image.source_url:
                if image.source_url not in credit_urls:
                    errors.append(f"image {i} source URL missing from image credits appendix")
            elif image.source_label not in credits.get_text(" ", strip=True):
                errors.append(f"image {i} user-provided label missing from image credits appendix")

    by_sha: dict[str, list[ReportImage]] = {}
    for image in ctx.images:
        if image.sha256:
            by_sha.setdefault(image.sha256, []).append(image)
    for sha, group in by_sha.items():
        if len(group) > 1 and len({image.source_url for image in group}) > 1:
            warnings.append(
                "identical image bytes are credited to different source pages: "
                + ", ".join(image.source_url for image in group)
            )

    if not ctx.images:
        warnings.append("no images; a long history report should use at least 3 sourced images")
    else:
        plain_len = len(re.sub(r"\s+", "", strip_fenced_code(ctx.body)))
        if plain_len >= 3000 and len(ctx.images) < 3:
            warnings.append(
                f"long report ({plain_len} chars) has only {len(ctx.images)} image(s); "
                "standard long-form needs at least 3 sourced images"
            )

    warnings.extend(section_link_warnings(ctx.body))

    if args.require_review:
        sections = [m.group(1).strip() for m in SECTION_RE.finditer(strip_fenced_code(ctx.body))]
        errors.extend(review_gate_errors(report_dir, sections, md_hash))

    sources_path = report_dir / "sources.tsv"
    source_urls: list[str] = []
    source_rows: list[tuple[int, str, str]] = []
    if sources_path.exists():
        with sources_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for line_no, row in enumerate(reader, 2):
                url = (row.get("source_url") or "").strip()
                if not url:
                    continue
                if not url.startswith(("http://", "https://")):
                    errors.append(f"sources.tsv line {line_no}: non-http(s) source_url: {url}")
                if not (row.get("claim") or "").strip():
                    warnings.append(f"sources.tsv line {line_no}: empty claim")
                if not (row.get("quote") or "").strip():
                    warnings.append(f"sources.tsv line {line_no}: empty quote")
                confidence = (row.get("confidence") or "").strip().lower()
                if confidence not in ("high", "medium", "low"):
                    warnings.append(
                        f"sources.tsv line {line_no}: confidence should be high/medium/low, got {confidence!r}"
                    )
                source_urls.append(url)
                source_rows.append((line_no, url, (row.get("quote") or "").strip()))
        if not source_urls:
            warnings.append("sources.tsv has no source rows")
    else:
        warnings.append("sources.tsv missing; the evidence ledger should accompany the report")

    if args.online:
        candidates = source_urls + [image.source_url for image in ctx.images] + [image.url for image in ctx.images]
        urls = sorted({url for url in candidates if url.startswith(("http://", "https://"))})
        for url in urls:
            status, final = url_status(url)
            if status == "ERR":
                errors.append(f"online check failed: {url} — {final}")
            elif isinstance(status, int) and status >= 400:
                warnings.append(f"online check HTTP {status}: {url} -> {final}")
            else:
                print(f"online {status}: {url}")

        for line_no, url, quote in source_rows:
            if not quote:
                continue
            page_text = fetch_page_text(url)
            if page_text is None:
                continue
            normalized_page = normalize_for_quote(page_text)
            segments = [
                normalize_for_quote(segment)
                for segment in re.split(r"……|\.\.\.", quote)
            ]
            segments = [segment for segment in segments if len(segment) >= 8]
            if segments and not all(segment in normalized_page for segment in segments):
                warnings.append(
                    f"sources.tsv line {line_no}: quote segments not found on {url}; "
                    "check the quote or use a more specific source"
                )

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    if args.strict and warnings:
        print("check failed: --strict treats warnings as errors", file=sys.stderr)
        return 2
    external_links = soup.select('a[href^="http"]')
    link_verb = "checked online" if args.online else "present in HTML"
    print(
        "OK: md/html/pdf in sync; "
        f"{len(ctx.images)} images with provenance; "
        f"{len(external_links)} external links {link_verb}"
    )
    return 0


REVIEW_ANGLES = [
    (
        "01-facts",
        "事实与来源核验",
        """Check every factual claim, date, number, name, place, and quotation.
For each one, open the cited source when possible and verify that the source
actually says what the report says. Flag: citation mismatch, source laundering
(a weak source cited for a strong claim), missing citations, numbers that do
not add up, and claims that are true in general but wrong for this time/place.
Prefer primary sources (gazetteers, archives, official documents, academic
papers, contemporary newspapers). Treat encyclopedia/self-media as leads, not
proof.""",
    ),
    (
        "02-chronology",
        "时间线与因果",
        """Reconstruct the report's timeline independently. Check the order of
events, the causal links, and the boundaries between periods. Flag:
anachronisms, events placed under the wrong reign/year, cause claimed without
evidence, missing turning points, and a timeline that hides a discontinuity
(e.g. a new institution reusing an old name). Verify that "before/after" claims
are supported by dates, not by narrative convenience.""",
    ),
    (
        "03-context-contrarian",
        "地方细节与反方证据",
        """Read as a skeptical local historian or domain expert. Look for the
detail a local gazetteer would correct: administrative boundary changes,
village-name etymology, local legends presented as fact, a famous person's
residence that is disputed, a "historic site" that is a later reconstruction.
Search for competing accounts and the strongest counter-evidence. Flag
overgeneralization, center-periphery bias, and any place where the report
repeats a popular story without labeling it as a story.""",
    ),
    (
        "04-images-licensing",
        "图片、出处与许可",
        """Audit every image. For each: (1) does the image actually depict what
the caption says? (2) does the source page link work, and is it the original
page rather than a search result or hotlink? (3) is the author/license stated,
and is reuse plausible? (4) is the resolution adequate for print? (5) is the
caption specific (who/what/where/when) rather than decorative? For
user-provided photos (label 用户提供/作者拍摄/自摄), an external link is not
required; check that the credit says who took it, when, and where, and that it
is appropriate to publish. For external images, require the original page.
Flag images whose source is a generic homepage, whose license is unknown, or
whose caption overclaims. Also check the auto-generated image credits appendix.""",
    ),
    (
        "05-structure-readability",
        "结构与可读性",
        """Review the report as an editor. Check the opening (does it state the
question and the payoff?), section order, transitions, redundancy, jargon,
paragraph length, and the ending. Flag sections that read as a list of facts
without an argument, claims that arrive before their evidence, and any place
where the reader must hold too many names/dates at once. Suggest cuts, not
just additions. Verify that the "信史 / 争议 / 传说" distinction is visible
where it matters.""",
    ),
    (
        "06-render-qa",
        "HTML/PDF 渲染与交付",
        """Open report.html and report.pdf. Check: cover, table of contents,
heading hierarchy, Chinese fonts, line spacing, page breaks, figure placement,
image sharpness, table overflow, link targets, footer page numbers, and the
build hash. Verify that every image is visible in the PDF and that no figure is
split across pages. Check the HTML at a narrow width (mobile) and in print
media. Flag anything that looks broken in the delivered artifacts, not just in
the Markdown.""",
    ),
    (
        "07-red-team",
        "红队：努力推翻报告",
        """Your job is to refute the report's central claims, not to polish it.
Build the strongest case that the report is wrong, overstated, or built on a
myth. Look for: a legend repeated as history, a single source doing too much
work, a date that comes from a secondary retelling, a "first/only/largest"
claim without a primary source, a plan described as if implemented, and a
citation that points to a page that does not contain the quote. If you cannot
refute a claim, say what evidence would be needed. Default to skepticism.""",
    ),
]


ANGLE_KEYS = {key for key, _, _ in REVIEW_ANGLES}
# These angles verify claims against sources.tsv, so a source-only change invalidates them.
SOURCE_ANGLES = {"01-facts", "02-chronology", "03-context-contrarian", "07-red-team"}
# Defaults are conservative: every text angle depends on all sections. A report can
# narrow an angle in review/scopes.json by listing section-title patterns.
DEFAULT_SCOPES: dict[str, list[str]] = {key: ["*"] for key in ANGLE_KEYS}
DEFAULT_SCOPES["04-images-licensing"] = ["@images"]
DEFAULT_SCOPES["06-render-qa"] = ["@build"]


def frontmatter_hash(meta: dict[str, Any]) -> str:
    return sha256_bytes(
        json.dumps(meta, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    )


def review_manifest_id(md_hash: str, sources_hash: str) -> str:
    """Reviews bind to both the report and the evidence table, so key on both."""
    return sha256_bytes(f"{md_hash}\n{sources_hash}".encode("utf-8"))


def load_review_scopes(report_dir: Path) -> dict[str, list[str]]:
    """Optional review/scopes.json override: angle -> section-title substring patterns."""
    path = report_dir / "review" / "scopes.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"warning: cannot read {path}: {exc}; using default scopes", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"warning: {path} must be a JSON object; using default scopes", file=sys.stderr)
        return {}
    scopes: dict[str, list[str]] = {}
    for key, value in data.items():
        if key not in ANGLE_KEYS:
            print(f"warning: {path}: unknown angle {key!r}; ignored", file=sys.stderr)
            continue
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not value or not all(
            isinstance(item, str) and item for item in value
        ):
            print(
                f"warning: {path}: scope for {key!r} must be a non-empty list of section patterns",
                file=sys.stderr,
            )
            continue
        special = [item for item in value if item in ("@images", "@build")]
        if special and len(value) > 1:
            print(
                f"warning: {path}: {key!r} mixes {special[0]} with section patterns; ignored",
                file=sys.stderr,
            )
            continue
        scopes[key] = value
    return scopes


def angle_scope(key: str, scopes: dict[str, list[str]]) -> list[str]:
    return scopes.get(key) or DEFAULT_SCOPES.get(key) or ["*"]


def _image_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return (start, end, hash) for standalone image blocks outside code fences."""
    blocks: list[tuple[int, int, str]] = []
    in_fence = False
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence or not IMAGE_RE.match(stripped):
            i += 1
            continue
        start = i
        end = i
        block = stripped
        j = i + 1
        if j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and SOURCE_RE.match(lines[j].strip()):
            block += "\n" + lines[j].strip()
            end = j
        blocks.append((start, end, sha256_bytes(block.encode("utf-8"))))
        i = end + 1
    return blocks


def section_hashes(lines: list[str]) -> dict[str, Any]:
    """Text hash excludes image blocks; image blocks are hashed separately."""
    blocks = _image_blocks(lines)
    excluded: set[int] = set()
    for start, end, _ in blocks:
        excluded.update(range(start, end + 1))
    prose = [line for index, line in enumerate(lines) if index not in excluded]
    return {
        "sha256": sha256_bytes("\n".join(lines).encode("utf-8")),
        "text_sha256": sha256_bytes("\n".join(prose).encode("utf-8")),
        "images": [block_hash for _, _, block_hash in blocks],
    }


def split_sections(body: str) -> list[dict[str, Any]]:
    """Split on H2 headings (ignoring fenced code), with a preamble bucket."""
    sections: list[dict[str, Any]] = []
    current_title = "（前言）"
    current_lines: list[str] = []

    def flush() -> None:
        if not current_lines and current_title == "（前言）":
            return
        text = "\n".join(current_lines)
        if not text.strip():
            return
        sections.append({"title": current_title, **section_hashes(current_lines)})

    in_fence = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            flush()
            current_title = match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    flush()
    return sections


def build_state(report_dir: Path) -> dict[str, Any]:
    path = report_dir / "report.build.json"
    if not path.exists():
        return {"build_sha256": "", "build_images": [], "md_sha256": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"build_sha256": "", "build_images": [], "md_sha256": ""}
    build_sha256 = sha256_bytes(
        f"{data.get('html_sha256', '')}:{data.get('pdf_sha256', '')}".encode("utf-8")
    )
    images = [
        {
            "image_url": str(item.get("image_url", "")),
            "source_url": str(item.get("source_url", "")),
            "sha256": str(item.get("sha256", "")),
        }
        for item in data.get("images", [])
    ]
    return {
        "build_sha256": build_sha256,
        "build_images": images,
        "md_sha256": str(data.get("md_sha256", "")),
    }


def review_manifest(
    ctx: BuildContext,
    review_id: str,
    md_hash: str,
    sources_hash: str,
    scopes: dict[str, list[str]],
) -> dict[str, Any]:
    sections = split_sections(ctx.body)
    state = build_state(ctx.report_dir)
    return {
        "version": 2,
        "review_id": review_id,
        "md_sha256": md_hash,
        "sources_sha256": sources_hash,
        "frontmatter_sha256": frontmatter_hash(ctx.meta),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
        "section_order": [section["title"] for section in sections],
        "build_sha256": state["build_sha256"],
        "build_images": state["build_images"],
        "angles": [
            {"key": key, "label": label, "scope": angle_scope(key, scopes)}
            for key, label, _ in REVIEW_ANGLES
        ],
    }


def review_prompt(
    md_path: Path,
    sources_path: Path,
    html_path: Path,
    pdf_path: Path,
    key: str,
    label: str,
    focus: str,
    md_hash: str,
    sources_hash: str,
    sections: list[str],
) -> str:
    section_list = "\n".join(f"- {section}" for section in sections)
    return (
        f"# Context-free reviewer: {label}\n\n"
        "You are a fresh reviewer. You have no prior conversation context and no\n"
        "stake in this report. Do not assume the author's conclusions are correct.\n"
        "Read the entire report, every section. Do not sample.\n\n"
        "## Inputs (relative to the current directory)\n\n"
        f"- Source of truth (Markdown): `{md_path.name}`\n"
        f"- Evidence ledger (TSV): `{sources_path.name}`\n"
        f"- Rendered HTML: `{html_path.name}`\n"
        f"- Rendered PDF: `{pdf_path.name}`\n"
        "- Build manifest: `report.build.json`\n"
        "- Local images: `assets/img/`\n"
        f"- Review ID (md_sha256 + sources_sha256): `{review_manifest_id(md_hash, sources_hash)}`\n\n"
        "A required input that is missing or unreadable is a P0 finding, not a\n"
        "reason to review a degraded version.\n\n"
        "## Sections to cover\n\n"
        f"{section_list}\n\n"
        "## Your angle\n\n"
        f"{focus}\n\n"
        "## Output format\n\n"
        "Return Markdown only:\n\n"
        "1. **Verdict**: PASS or NEEDS WORK.\n"
        "2. **Issues**: a table with columns\n"
        "   `Severity | Section | Issue | Evidence / source | Required fix`.\n"
        "   - P0 = factual error, fabricated/mismatched source, missing image\n"
        "     source, broken HTML/PDF, missing required input.\n"
        "   - P1 = important unsupported claim, chronology error, misleading\n"
        "     framing, image license problem.\n"
        "   - P2 = clarity, style, minor.\n"
        "   Each issue must quote the offending sentence or name the exact figure\n"
        "   and give a locator (Markdown line or PDF page).\n"
        "3. **Coverage**: one line per section: `section — checked, N issues`.\n"
        "   For a zero-issue section, name at least one claim or image you checked.\n"
        "4. **Could not verify**: list what you could not check and why.\n\n"
        "## Required metadata (machine-checked)\n\n"
        "End your output with exactly one fenced JSON block in this shape:\n\n"
        "```json\n"
        "{\n"
        f'  "angle": "{key}",\n'
        f'  "review_id": "{review_manifest_id(md_hash, sources_hash)}",\n'
        f'  "md_sha256": "{md_hash}",\n'
        f'  "sources_sha256": "{sources_hash}",\n'
        '  "verdict": "PASS",\n'
        '  "p0": 0,\n'
        '  "p1": 0,\n'
        '  "coverage": ["<every section heading, verbatim>"]\n'
        "}\n"
        "```\n\n"
        "`verdict` must be PASS or NEEDS WORK; `p0`/`p1` are the counts of P0/P1\n"
        "issues you found; `coverage` must list every section heading verbatim.\n\n"
        "Be specific and adversarial. Do not praise. If you find no issues, say\n"
        "so explicitly and list what you checked. A review with zero P0/P1 is\n"
        "still useful if it shows the coverage.\n"
    )


def cmd_review_kit(args: argparse.Namespace) -> int:
    md_path = Path(args.report).resolve()
    ctx = load_context(md_path)
    report_dir = ctx.report_dir
    review_dir = report_dir / "review"
    review_dir.mkdir(exist_ok=True)
    sources_path = report_dir / "sources.tsv"
    html_path = report_dir / "report.html"
    pdf_path = report_dir / "report.pdf"
    section_objs = split_sections(ctx.body)
    # The preamble is tracked for impact analysis but is not a coverage row; reviewers
    # still read it as part of the whole report.
    sections = [section["title"] for section in section_objs if section["title"] != "（前言）"]
    if not sections:
        sections = ["（未检测到二级标题）"]
    if not html_path.exists() or not pdf_path.exists():
        raise BuildError(
            "report.html/report.pdf not found; run build first so the render-QA and image reviewers "
            "can inspect the real artifacts"
        )

    coverage_path = review_dir / "coverage.md"
    existing_cells: dict[str, list[str]] = {}
    if coverage_path.exists():
        for line in coverage_path.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != len(REVIEW_ANGLES) + 1 or cells[0] in ("章节", ""):
                continue
            if set(cells[0]) <= set("-: "):
                continue
            existing_cells[cells[0]] = cells[1:]
        removed = sorted(set(existing_cells) - set(sections))
        if removed:
            print(
                "warning: coverage.md had sections that no longer exist: " + ", ".join(removed),
                file=sys.stderr,
            )

    coverage = [
        "# 审阅覆盖矩阵",
        "",
        "每个审阅 agent 都必须通读全文；这张表用来确认没有章节漏审。",
        "",
        "| 章节 | " + " | ".join(label for _, label, _ in REVIEW_ANGLES) + " |",
        "|---|" + "---|" * len(REVIEW_ANGLES),
    ]
    for section in sections:
        cells = existing_cells.get(section)
        if not cells or len(cells) != len(REVIEW_ANGLES):
            cells = ["☐" for _ in REVIEW_ANGLES]
        coverage.append("| " + section + " | " + " | ".join(cells) + " |")
    coverage_path.write_text("\n".join(coverage) + "\n", encoding="utf-8")

    md_hash = sha256_file(md_path)
    sources_hash = sha256_file(sources_path) if sources_path.exists() else ""
    scopes_path = review_dir / "scopes.json"
    if not scopes_path.exists():
        default_scopes = {key: DEFAULT_SCOPES[key] for key, _, _ in REVIEW_ANGLES}
        scopes_path.write_text(
            json.dumps(default_scopes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    scopes = load_review_scopes(report_dir)
    review_id = review_manifest_id(md_hash, sources_hash)
    manifests_dir = review_dir / "manifests"
    manifests_dir.mkdir(exist_ok=True)
    manifest = review_manifest(ctx, review_id, md_hash, sources_hash, scopes)
    (manifests_dir / f"{review_id}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (review_dir / "manifest.json").write_text(
        json.dumps(
            {
                "current": review_id,
                "md_sha256": md_hash,
                "sources_sha256": sources_hash,
                "manifest": f"manifests/{review_id}.json",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    canonical = {f"{key}.prompt.md" for key, _, _ in REVIEW_ANGLES}
    for stale in review_dir.glob("*.prompt.md"):
        if stale.name not in canonical:
            stale.unlink()
            print(f"removed stale prompt: {stale.name}", file=sys.stderr)
    for key, label, focus in REVIEW_ANGLES:
        prompt = review_prompt(
            md_path, sources_path, html_path, pdf_path, key, label, focus, md_hash, sources_hash, sections
        )
        (review_dir / f"{key}.prompt.md").write_text(prompt, encoding="utf-8")

    readme = textwrap.dedent(
        f"""\
        # 无上下文审阅

        本目录由 `history_research.py review-kit` 生成。每个 `*.prompt.md` 是一个
        独立审阅任务，覆盖一个角度；`coverage.md` 是章节覆盖矩阵。

        ## 规则

        1. **每个 prompt 起一个全新 agent**。不要把本会话的调研过程、结论或摘要
           带进去。agent 只应看到 prompt 里列出的文件。
        2. **通读全文**，不要抽查。每个 agent 都要对每个章节给出结论。
        3. 审阅输出存为 `review/out/<key>.out.md`，然后把问题修进 `report.md`。
        4. P0/P1 必须修复并重跑对应审阅；P2 可记录后延。
        5. 每个输出末尾必须有机器可读 JSON（`angle` / `review_id` / `md_sha256` /
           `sources_sha256` / `verdict` / `p0` / `p1` / `coverage`）；
           `check --require-review` 会校验版本哈希、P0/P1 为零、以及每个章节都在
           coverage 里。
        6. 改完 `report.md` 后依次运行 `build`、`review-kit`（重新绑定哈希并生成
           新 prompt）、`review-impact`，只重跑受影响的角度；可用 `scopes.json`
           按报告收窄某个角度的章节范围。最终交付前仍要对同一整篇哈希跑完
           7 个角度（见 `../review/impact.md`）。
        7. 把每轮结果记进 `../review.md`，并更新 `coverage.md` 的勾选。

        ## 运行

        在 Claude Code 里用 `Agent`（或环境提供的 fresh subagent 工具），每个
        prompt 一个 fresh subagent，原文作为任务，不要附加对话历史。

        在 paseo 托管、或不允许嵌套子代理的环境里，用 shell 版（每个 prompt 一个
        新鲜 `claude -p` 进程）：

        ```bash
        SKILL_DIR="${{CLAUDE_SKILL_DIR:-$HOME/.claude/skills/history-research}}"
        "$SKILL_DIR/scripts/run_reviews.sh" .
        ```

        ## 审阅角度
        """
    ) + "\n" + "\n".join(f"- `{key}` — {label}" for key, label, _ in REVIEW_ANGLES) + "\n"
    (review_dir / "README.md").write_text(readme, encoding="utf-8")
    print(f"review kit written to {review_dir}")
    print(f"  {len(REVIEW_ANGLES)} prompts, {len(sections)} sections in coverage.md")
    print(f"  review id: {review_id}")
    print(f"  manifest: {review_dir / 'manifests' / (review_id + '.json')}")
    print(f"  scopes: {scopes_path}")
    return 0


def load_review_manifest(report_dir: Path, review_id: str) -> dict[str, Any] | None:
    path = report_dir / "review" / "manifests" / f"{review_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def angle_impacted(
    key: str,
    changed_text: list[str],
    removed: list[str],
    front_changed: bool,
    sources_changed: bool,
    old_sections: dict[str, dict[str, Any]],
    new_sections: dict[str, dict[str, Any]],
    old_build_images: list[dict[str, str]],
    new_build_images: list[dict[str, str]],
    old_md: str,
    current_md: str,
    old_build: str,
    new_build: str,
    scopes: dict[str, list[str]],
) -> bool:
    scope = angle_scope(key, scopes)
    if scope == ["@images"]:
        old_images = [h for section in old_sections.values() for h in section.get("images", [])]
        new_images = [h for section in new_sections.values() for h in section.get("images", [])]
        return old_images != new_images or old_build_images != new_build_images
    if scope == ["@build"]:
        return old_md != current_md or old_build != new_build
    changed = changed_text + removed
    if "*" in scope:
        local = bool(changed)
    else:
        local = any(any(pattern in title for pattern in scope) for title in changed)
    return local or front_changed or (sources_changed and key in SOURCE_ANGLES)


def cmd_review_impact(args: argparse.Namespace) -> int:
    md_path = Path(args.report).resolve()
    ctx = load_context(md_path)
    report_dir = ctx.report_dir
    current_md = sha256_file(md_path)
    sources_path = report_dir / "sources.tsv"
    current_sources = sha256_file(sources_path) if sources_path.exists() else ""
    current_front = frontmatter_hash(ctx.meta)
    current_sections = {section["title"]: section for section in split_sections(ctx.body)}
    current_build = build_state(report_dir)
    if current_build["md_sha256"] != current_md:
        print(
            "warning: report.html/report.pdf are older than report.md; run build before review-impact",
            file=sys.stderr,
        )
    scopes = load_review_scopes(report_dir)

    rows: list[dict[str, Any]] = []
    rerun: list[str] = []
    for key, label, _ in REVIEW_ANGLES:
        out_path = report_dir / "review" / "out" / f"{key}.out.md"
        if not out_path.exists() or out_path.stat().st_size == 0:
            rows.append({"angle": key, "label": label, "status": "missing", "changed": [], "reason": "no output"})
            rerun.append(key)
            continue
        meta = extract_review_metadata(out_path.read_text(encoding="utf-8", errors="ignore"))
        if meta is None:
            rows.append({"angle": key, "label": label, "status": "missing-metadata", "changed": [], "reason": "no JSON block"})
            rerun.append(key)
            continue
        review_id = meta.get("review_id") or meta.get("md_sha256")
        old_manifest = load_review_manifest(report_dir, str(review_id or ""))
        if old_manifest is None:
            rows.append(
                {
                    "angle": key,
                    "label": label,
                    "status": "stale-no-manifest",
                    "changed": [],
                    "reason": "reviewed an older version and no section manifest was kept",
                }
            )
            rerun.append(key)
            continue
        old_sections = {section["title"]: section for section in old_manifest.get("sections", [])}
        old_order = old_manifest.get("section_order") or list(old_sections)
        order_changed = old_order != list(current_sections)
        removed = [title for title in old_sections if title not in current_sections]
        changed_text: list[str] = []
        changed_images: list[str] = []
        for title, section in current_sections.items():
            old = old_sections.get(title)
            if old is None:
                changed_text.append(title)
                if section.get("images"):
                    changed_images.append(title)
                continue
            old_text = old.get("text_sha256") or old.get("sha256")
            if old_text != section.get("text_sha256"):
                changed_text.append(title)
            if old.get("images", []) != section.get("images", []):
                changed_images.append(title)
        front_changed = old_manifest.get("frontmatter_sha256") != current_front
        sources_changed = old_manifest.get("sources_sha256") != current_sources
        old_md = str(old_manifest.get("md_sha256", ""))
        old_build = str(old_manifest.get("build_sha256", ""))
        same_md = old_md == current_md
        impacted = angle_impacted(
            key,
            changed_text + (["（章节顺序）"] if order_changed else []),
            removed,
            front_changed,
            sources_changed,
            old_sections,
            current_sections,
            old_manifest.get("build_images", []),
            current_build["build_images"],
            old_md,
            current_md,
            old_build,
            current_build["build_sha256"],
            scopes,
        )
        image_only = set(changed_images) - set(changed_text)
        changed_labels: list[str] = []
        for title in current_sections:
            if title in changed_text:
                changed_labels.append(title)
            elif title in image_only:
                changed_labels.append(f"{title}（图片）")
        if order_changed:
            changed_labels.append("（章节顺序）")
        changed_labels.extend(f"已删除: {title}" for title in removed)
        reasons: list[str] = []
        if impacted:
            if front_changed:
                reasons.append("frontmatter changed")
            if sources_changed and key in SOURCE_ANGLES:
                reasons.append("sources.tsv changed")
            if key == "04-images-licensing" and (
                changed_images or old_manifest.get("build_images", []) != current_build["build_images"]
            ):
                reasons.append("image block or build image changed")
            if key == "06-render-qa" and old_md != current_md:
                reasons.append("report.md changed; rebuild and re-check render")
        rows.append(
            {
                "angle": key,
                "label": label,
                "status": "current" if same_md and not impacted else ("impacted" if impacted else "not-impacted"),
                "changed": changed_labels,
                "reason": "；".join(reasons),
            }
        )
        if impacted:
            rerun.append(key)

    lines = [
        "# 审阅影响面",
        "",
        f"- 当前 `report.md` SHA256：`{current_md}`",
        f"- 当前 `sources.tsv` SHA256：`{current_sources}`",
        f"- 当前构建产物 SHA256：`{current_build['build_sha256']}`",
        "",
        "| 角度 | 状态 | 变更章节 | 说明 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        changed = "、".join(row["changed"]) if row["changed"] else "—"
        lines.append(f"| {row['label']} (`{row['angle']}`) | {row['status']} | {changed} | {row['reason']} |")
    lines.append("")
    if rerun:
        lines.append("建议重跑：`REVIEW_ONLY=" + ",".join(rerun) + " scripts/run_reviews.sh <report-dir>`")
    else:
        lines.append("所有角度都绑定当前版本，可以直接过 `check --require-review`。")
    lines.append("")
    lines.append("增量重跑只用于迭代；最终交付仍要对同一整篇哈希跑完 7 个角度。")
    text = "\n".join(lines) + "\n"
    if args.write:
        (report_dir / "review" / "impact.md").write_text(text, encoding="utf-8")
        print(f"written: {report_dir / 'review' / 'impact.md'}", file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                {
                    "current_md_sha256": current_md,
                    "current_sources_sha256": current_sources,
                    "current_build_sha256": current_build["build_sha256"],
                    "angles": rows,
                    "rerun": rerun,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build sourced history reports from Markdown.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="scaffold a report directory")
    p_init.add_argument("dir")
    p_init.add_argument("--title", required=True)
    p_init.add_argument("--subtitle", default="")
    p_init.add_argument("--scope", default="")
    p_init.add_argument("--author", default="")
    p_init.add_argument("--date", default="")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_build = sub.add_parser("build", help="compile report.md to HTML and PDF")
    p_build.add_argument("report")
    p_build.add_argument("--pdf-engine", choices=["auto", "playwright", "chrome", "weasyprint"], default="auto")
    p_build.add_argument(
        "--allow-missing-images",
        action="store_true",
        help="draft mode: insert a visible placeholder instead of failing",
    )
    p_build.set_defaults(func=cmd_build)

    p_check = sub.add_parser("check", help="verify image provenance, citations, and sync")
    p_check.add_argument("report")
    p_check.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p_check.add_argument("--online", action="store_true", help="check source URLs over the network")
    p_check.add_argument(
        "--require-review",
        action="store_true",
        help="require 7 recorded review angles and a fully checked coverage matrix",
    )
    p_check.set_defaults(func=cmd_check)

    p_review = sub.add_parser("review-kit", help="generate context-free reviewer prompts")
    p_review.add_argument("report")
    p_review.set_defaults(func=cmd_review_kit)

    p_impact = sub.add_parser("review-impact", help="show which review angles a change invalidates")
    p_impact.add_argument("report")
    p_impact.add_argument("--json", action="store_true")
    p_impact.add_argument("--write", action="store_true", help="write review/impact.md")
    p_impact.set_defaults(func=cmd_review_impact)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
