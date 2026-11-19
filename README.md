# 赤舟作文批改工作台（essay-grader）

给助教用的作文批改 web 应用：粘贴 / 上传学生**手写作文截图** +（可选）题目 → 一键生成红黑灰版式的**批改 PDF**。

## 用法

双击 `启动.bat`（或 `python server.py`），浏览器打开 http://127.0.0.1:8790

1. 填学生姓名、选级别（初L2 ~ 高L2）
2. 粘贴 / 上传作文截图（必填）
3. 题目可打字、可粘贴截图（可选，可留空）
4. 点「批改并生成 PDF」→ 约 1–2 分钟后自动下载 PDF

## 配置

`config.json`（本地，别上传）：

- **DeepSeek（批改用）**：`key` / `model` / `api`
- **OCR（转写用）**：`ocr.provider`（`ark` 豆包视觉 / `paddle` 本地 PaddleOCR / `moonshot` Kimi）、`ocr.key`、`ocr.api`、`ocr.model`

也支持环境变量 `DEEPSEEK_KEY` / `DEEPSEEK_MODEL` / `DEEPSEEK_API` 和 `OCR_PROVIDER` / `OCR_KEY` / `OCR_API` / `OCR_MODEL`。

**OCR 推荐用豆包 Vision（火山方舟）**：模型 `doubao-seed-2-1-pro`（更准）或 `doubao-seed-2-1-turbo`（更快）。⚠️ 豆包 Seed 是**推理模型**，代码里已加 `"thinking": {"type": "disabled"}` 关闭思考——不关会卡 90 秒以上（实测超时）；同时会自动把图缩到 ≤1200px（大图易断连），失败自动重试 4 次。

## 评分标准

5 维 × 5 分 = **25 分**，分五档（21–25 五档 / 16–20 四档 / 11–15 三档 / 6–10 二档 / 1–5 一档）：

内容要点 / 语法准确 / 词汇运用 / 篇章结构 / 书写卷面（书写卷面由视觉模型在原图上直接评估）。

7 个级别对标剑桥 + 中高考，字数与语法范围全在 `rubric.json`，改它即改规范：

| 级别 | 对标 | 字数 |
|---|---|---|
| 初L2 | Movers · 小学 A1 | 30–50 |
| 初L3 | Flyers · 初一 A2 | 50–80 |
| 中L1 | AEIM L1 · KET · 初二–中考 | 80–100 |
| 中L2 | AEIM L2 · KET优/PET过 · 中考–高一 | 100–130 |
| 中L3 | AEIM L3 · PET优 · 高二 | 120–160 |
| 高L1 | FCE过 · 高考 B2 | 140–190 |
| 高L2 | FCE优 · 六级 B2–C1 | 160–220 |

## 测试

`python server.py --mock`：不调模型 / OCR，用假数据验证界面与 PDF 下载。

## 文件

- `server.py` 后端（HTTP + 视觉转写(豆包/本地) + DeepSeek 批改 + WeasyPrint 出 PDF）
- `index.html` 前端
- `rubric.json` 7 级评分标准
- `report_template.html` 红黑灰 PDF 模板
- `启动.bat` 一键启动
