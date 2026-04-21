#!/usr/bin/env python3
"""
将 docs/PRD_agent_roadmap.md 导出为 docs/PRD_agent_roadmap.pdf。

优先级：
1) 系统已安装 pandoc 且可用 PDF 引擎（如已装 MiKTeX / TeX / wkhtmltopdf 等）时，直接 pandoc 转换（版式通常更好）。
2) 否则使用 Python：markdown + xhtml2pdf（安装：pip install markdown xhtml2pdf）。

Windows / Linux 下通过 CSS @font-face 嵌入常见中文字体文件；勿仅依赖 reportlab.registerFont（xhtml2pdf 对 HTML 正文不会自动用）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MD = _REPO_ROOT / "docs" / "PRD_agent_roadmap.md"


def _try_pandoc(md: Path, pdf: Path) -> bool:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return False
    cmd = [pandoc, str(md), "-o", str(pdf), f"--resource-path={md.parent}"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return pdf.is_file()
    except (subprocess.CalledProcessError, OSError):
        return False


def _find_browser_for_pdf() -> str | None:
    """查找可用于无头打印 PDF 的浏览器（Edge/Chrome）。"""
    for name in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    candidates = [
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def _pick_cjk_font_file() -> Path | None:
    """选可嵌入的字体路径：优先微软雅黑。"""
    win = Path(r"C:\Windows\Fonts")
    if win.is_dir():
        # 用户指定优先微软雅黑：先尝试 msyh.ttf（更稳），再 msyh.ttc（可能受环境影响）
        for name in (
            "msyh.ttf",
            "msyh.ttc",
            "msyhbd.ttf",
            "msyhbd.ttc",
            "simhei.ttf",
            "simkai.ttf",
            "Deng.ttf",
            "STXIHEI.TTF",
            "STSONG.TTF",
            "msjh.ttf",
        ):
            p = win / name
            if p.is_file():
                return p
        for p in sorted(win.glob("msyh*.tt*")):
            return p
    for p in (
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ):
        if p.is_file():
            return p
    ping = Path("/System/Library/Fonts/PingFang.ttc")
    if ping.is_file():
        return ping
    # 末位：仅 .ttc 时 xhtml2pdf 仍可能缺字，仅作兜底
    if win.is_dir():
        for name in ("msyh.ttc", "simsun.ttc"):
            p = win / name
            if p.is_file():
                return p
    return None


def _cjk_font_css() -> tuple[str, str]:
    """
    返回 (@font-face + 全局 font-family 的 style 片段, 主 font-family 名)。
    找不到字体时返回 ("", "Helvetica")。
    """
    path = _pick_cjk_font_file()
    if path is None:
        return "", "Helvetica"
    try:
        uri = path.resolve().as_uri()
    except ValueError:
        return "", "Helvetica"
    fam = "CJKDoc"
    face = f"""
@font-face {{
  font-family: {fam};
  src: url("{uri}");
  font-weight: normal;
  font-style: normal;
}}
"""
    global_css = f"""
  html, body, div, span, h1, h2, h3, p, li, td, th, strong, em, code, pre {{
    font-family: {fam}, "Microsoft YaHei", SimHei, sans-serif !important;
  }}
"""
    return face + global_css, fam


def _render_markdown_html(md: Path, *, for_browser: bool = False) -> str | None:
    try:
        import markdown
    except ImportError:
        return None
    text = md.read_text(encoding="utf-8")
    body = markdown.markdown(
        text,
        extensions=["markdown.extensions.tables", "markdown.extensions.fenced_code"],
    )
    if for_browser:
        # 浏览器打印时优先依赖系统字体映射，避免 @font-face + .ttc 引发缺字/tofu
        font_css = """
  html, body, div, span, h1, h2, h3, p, li, td, th, strong, em, code, pre {
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", SimHei, sans-serif !important;
  }
