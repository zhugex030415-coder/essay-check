#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赤舟作文批改工作台 · 后端

流程：收作文图 + 题目(文字/图) + 姓名 + 级别 → PaddleOCR 转写 → DeepSeek 批改 → WeasyPrint 出 PDF。

用法：
    python server.py                # http://127.0.0.1:8790
    python server.py --mock         # 不调模型/OCR，用假数据验证界面与 PDF

HTTP 用标准库；模型调用用 urllib；OCR 走子进程（.venvs/paddleocr）；PDF 用 weasyprint（系统 Python 已装）。
"""

import argparse
import base64
import io
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent

# ---------------------------------------------------------------- 本地路径（OCR 环境，可 env 覆盖）
OCR_PYTHON = os.environ.get("OCR_PYTHON") or r"D:\deepseek\AI探索研究\.venvs\paddleocr\Scripts\python.exe"
OCR_SCRIPT = os.environ.get("OCR_SCRIPT") or r"D:\deepseek\AI探索研究\tools\ocr\ocr.py"
OCR_LANG = os.environ.get("OCR_LANG") or "ch"

# ---------------------------------------------------------------- 配置（key 只在这/env，不进前端）
def _load_config():
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


CONFIG = _load_config()
API_KEY = os.environ.get("DEEPSEEK_KEY", "").strip() or CONFIG.get("key", "").strip()
API_MODEL = os.environ.get("DEEPSEEK_MODEL", "").strip() or CONFIG.get("model", "deepseek-chat")
API_URL = os.environ.get("DEEPSEEK_API", "").strip() or CONFIG.get("api", "https://api.deepseek.com/chat/completions")

# OCR 后端：paddle（本地 PaddleOCR）｜ark（豆包视觉/火山方舟）｜moonshot（Kimi 视觉）
OCR_PROVIDER = os.environ.get("OCR_PROVIDER") or CONFIG.get("ocr", {}).get("provider", "paddle")
OCR_KEY = os.environ.get("OCR_KEY") or CONFIG.get("ocr", {}).get("key", "")
OCR_API = os.environ.get("OCR_API") or CONFIG.get("ocr", {}).get("api", "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
OCR_MODEL = os.environ.get("OCR_MODEL") or CONFIG.get("ocr", {}).get("model", "")
# 公网部署时设置访问码，防止别人白用你的额度
ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()

RUBRIC = json.loads((ROOT / "rubric.json").read_text(encoding="utf-8"))
TEMPLATE = (ROOT / "report_template.html").read_text(encoding="utf-8")

# ---------------------------------------------------------------- 系统提示
SYS = (
    "你是「赤舟国际教育」的英语作文批改老师。你严格按给定的评分标准与该级难度批改，"
    "不自由发挥、不套用别的考试标准。批改落到具体句子，讲机制不讲「记住」；"
    "自然成立的多解不强行改；OCR 看不清的字标「待确认」，不瞎批。"
    "所有评语（维度评语、总评、亮点、修改建议）都用温暖、鼓励、有爱的语气：先肯定学生的努力和写得好的地方，"
    "再温和地指出问题，对事不对人，不说打击人的话，给出具体可行的改进方向。你只输出 JSON，不要任何解释。"
)

JSON_SCHEMA = '''{
  "评分": {
    "内容要点": {"分数": 4, "评语": "温暖一句话：先肯定，再温和提可提升处"},
    "语法准确": {"分数": 3, "评语": "温暖一句话：先肯定，再温和提可提升处"},
    "词汇运用": {"分数": 3, "评语": "温暖一句话：先肯定，再温和提可提升处"},
    "篇章结构": {"分数": 4, "评语": "温暖一句话：先肯定，再温和提可提升处"}
  },
  "逐句批注": [{"原句": "原文句子", "改正": "改好的句子（把修正的词/短语用 **双星号** 标出，只标关键改动）", "原因": ["分点原因1", "分点原因2"]}],
  "亮点": [{"表达": "用得好的词/句", "好在哪": "为什么好，一两句"}],
  "总评": "温暖鼓励的一段话：先肯定优点，再温和指出问题，结尾鼓励",
  "修改建议": ["具体可执行的建议"],
  "升格范文": "保留原意的改写全文；把用得好的词/短语/句式用 **双星号** 包起来（3–6 处）"
}'''

MOCK_TRANSCRIPT = (
    "My name is Tom. I like playing football with my friend. "
    "Last weekend, we go to the park and play football together. It is very happy day.\n\n"
    "I think football is the best sport. It make me strong and happy."
)
MOCK_GRADE = {
    "评分": {
        "内容要点": {"分数": 4, "评语": "要点基本齐全，介绍了爱好与一次经历"},
        "语法准确": {"分数": 3, "评语": "过去时混用（go→went）、三单漏 s（make→makes）"},
        "词汇运用": {"分数": 3, "评语": "基础词汇够用，可再丰富"},
        "篇章结构": {"分数": 4, "评语": "分段清楚、衔接自然"},
    },
    "逐句批注": [
        {"原句": "we go to the park", "改正": "we went to the park", "原因": "上周的事用一般过去时，go 是不规则动词"},
        {"原句": "It make me strong", "改正": "It makes me strong", "原因": "主语 it 是第三人称单数，make 加 -s"},
    ],
    "亮点": ["I like playing football with my friend", "I think football is the best sport"],
    "总评": "能清楚表达爱好和经历，要点齐全；主要问题是过去时与三单时态，属该级别高频错误。",
    "修改建议": ["练一般过去时不规则动词（go/went、make/made）", "三单现在时动词加 -s 的专项练习"],
    "升格范文": "My name is Tom. I like playing football with my friends. Last weekend, we went to the park and played football together. It was a very happy day. I think football is the best sport. It makes me strong and happy.",
}


# ---------------------------------------------------------------- 工具
def extract_json(text):
    """从模型输出里抠出第一个完整 JSON 对象。"""
    if not text:
        raise ValueError("空输出")
    s = text.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    start = s.find("{")
    if start < 0:
        raise ValueError("输出里没有 JSON")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError("JSON 不完整")


def count_words(text):
    return len(re.findall(r"[A-Za-z]+(?:[’'-][A-Za-z]+)*", text or ""))


def decode_image(b64):
    if "," in b64 and b64.lstrip().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64.strip())


def ocr_bytes(data: bytes, lang=OCR_LANG):
    """图片字节 -> (文本, 平均置信度)。走 paddleocr venv 子进程。"""
    fd, tmp = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        proc = subprocess.run(
            [OCR_PYTHON, OCR_SCRIPT, tmp, "--json", "--lang", lang],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"OCR 退出码 {proc.returncode}：{proc.stderr[-300:]}")
        payload = extract_json(proc.stdout)
        pages = payload.get("pages") or []
        text = "\n\n".join((p.get("text") or "").strip() for p in pages).strip()
        scores = [l.get("score", 0.0) for p in pages for l in (p.get("lines") or [])]
        conf = (sum(scores) / len(scores)) if scores else 0.0
        return text, conf
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def handwriting_score(conf):
    """用 OCR 置信度粗估书写卷面（1–5），并标注需人工核对。"""
    if conf >= 0.93:
        score, word = 5, "工整美观"
    elif conf >= 0.88:
        score, word = 4, "工整"
    elif conf >= 0.80:
        score, word = 3, "基本可读"
    elif conf >= 0.70:
        score, word = 2, "潦草"
    else:
        score, word = 1, "难辨"
    note = f"{word}（OCR 置信度 {conf:.0%}，建议人工核对原图）"
    return score, note


# ---------------------------------------------------------------- 模型调用
def call_chat(api, key, payload):
    """通用 OpenAI 兼容 chat 调用，返回 message content 字符串。"""
    if not key:
        raise RuntimeError("未配置 key")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"HTTP {e.code}：{detail}")
    except Exception as e:
        raise RuntimeError(f"请求失败：{e}")
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"返回格式异常：{str(data)[:300]}")


def call_model(prompt):
    if not API_KEY:
        raise RuntimeError("未配置 API key：填 config.json 的 key，或设环境变量 DEEPSEEK_KEY")
    m = str(API_MODEL).lower()
    payload = {
        "model": API_MODEL,
        "messages": [
            {"role": "system", "content": SYS},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
    }
    # 推理模型（v4-pro / reasoner）显式关掉思考，避免默认深度思考拖慢
    if "v4-pro" in m or "reasoner" in m:
        payload["reasoning_effort"] = "none"
    return call_chat(API_URL, API_KEY, payload)


def _downscale_image(data: bytes, max_side=1200, quality=88):
    """把图片缩到 max_side 以内（JPEG）。大图更容易触发远端断连/404；失败就原样返回。"""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, "image/png"


def ocr_vision(data: bytes):
    """视觉模型转写手写作文 + 评估书写卷面。返回 (text, hw_score, hw_note)。带缩图与重试。"""
    if not OCR_KEY or not OCR_MODEL:
        raise RuntimeError("未配置 OCR 视觉模型：config.json 的 ocr.key / ocr.model，或环境变量 OCR_KEY / OCR_MODEL")
    img_bytes, mime = _downscale_image(data)
    img_b64 = base64.b64encode(img_bytes).decode()
    prompt = (
        "这是学生的手写英语作文。请做两件事，只输出 JSON，不要解释：\n"
        "1. 逐句转写成英文原文，保留学生原样（含语法/拼写错误），不要修改、不要批改；看不清的字用[?]标注。\n"
        "2. 评估书写卷面：1-5 分（5=工整美观，4=工整，3=基本可读，2=潦草，1=难辨），给一句评语。\n"
        '输出 JSON：{"text":"转写全文","handwriting":{"score":1到5的整数,"note":"一句评语"}}'
    )
    payload = {
        "model": OCR_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": 2000,
        # 豆包 Seed 是推理模型，默认会「思考」很久导致转写卡死；关闭思考
        "thinking": {"type": "disabled"},
    }
    last = None
    for attempt in range(4):
        try:
            raw = call_chat(OCR_API, OCR_KEY, payload)
            j = extract_json(raw)
            text = (j.get("text") or "").strip()
            hw = j.get("handwriting") or {}
            try:
                hw_score = max(1, min(5, int(hw.get("score", 3))))
            except (TypeError, ValueError):
                hw_score = 3
            hw_note = str(hw.get("note", "")).strip() or "由视觉模型评估"
            return text, hw_score, hw_note
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"视觉模型调用失败（已重试 4 次）：{last}")


def transcribe(data: bytes):
    """按 OCR_PROVIDER 分派转写，统一返回 (text, hw_score, hw_note)。"""
    if OCR_PROVIDER == "paddle":
        text, conf = ocr_bytes(data)
        hw_score, hw_note = handwriting_score(conf)
        return text, hw_score, hw_note
    return ocr_vision(data)


def grade(essay_text, question_text, level, mock=False):
    if mock:
        return MOCK_GRADE
    lv = RUBRIC["levels"][level]
    desc = RUBRIC["dimension_descriptors"]
    dim_lines = []
    for d in ["内容要点", "语法准确", "词汇运用", "篇章结构"]:
        dd = desc[d]
        dim_lines.append(f"- {d}：1分={dd['1']}；2分={dd['2']}；3分={dd['3']}；4分={dd['4']}；5分={dd['5']}")
    dim_text = "\n".join(dim_lines)

    prompt = f"""学生级别：{level}（{lv['target']}）
