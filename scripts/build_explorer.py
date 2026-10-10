#!/usr/bin/env python3
"""Build a portable, offline design comparison from a local manifest."""

from __future__ import annotations

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "assets" / "style-explorer.html"
MARKER = "/*__OIL_UI_DATA__*/ null"
CONNECT_CSP = "connect-src 'none'"
PREVIEW_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "font-src data:; media-src data:; script-src 'none'; "
    "form-action 'none'; base-uri 'none'; object-src 'none'"
)


class ManifestError(ValueError):
    """Every problem found in one pass, so the manifest can be fixed in one edit."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        if len(problems) == 1:
            super().__init__(problems[0])
        else:
            super().__init__(f"manifest 有 {len(problems)} 处要改：\n" + "\n".join(f"  {i}. {p}" for i, p in enumerate(problems, 1)))


def text_field(obj: dict, name: str, *, default: str | None = None, where: str = "") -> str:
    value = obj.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}{name} 必须是非空字符串")
    return value.strip()


def text_list(obj: dict, name: str, *, where: str = "") -> list[str]:
    values = obj.get(name)
    if not isinstance(values, list) or not values:
        raise ValueError(f"{where}{name} 必须是非空字符串数组")
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError(f"{where}{name} 的每项必须是非空字符串")
    return [v.strip() for v in values]


class AssetCheck(HTMLParser):
    """Reject resource dependencies; the browser sandbox disables behavior."""

    def __init__(self):
        super().__init__()
        self.styles = []
        self.in_style = False
        self.head_position = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attrs = dict(attrs)
        if tag == "head" and self.head_position is None:
            self.head_position = (self.getpos(), self.get_starttag_text())
        if tag in ("iframe", "frame", "object", "embed"):
            raise ValueError("候选 HTML 不支持嵌套文档；请提供静态内容或截图")
        if tag == "style":
            self.in_style = True
        if attrs.get("style"):
            self.styles.append(attrs["style"])
        if tag == "base":
            raise ValueError("候选 HTML 不应包含 base；资源需要内嵌")
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh":
            raise ValueError("候选 HTML 不应自动跳转")
        for key in ("src", "poster", "data", "href", "xlink:href"):
            value = attrs.get(key, "") or ""
            if not value or value.startswith(("#", "data:")):
                continue
            raise ValueError(f"候选 HTML 含未内嵌资源 {tag}.{key}: {value}")
        if attrs.get("srcset"):
            raise ValueError("候选 HTML 请用内嵌 src 代替 srcset")

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self.in_style = False

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.styles.append(data)


CSS_TOKEN = re.compile(
    r"(?P<comment>/\*.*?\*/)|(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
    r"|(?P<ident>(?:[-_a-zA-Z]|\\(?:[0-9a-fA-F]{1,6}\s?|[^\r\n]))"
    r"(?:[-_a-zA-Z0-9]|\\(?:[0-9a-fA-F]{1,6}\s?|[^\r\n]))*)"
    r"|(?P<space>\s+)|(?P<symbol>.)", re.S,
)


def css_unescape(value: str) -> str:
    def replacement(match):
        if match.group(1):
            codepoint = int(match.group(1), 16)
            return chr(codepoint) if 0 < codepoint <= 0x10FFFF else "\ufffd"
        return match.group(2)
    return re.sub(r"\\([0-9a-fA-F]{1,6})(?:\s)?|\\([^\r\n])", replacement, value)


def check_css(css: str, label: str) -> None:
    tokens = [(m.lastgroup, m.group()) for m in CSS_TOKEN.finditer(css)
              if m.lastgroup not in ("comment", "space")]

    def check_resource(value: str):
        if not css_unescape(value).strip().lower().startswith(("data:", "#")):
            raise ValueError(f"{label}: CSS 含未内嵌资源")

    for index, (kind, value) in enumerate(tokens):
        if value == "@" and index + 1 < len(tokens) and tokens[index + 1][0] == "ident" and css_unescape(tokens[index + 1][1]).lower() == "import":
            raise ValueError(f"{label}: 请内嵌 CSS，不使用 @import")
        name = css_unescape(value).lower() if kind == "ident" else ""
        if name not in ("url", "image-set", "-webkit-image-set", "image", "src"):
            continue
        if index + 1 >= len(tokens) or tokens[index + 1][1] != "(":
            continue
        depth, body = 1, []
        for inner_kind, inner_value in tokens[index + 2:]:
            if inner_kind == "symbol" and inner_value == "(":
                depth += 1
            elif inner_kind == "symbol" and inner_value == ")":
                depth -= 1
                if depth == 0:
                    break
            if name == "url":
                body.append(inner_value[1:-1] if inner_kind == "string" else inner_value)
            elif depth == 1 and inner_kind == "string":
                check_resource(inner_value[1:-1])
        if name == "url":
            check_resource("".join(body))


# Sandboxed previews have no storage; give prototypes an in-memory stand-in so their scripts keep running.
STORAGE_SHIM = (
    "<script>(()=>{const mem=()=>{const m=new Map();return{get length(){return m.size},"
    "key:i=>[...m.keys()][i]??null,getItem:k=>m.has(String(k))?m.get(String(k)):null,"
    "setItem:(k,v)=>{m.set(String(k),String(v))},removeItem:k=>{m.delete(String(k))},clear:()=>m.clear()}};"
    "for(const n of['localStorage','sessionStorage']){try{window[n].length}catch{"
    "Object.defineProperty(window,n,{value:mem(),configurable:true})}}})();</script>"
)


def preview_csp(interactive: bool) -> str:
    # Interactive prototypes may run their own inline scripts, still with no network or external files.
    # 'unsafe-eval' lets declarative libraries such as Alpine.js evaluate their attribute expressions.
    return PREVIEW_CSP.replace("script-src 'none'", "script-src 'unsafe-inline' 'unsafe-eval'") if interactive else PREVIEW_CSP


EMBEDDABLE = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".gif": "image/gif", ".avif": "image/avif", ".svg": "image/svg+xml",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
    ".mp4": "video/mp4", ".webm": "video/webm",
}
ATTR_REF = re.compile(r"""(?P<lead>\b(?:src|poster|href|xlink:href)\s*=\s*)(?P<q>["'])(?P<ref>[^"'#][^"']*)(?P=q)""")
CSS_REF = re.compile(r"""url\(\s*(?P<q>["']?)(?P<ref>[^"')\s][^"')]*)(?P=q)\s*\)""")


def embed_local_files(content: str, base: Path, root: Path, used: set[Path]) -> str:
    """Inline images, fonts and videos referenced relative to the candidate, as long as they stay inside the manifest folder."""

    def data_url(ref: str) -> str | None:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:|^//", ref):
            return None
        target = (base / ref.split("?")[0].split("#")[0]).resolve()
        mime = EMBEDDABLE.get(target.suffix.lower())
        if not mime or not target.is_file() or not target.is_relative_to(root):
            return None
        used.add(target)
        return f"data:{mime};base64," + base64.b64encode(target.read_bytes()).decode("ascii")

    def attr(match):
        url = data_url(match["ref"])
        return match.group(0) if url is None else f'{match["lead"]}{match["q"]}{url}{match["q"]}'

    def css(match):
        url = data_url(match["ref"])
        return match.group(0) if url is None else f'url("{url}")'

    return CSS_REF.sub(css, ATTR_REF.sub(attr, content))


SCRIPT_SRC = re.compile(r"""<script\b(?P<attrs>[^>]*?)\bsrc\s*=\s*(?P<q>["'])(?P<ref>[^"']+)(?P=q)(?P<rest>[^>]*)>\s*</script>""", re.I)
LINK_TAG = re.compile(r"<link\b[^>]*>", re.I)
LINK_ATTR = re.compile(r"""\b(?P<name>rel|href)\s*=\s*(?P<q>["'])(?P<value>[^"']*)(?P=q)""", re.I)


def local_file(ref: str, base: Path, root: Path) -> Path | None:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:|^//", ref):
        return None
    target = (base / ref.split("?")[0].split("#")[0]).resolve()
    return target if target.is_file() and target.is_relative_to(root) else None


def inline_local_code(content: str, base: Path, root: Path, used: set[Path], interactive: bool) -> str:
    """Inline stylesheets and, for interactive candidates, scripts that live in the manifest folder."""

    def link(match):
        attrs = {m["name"].lower(): m["value"] for m in LINK_ATTR.finditer(match.group(0))}
        target = local_file(attrs.get("href", ""), base, root) if "stylesheet" in attrs.get("rel", "").lower() else None
        if target is None:
            return match.group(0)
        used.add(target)
        css = embed_local_files(target.read_text(encoding="utf-8"), target.parent, root, used)
        return "<style>" + css.replace("</style", "<\\/style") + "</style>"

    deferred = []

    def script(match):
        target = local_file(match["ref"], base, root)
        if target is None:
            return match.group(0)
        used.add(target)
        if not interactive:
            return ""
        attrs = re.sub(r"\s+", " ", f'{match["attrs"]} {match["rest"]}').strip()
        code = target.read_text(encoding="utf-8").replace("</script", "<\\/script")
        is_module = re.search(r"""type\s*=\s*["']module["']""", attrs, re.I)
        tag = ('<script type="module">' if is_module else "<script>") + code + "</script>"
        # Inline scripts ignore defer, so deferred files move to the end of the body to keep their timing.
        if re.search(r"\bdefer\b", attrs, re.I) and not is_module:
            deferred.append(tag)
            return ""
        return tag

    content = SCRIPT_SRC.sub(script, LINK_TAG.sub(link, content))
    if deferred:
        close = content.lower().rfind("</body>")
        content = content + "".join(deferred) if close < 0 else content[:close] + "".join(deferred) + content[close:]
    return content


def prepare_html(path: Path, interactive: bool = False, root: Path | None = None, used: set[Path] | None = None) -> str:
    content = path.read_text(encoding="utf-8")
    if root is not None:
        root = root.resolve()
        used = used if used is not None else set()
        content = inline_local_code(content, path.parent, root, used, interactive)
        content = embed_local_files(content, path.parent, root, used)
    parser = AssetCheck()
    parser.feed(content)
    for style in parser.styles:
        check_css(style, path.name)
    if parser.head_position is None:
        raise ValueError(f"{path.name}: 候选 HTML 需要完整的 head 元素")
    (line, column), start_tag = parser.head_position
    head_end = sum(len(part) + 1 for part in content.split('\n')[:line - 1]) + column + len(start_tag)
    meta = f'<meta http-equiv="Content-Security-Policy" content="{preview_csp(interactive)}">'
    return content[:head_end] + meta + (STORAGE_SHIM if interactive else "") + content[head_end:]


def prepare_image(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ValueError(f"{path.name}: 静态预览支持 PNG、JPEG 和 WebP")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def local_url(value: str, label: str) -> str:
    """Live candidates only point at a dev server on this machine."""
    try:
        parts = urlsplit(value)
        parts.port  # Reject malformed ports before generating CSP origins.
    except ValueError:
        raise ValueError(f"{label}必须是有效的本机 http(s) 地址，收到的是 {value}") from None
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in LOOPBACK or parts.username or parts.password:
        raise ValueError(f"{label}只能是本机开发服务器地址，例如 http://localhost:5173/orders；收到的是 {value}")
    return value


HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


def read_manifest(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"找不到 manifest：{path}。先在任务目录里写好 manifest.json，字段见 references/style-explorer.md 的“准备输入”")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest 不是合法的 JSON：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}") from None
    if not isinstance(raw, dict):
        raise ValueError("manifest 的最外层必须是一个对象，里面有 schemaVersion、project、brief 和 candidates")
    return raw


def load_manifest(path: Path) -> tuple[dict, set[Path]]:
    raw = read_manifest(path)
    problems: list[str] = []

    def attempt(check):
        """Run one check; keep its message and carry on so the rest still gets checked."""
        try:
            return check()
        except ValueError as exc:
            problems.append(str(exc))
            return None

    if raw.get("schemaVersion") != 1:
        problems.append("manifest.schemaVersion 必须为 1")
    data = {"schemaVersion": 1}
    for name, default in (("project", None), ("brief", None), ("round", "01")):
        data[name] = attempt(lambda: text_field(raw, name, default=default, where="manifest."))
    if "lang" in raw:
        if raw["lang"] not in ("zh", "en"):
            problems.append('manifest.lang 只能是 "zh" 或 "en"')
        data["lang"] = raw["lang"]
    if "serve" in raw:
        serve = raw["serve"]
        if not isinstance(serve, dict):
            problems.append("serve 必须是对象，例如 {\"command\": \"pnpm dev\", \"cwd\": \"/项目的绝对路径\", \"url\": \"http://localhost:3000\"}")
        else:
            if not isinstance(serve.get("command"), str) or not serve["command"].strip():
                problems.append("serve.command 必须是非空字符串")
            if "cwd" in serve and (not isinstance(serve["cwd"], str) or not Path(serve["cwd"]).is_absolute()):
                problems.append("serve.cwd 必须是绝对路径")
            if "url" in serve:
                if not isinstance(serve["url"], str) or not serve["url"].strip():
                    problems.append("serve.url 必须是非空的本机 http(s) 地址")
                else:
                    attempt(lambda: local_url(serve["url"], "serve.url "))
        data["serve"] = serve
    candidates = raw.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        problems.append("candidates 至少需要一个候选")
        candidates = []
    seen = set()
    inputs = {path, TEMPLATE.resolve()}
    output = []
    for index, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict):
            problems.append(f"第 {index} 个候选必须是对象")
            continue
        before = len(problems)
        identifier = candidate.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            problems.append(f"第 {index} 个候选缺少 id")
            identifier = None
        else:
            identifier = identifier.strip()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", identifier):
                problems.append(f'候选 id "{identifier}" 无效：只用小写字母、数字、连字符和下划线，以字母或数字开头，例如 "a" 或 "dir-2"')
            elif identifier in seen:
                problems.append(f'候选 id "{identifier}" 重复了，每个候选要用不同的 id')
            seen.add(identifier)
        where = f"候选 {identifier}：" if identifier else f"第 {index} 个候选："
        kind = candidate.get("kind", "html")
        if kind not in ("html", "image", "url"):
            problems.append(f'{where}kind 只能是 "html"、"image" 或 "url"，收到的是 {json.dumps(kind, ensure_ascii=False)}')
            kind = None
        baseline = candidate.get("baseline", False)
        interactive = candidate.get("interactive", False)
        if not isinstance(baseline, bool) or not isinstance(interactive, bool):
            problems.append(f"{where}baseline 和 interactive 必须是 true 或 false")
        elif interactive and kind not in ("html", None):
            problems.append(f"{where}interactive 只用于 html 候选")
        source = url = None
        if kind == "url":
            url = attempt(lambda: local_url(text_field(candidate, "url", where=where), f"{where}url "))
        elif kind:
            name = attempt(lambda: text_field(candidate, "source", where=where))
            if name:
                source = (path.parent / name).resolve()
                if not source.is_relative_to(path.parent):
                    problems.append(f"{where}source 必须在 manifest 所在的目录里，收到的是 {name}")
                    source = None
                elif not source.is_file():
                    problems.append(f"{where}找不到 source 文件 {name}（按 manifest 所在的目录 {path.parent} 找）")
                    source = None
                else:
                    inputs.add(source)
        colors = attempt(lambda: text_list(candidate, "palette", where=where))
        if colors:
            wrong = [c for c in colors if not HEX_COLOR.fullmatch(c)]
            if wrong:
                problems.append(f'{where}palette 要写十六进制颜色，例如 "#1a2b3c"；这几项不是：{"、".join(wrong)}')
        texts = {name: attempt(lambda: text_field(candidate, name, where=where)) for name in ("name", "concept", "typography")}
        traits = attempt(lambda: text_list(candidate, "traits", where=where))
        if len(problems) > before:
            continue
        try:
            content = url if kind == "url" else prepare_html(source, interactive, path.parent, inputs) if kind == "html" else prepare_image(source)
        except ValueError as exc:
            message = str(exc)
            if message.startswith(f"{source.name}: "):
                message = message[len(source.name) + 2:]
            problems.append(f"{where}{source.name} {message}" if source else f"{where}{message}")
            continue
        output.append({
            "id": identifier, **texts,
            "palette": colors, "traits": traits, "kind": kind,
            "content": content,
            "sourceLabel": url if kind == "url" else source.name,
            "baseline": baseline,
            "interactive": interactive,
        })
    if sum(c["baseline"] for c in output) > 1:
        problems.append("最多只能有一个基线候选（baseline: true）")
    if problems:
        raise ManifestError(problems)
    # The current version always sits first so every direction is read against it.
    output.sort(key=lambda c: not c["baseline"])
    data["candidates"] = output
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    data["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return data, inputs


def build(manifest: Path, output: Path, *, force: bool = False) -> dict:
    manifest, output = manifest.resolve(), output.resolve()
    data, inputs = load_manifest(manifest)
    if output in inputs or output.is_relative_to(SKILL_ROOT):
        raise ValueError("输出不能覆盖输入或写进 Skill 安装目录")
    if output.exists() and not force:
        raise FileExistsError(f"输出已存在：{output}。要更新它就加 --force，要保留它就用 --output 换一个路径")
    template = embed_local_files(TEMPLATE.read_text(encoding="utf-8"), TEMPLATE.parent, SKILL_ROOT, inputs)
    skill_file = SKILL_ROOT / "SKILL.md"
    skill = skill_file.read_text(encoding="utf-8") if skill_file.is_file() else ""
    data["edition"] = "pro" if re.search(r"^name:\s*oil-ui-pro\s*$", skill, re.MULTILINE) else "open"
    if template.count(MARKER) != 1:
        raise ValueError("模板数据入口缺失或重复")
    if template.count(CONNECT_CSP) != 1:
        raise ValueError("模板连接策略入口缺失或重复")
    origins = set()
    for candidate in data["candidates"]:
        if candidate["kind"] == "url":
            parts = urlsplit(candidate["content"])
            # Browsers reject IPv6 literals in CSP source lists; probe their loopback alias.
            host = "localhost" if parts.hostname == "::1" else parts.hostname.lower()
            port = f":{parts.port}" if parts.port is not None else ""
            origins.add(f"{parts.scheme}://{host}{port}")
    connect_csp = "connect-src " + (" ".join(sorted(origins)) if origins else "'none'")
    # A closing script tag in metadata or a nested candidate cannot escape the container.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = template.replace(CONNECT_CSP, connect_csp).replace(MARKER, payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    if force:
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
                temp = Path(handle.name)
                handle.write(page)
            temp.replace(output)
        finally:
            if temp and temp.exists():
                temp.unlink()
    else:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(page)
    if origins and "serve" not in data:
        print("提醒：本轮包含 url 候选，建议在 manifest 顶层补上 serve 启动方式，方便重新打开对比页。", file=sys.stderr)
    return {"output": str(output), "candidates": len(data["candidates"]), "fingerprint": data["fingerprint"], "bytes": output.stat().st_size}


HELP_EPILOG = """\
用法示例：
  python3 build_explorer.py <任务目录>/manifest.json
      生成 <任务目录>/style-explorer.html；已有同名文件时加 --force 覆盖。

manifest.json 最少要有：
  {"schemaVersion": 1, "project": "<项目名>", "brief": "<所有候选共同的内容与任务>",
   "candidates": [{"id": "a", "name": "<方向名>", "concept": "<一句体验意图>",
     "typography": "<实际使用的字体关系>", "palette": ["#112233", "#f5f5f5"],
     "traits": ["<看得见的特征>"], "kind": "html", "source": "a.html"}]}
  kind 还可以是 "image"（source 指向 PNG、JPEG 或 WebP）或 "url"（用 url 字段指向本机开发服务器）。
  全部字段见 references/style-explorer.md 的“准备输入”。

写错时会把 manifest 里所有要改的地方一次列出来，旧的对比页不会被改动。
成功时在标准输出打印一行 JSON：output、candidates、fingerprint、bytes。"""


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更新版本", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="把 manifest 里列出的候选页面组装成一个可以离线打开的风格对比页",
                                     epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path, help="manifest.json 的路径，候选文件和它放在同一个目录或子目录里")
    parser.add_argument("--output", type=Path, help="输出的 HTML 路径；不写时是 manifest 旁边的 style-explorer.html")
    parser.add_argument("--force", action="store_true", help="输出文件已经存在时覆盖它")
    args = parser.parse_args(argv)
    output = args.output or args.manifest.resolve().parent / "style-explorer.html"
    try:
        print(json.dumps(build(args.manifest, output, force=args.force), ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"未生成对比页：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