"""
    else:
        font_css, _fam = _cjk_font_css()
        if not font_css:
            print(
                "警告: 未找到可用的中文字体文件（优先微软雅黑 msyh.ttf/msyh.ttc）。"
                "PDF 中文可能显示为黑块；请安装简体中文语言补充字体，或改用系统 Pandoc 导出。",
                file=sys.stderr,
            )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<style>
{font_css}
  @page {{ size: A4; margin: 14mm 14mm 16mm 14mm; }}
  html, body {{
    margin: 0;
    padding: 0;
    color: #111;
    font-size: 10.5pt;
    line-height: 1.55;
  }}
  h1 {{ font-size: 17pt; margin: 0 0 8pt; }}
  h2 {{ font-size: 14pt; margin: 16pt 0 8pt; }}
  h3 {{ font-size: 12pt; margin: 12pt 0 6pt; }}
  p, li {{ margin: 3pt 0; }}
  ul, ol {{ margin: 4pt 0 6pt 18pt; padding: 0; }}
  hr {{ border: 0; border-top: 1px solid #bbb; margin: 10pt 0; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 8pt 0 10pt;
    table-layout: fixed;
    page-break-inside: auto;
    break-inside: auto;
  }}
  thead {{ display: table-header-group; }}
  tfoot {{ display: table-footer-group; }}
  tr {{ page-break-inside: avoid; break-inside: avoid; }}
  th, td {{
    border: 1px solid #999;
    padding: 4px 6px;
    vertical-align: top;
    word-break: break-word;
    overflow-wrap: anywhere;
  }}
  code {{ font-size: 9.5pt; word-break: break-word; }}
  pre {{
    white-space: pre-wrap;
    word-break: break-word;
    border: 1px solid #ddd;
    padding: 6pt;
  }}
</style></head><body>{body}</body></html>"""


def _try_browser_print(md: Path, pdf: Path) -> bool:
    """使用 Edge/Chrome 无头打印（中文字体渲染通常比 xhtml2pdf 稳）。"""
    browser = _find_browser_for_pdf()
    if not browser:
        return False
    html = _render_markdown_html(md, for_browser=True)
    if html is None:
        return False
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
            f.write(html)
            tmp = Path(f.name)
        file_url = tmp.resolve().as_uri()
        cmd_variants = [
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--allow-file-access-from-files",
                "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                "--prefer-css-page-size",
                f"--print-to-pdf={pdf}",
                file_url,
            ],
            [
                browser,
                "--headless",
                "--disable-gpu",
                "--allow-file-access-from-files",
                "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                "--prefer-css-page-size",
                f"--print-to-pdf={pdf}",
                file_url,
            ],
        ]
        for cmd in cmd_variants:
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if pdf.is_file() and pdf.stat().st_size > 0:
                    return True
            except (subprocess.CalledProcessError, OSError):
                continue
        return False
    finally:
        if tmp and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _try_xhtml2pdf(md: Path, pdf: Path) -> bool:
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return False

    html = _render_markdown_html(md, for_browser=False)
    if html is None:
        return False

    from io import BytesIO

    with pdf.open("wb") as f:
        status = pisa.CreatePDF(BytesIO(html.encode("utf-8")), dest=f, encoding="utf-8")
    return not status.err and pdf.is_file()


def main() -> int:
    p = argparse.ArgumentParser(description="导出 PRD Markdown 为 PDF")
    p.add_argument("--input", type=Path, default=_DEFAULT_MD, help="输入 .md 路径")
    p.add_argument("--output", type=Path, default=None, help="输出 .pdf 路径（默认与 md 同目录同名）")
    p.add_argument(
        "--engine",
        choices=["auto", "pandoc", "browser", "xhtml2pdf"],
        default="auto",
        help="导出引擎：auto(默认)/pandoc/browser/xhtml2pdf",
    )
    args = p.parse_args()
    md: Path = args.input.resolve()
    if not md.is_file():
        print(f"找不到文件: {md}", file=sys.stderr)
        return 2
    pdf = (args.output.resolve() if args.output else md.with_suffix(".pdf"))

    engine = args.engine
    if engine == "pandoc":
        if _try_pandoc(md, pdf):
            print(f"[engine=pandoc] {pdf}")
            return 0
    elif engine == "browser":
        if _try_browser_print(md, pdf):
            print(f"[engine=browser] {pdf}")
            return 0
    elif engine == "xhtml2pdf":
        if _try_xhtml2pdf(md, pdf):
            print(f"[engine=xhtml2pdf] {pdf}")
            return 0
    else:
        if _try_pandoc(md, pdf):
            print(f"[engine=pandoc] {pdf}")
            return 0
        if _try_browser_print(md, pdf):
            print(f"[engine=browser] {pdf}")
            return 0
        if _try_xhtml2pdf(md, pdf):
            print(f"[engine=xhtml2pdf] {pdf}")
            return 0

    print(
        "无法导出 PDF。建议按以下顺序排查：\n"
        "  1) 先用浏览器引擎（推荐）：python scripts/export_prd_pdf.py --engine browser\n"
        "     需本机安装 Edge 或 Chrome（支持 --headless --print-to-pdf）。\n"
        "  2) 再试 Pandoc：python scripts/export_prd_pdf.py --engine pandoc\n"
        "     需安装 Pandoc 并配置可用 PDF 引擎。\n"
        "  3) 最后用 xhtml2pdf：pip install markdown xhtml2pdf\n"
        "     然后执行：python scripts/export_prd_pdf.py --engine xhtml2pdf\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