建议字数：{lv['words'][0]}–{lv['words'][1]} 词
该级语法范围：{lv['grammar']}
该级内容要求：{lv['content']}
该级结构要求：{lv['structure']}

【作文题目】{question_text.strip() if question_text else '（未提供题目）'}

【学生作文原文】（手写 OCR 转写，可能有错字；看不清处标「待确认」，不确定处不硬批）
{essay_text}

【评分标准】5 个维度，每维 1–5 分，总分 25：
{dim_text}
- 书写卷面：由视觉模型另算，你不用打这维。

批改要求：
1. 语法准确、词汇运用两维按上面的「该级语法范围」判定，不要用更高/更低级别的标准。
2. 逐句批改：只挑有错或可提升的句子；**只要文中确有语法/用词/拼写错误，逐句批注就至少列 2 条、不能为空**；原句照抄原文；改正给正确写法，并把修正的词/短语用 **双星号** 标出（只标关键改动，不要整句标）；原因**分点写**，每点一句话，讲清机制（为什么错），不要堆成一大段。
3. 自然成立的多解不强行改。
4. 升格范文保留原意，只修正语言，不改变内容与结构骨架；同时把用得好的词/短语/句式用 **双星号** 包起来（3–6 处，只标亮点词句，不标普通词），便于在 PDF 里加粗。
5. 「亮点」不能只列表达，每条都要说明「好在哪」（为什么好、起了什么作用），一两句话。
6. 所有评语都用温暖鼓励的语气：先肯定学生的努力和优点，再温和指出问题，语气亲切，给出具体可行的改进方向；避免冷冰冰的评分腔，也不要说打击人的话。**每个维度的评语也要温暖**：先肯定该维度的亮点，再用「如果能……会更棒」这类温和说法指出可提升处，不要只写「有错误」「不够」。总评要像老师在跟学生当面聊天，先夸再建议、结尾给鼓励。

输出 JSON（只输出 JSON，不要解释、不要代码块）：
{JSON_SCHEMA}"""
    raw = call_model(prompt)
    result = extract_json(raw)
    # 兜底：模型偶尔不给「逐句批注」，为空时单独再要一次
    if not (result.get("逐句批注") or []):
        try:
            fix_prompt = (
                "请只针对下面这篇学生作文，列出至少 2 条「逐句批注」，"
                '输出 JSON：{"逐句批注":[{"原句":"...","改正":"改好的句子（把修正的词用 **双星号** 标出）","原因":["分点1","分点2"]}]}\n'
                f"级别：{level}\n作文原文：\n{essay_text}"
            )
            fix = extract_json(call_model(fix_prompt))
            if fix.get("逐句批注"):
                result["逐句批注"] = fix["逐句批注"]
        except Exception:
            pass
    return result


# ---------------------------------------------------------------- 报告
def _bold(text):
    """转义后把 **...** 转成加粗。"""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(str(text)))


def _reasons_html(v):
    """原因：列表或分号/换行分隔的字符串，渲染成分点列表。"""
    if isinstance(v, list):
        parts = [str(x) for x in v if str(x).strip()]
    else:
        parts = [p.strip() for p in re.split(r"[；;\n]", str(v)) if p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return escape(parts[0])
    return '<ul class="reasons">' + "".join(f"<li>{escape(p)}</li>" for p in parts) + "</ul>"


def render_report(grade_json, level, name, question_text, transcript, hw_score, hw_note, essay_img=""):
    lv = RUBRIC["levels"][level]
    date = datetime.now().strftime("%Y-%m-%d")
    wc = count_words(transcript)

    scores = grade_json.get("评分") or {}
    rows = []
    total = hw_score
    for d in ["内容要点", "语法准确", "词汇运用", "篇章结构"]:
        item = scores.get(d) or {}
        sc = item.get("分数", 0)
        sc = 3 if not isinstance(sc, (int, float)) else int(round(sc))
        sc = max(1, min(5, sc))
        total += sc
        rows.append(f'<tr><td>{d}</td><td class="right">{sc}</td><td>{escape(str(item.get("评语", "")))}</td></tr>')
    rows.append(f'<tr><td>书写卷面</td><td class="right">{hw_score}</td><td>{escape(hw_note)}</td></tr>')
    total = max(5, min(25, total))
    band = next((b["name"] for b in RUBRIC["bands"] if b["min"] <= total <= b["max"]), "一档")
    rows.append(f'<tr class="total-row"><td>总分</td><td class="right">{total}</td><td>{band}</td></tr>')

    ann_html = ""
    ann_list = grade_json.get("逐句批注") or grade_json.get("逐句批改") or []
    for a in ann_list:
        ann_html += (
            f'<tr><td>{escape(str(a.get("原句", "")))}</td>'
            f'<td>{_bold(a.get("改正", ""))}</td>'
            f'<td>{_reasons_html(a.get("原因", ""))}</td></tr>'
        )
    if not ann_html:
        ann_html = '<tr><td colspan="3">（无批注）</td></tr>'

    hl = grade_json.get("亮点") or []
    if hl:
        items = []
        for x in hl:
            if isinstance(x, dict):
                e = escape(str(x.get("表达", x.get("亮点", ""))))
                w = escape(str(x.get("好在哪", x.get("为什么", ""))))
                items.append(f"<li><strong>{e}</strong>：{w}</li>")
            else:
                items.append(f"<li>{escape(str(x))}</li>")
        hl_html = '<ul class="suggest">' + "".join(items) + "</ul>"
    else:
        hl_html = '<div class="note">（无明显亮点）</div>'
    sug = grade_json.get("修改建议") or []
    sug_html = '<ol class="suggest">' + "".join(f"<li>{escape(str(x))}</li>" for x in sug) + "</ol>" if sug else '<div class="note">（无）</div>'

    q = (question_text or "").strip()
    q_block = f'<h2 class="sec">〇、作文题</h2><div class="note">{escape(q) if q else "（未提供题目）"}</div>'

    # 升格范文：把 **好词好句** 转成加粗
    polished_html = _bold(str(grade_json.get("升格范文", "")) or "（无）")

    html = TEMPLATE
    fill = {
        "{{NAME}}": escape(name or "学生"),
        "{{LEVEL}}": escape(level),
        "{{TARGET}}": escape(lv["target"]),
        "{{WORDS}}": str(wc),
        "{{DATE}}": date,
        "{{TOTAL}}": str(total),
        "{{BAND}}": band,
        "{{QUESTION_BLOCK}}": q_block,
        "{{ESSAY_IMG}}": essay_img,
        "{{SCORE_TABLE}}": "".join(rows),
        "{{ANNOTATIONS}}": ann_html,
        "{{HIGHLIGHTS}}": hl_html,
        "{{OVERALL}}": escape(str(grade_json.get("总评", "")) or "（无）"),
        "{{SUGGESTIONS}}": sug_html,
        "{{POLISHED}}": polished_html,
    }
    for token, val in fill.items():
        html = html.replace(token, val)
    return html


def html_to_pdf(html):
    from weasyprint import HTML  # 懒加载，系统 Python 已装
    return HTML(string=html).write_pdf()


# ---------------------------------------------------------------- 请求处理
def handle_grade(req):
    name = (req.get("name") or "").strip() or "学生"
    level = (req.get("level") or "").strip()
    if level not in RUBRIC["levels"]:
        raise ValueError(f"未知级别：{level}")
    essay_b64 = (req.get("essay_image") or "").strip()
    if not essay_b64:
        raise ValueError("缺少作文图片")

    mock = bool(req.get("mock")) or SERVER_MOCK

    essay_data = decode_image(essay_b64)
    if mock:
        transcript, hw_score, hw_note = MOCK_TRANSCRIPT, 4, "mock 模式示例"
    else:
        transcript, hw_score, hw_note = transcribe(essay_data)

    question_text = (req.get("question_text") or "").strip()
    question_b64 = (req.get("question_image") or "").strip()
    if question_b64:
        if mock:
            q_text = "（题目图：mock）"
        else:
            q_text, _, _ = transcribe(decode_image(question_b64))
        question_text = (question_text + "\n" + q_text).strip() if question_text else q_text

    grade_json = grade(transcript, question_text, level, mock)
    # 原图缩到适合 PDF 的尺寸，转 data URI 嵌进报告
    img_bytes, img_mime = _downscale_image(essay_data)
    essay_img = f"data:{img_mime};base64,{base64.b64encode(img_bytes).decode()}"
    html = render_report(grade_json, level, name, question_text, transcript, hw_score, hw_note, essay_img)
    return html_to_pdf(html)


class Handler(BaseHTTPRequestHandler):
    server_version = "EssayGrader"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = self.path.split("?")[0]
        name = "index.html" if p in ("/", "/index.html") else p.lstrip("/")
        f = (ROOT / name).resolve()
        if not str(f).startswith(str(ROOT.resolve())) or not f.is_file():
            self._send(404, {"error": "not found"})
            return
        ctype = "text/html; charset=utf-8" if f.suffix == ".html" else "application/json; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    def _auth(self):
        if not ACCESS_CODE:
            return True
        code = self.headers.get("X-Access-Code") or ""
        if not code and "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if kv.startswith("code="):
                    code = kv[5:]
        return code == ACCESS_CODE

    def do_POST(self):
        if self.path != "/api/grade":
            self._send(404, {"error": "not found"})
            return
        if not self._auth():
            self._send(401, {"error": "需要访问码"})
            return
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            self._send(400, {"error": f"bad json: {e}"})
            return
        try:
            pdf = handle_grade(req)
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return
        self._send(200, pdf, "application/pdf")


SERVER_MOCK = False

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8790")))
    _default_host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    ap.add_argument("--host", default=os.environ.get("HOST", _default_host))
    ap.add_argument("--mock", action="store_true", help="不调模型/OCR，用假数据验证界面与 PDF")
    a = ap.parse_args()
    SERVER_MOCK = a.mock
    print(f"赤舟作文批改工作台 → http://{a.host}:{a.port}")
    print(f"模型 {API_MODEL} ｜ {'MOCK 模式' if a.mock else '真实调用'} ｜ OCR 后端 {OCR_PROVIDER}")
    print(f"API key {'已配置' if API_KEY else '未配置（填 config.json 的 key 或设 DEEPSEEK_KEY）'}")
    print("Ctrl+C 停止")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
