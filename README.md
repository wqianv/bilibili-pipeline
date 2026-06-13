# Bilibili Pipeline

全自动 B 站视频处理流水线：下载 → 字幕/语音转录 → LLM 摘要 → 可选视觉分析。  
配套本地 RAG 图书问答系统（Book QA）。

## 快速开始

```bash
# 安装依赖
pip install faster-whisper openai flask yt-dlp

# 配置
cp .env.example .env   # 填入 API Key
# 编辑 config.json 修改模型/定价等

# 处理单个视频
./pipeline.sh https://www.bilibili.com/video/BV1xx...
```

## 项目结构

```
bilibili-pipeline/
├── config.json          # 集中配置：模型/定价/提示词/下载参数
├── config_get.py        # bash 读取 config.json 的辅助工具
│
├── pipeline.sh          # 编排器：逐字稿转换→摘要→分析
├── transcribe.py        # faster-whisper 语音转文字
├── summarize.py         # LLM 结构化摘要
├── analyze.py           # 视觉分析：检测关键帧 + 多模态描述
│
├── batch_dl.py          # 批量下载（反 Ban 策略）
├── batch_up.py          # UP 主批量采集 + 总摘要
├── fetch_fav.py         # 收藏夹获取器
├── process_fav.py       # 逐条处理收藏夹视频
│
├── db.py                # SQLite 数据库 + CLI
├── webui.py             # Flask 面板（端口 8686）
├── scan_history.py      # 扫描历史结果导入数据库
│
├── skills/
│   ├── bilibili-pipeline.md   # opencode Skill 配置
│   └── book-qa.md             # Book QA Skill 配置
└── .env                 # API Key（已 gitignore）
```

## 使用方式

### 单视频处理

```bash
# 文本模式（最快）
./pipeline.sh https://www.bilibili.com/video/BV1xx...

# 带截图分析
./pipeline.sh --analyze https://www.bilibili.com/video/BV1xx...

# 指定视觉分析管线
./pipeline.sh --analyze --pipeline ds_qwen https://...
```

### 批量下载

```bash
# 从文件读取 URL 列表
python3 batch_dl.py --urls-file urls.txt

# 播放列表
python3 batch_dl.py --playlist "https://..."

# 随机间隔 10-30s
python3 batch_dl.py --urls-file urls.txt --sleep-interval 10 30
```

### UP 主采集

```bash
python3 batch_up.py --up-uid 385474 --cookies cookies.txt
python3 batch_up.py --up-uid 385474 --cookies cookies.txt --max-videos 5
```

### 收藏夹处理

```bash
# 列出收藏夹（需 cookies）
python3 fetch_fav.py --uid 13585203 --cookies cookies.txt --list-only

# 下载收藏夹视频
python3 fetch_fav.py --media-id 96336103 --download

# 逐条处理（下载→转录→摘要，大随机间隔）
python3 process_fav.py --sleep-interval 60 180
```

### 数据库 / Web UI

```bash
python3 db.py list          # 列出所有视频（含 T/S/V 状态）
python3 db.py status        # 查看未处理的视频
python3 db.py info BV1xx    # 视频详情
python3 db.py stats         # 统计

python3 webui.py --port 8686  # 启动面板 → http://127.0.0.1:8686
```

## 输出目录

```
~/Documents/bilibili/
├── clips/          # 单视频处理结果
│   └── BVxxx_20260607/
│       ├── video.mp4
│       ├── audio.mp3
│       ├── transcript.txt
│       ├── transcript.srt
│       ├── summary.md
│       ├── visual_notes.md
│       └── frames/
├── batch/          # UP 主批量采集结果
├── downloads/      # 批量下载
├── bilibili.db     # SQLite 数据库
└── fav_urls.txt    # 收藏夹 URL 列表
```

## 模型配置

所有模型/定价/提示词均在 `config.json` 集中管理：

| 模型 | 用途 | 输入 ¥/M | 输出 ¥/M |
|------|------|----------|----------|
| DeepSeek V4 Flash | 摘要 | ¥1 | ¥2 |
| Agnes-2.0-Flash | 视觉分析 | **免费** | **免费** |
| Qwen-VL-Plus | 视觉分析（回退） | ¥0.8 | ¥2 |
| faster-whisper base | 语音转文字 | 免费（本地） | — |

典型 5 分钟视频 + 12 帧分析：**¥0**（Agnes）或 **~¥0.02**（DS+Qwen）。

## 反 Ban 策略

- 随机间隔（API 请求间 / 下载间）
- 限速（默认 5M/s）
- 浏览器请求头伪装
- 下载记录存档，避免重复下载

## Skill 集成（opencode）

本项目提供两个 opencode Skill，可在 AI 对话中直接触发：

### bilibili-pipeline Skill

触发词：`bilibili`, `B站`, `b23.tv`, `BV1`, `视频总结`, `转录`, `逐字稿`

AI 会自动执行：
1. 检查 CC 字幕（最快）或下载音频
2. faster-whisper 转录
3. DeepSeek V4 Flash 生成结构化摘要
4. 可选截图 + 多模态分析

**安装**：将 `skills/bilibili-pipeline.md` 放到 `~/.config/opencode/skills/bilibili-pipeline/SKILL.md`。  
或在 opencode 配置中添加该 skill 路径。

### book-qa Skill

触发词：`书`, `读书`, `书籍`, `PDF`, `epub`, `识别书`, `总结书`, `问书`

AI 会自动操作本地 RAG 图书问答系统：
1. `ingest.py` 导入 PDF/epub/txt → 分块 → 嵌入 → ChromaDB
2. `query.py` 语义搜索 + LLM 问答
3. `list.py` 浏览已入库图书

**安装**：将 `skills/book-qa.md` 放到 `~/.config/opencode/skills/book-qa/SKILL.md`。

## 环境变量

创建 `.env` 文件（参考 `.env.example`）：

```
DEEPSEEK_API_KEY=sk-xxx
DASHSCOPE_API_KEY=sk-xxx
AGNES_API_KEY=sk-xxx
```

## Book QA（本地 RAG 图书问答）

独立的图书问答系统，位于 `~/book-qa/`。

```bash
# 导入
python3 ingest.py book.pdf
python3 ingest.py book.epub
python3 ingest.py books/        # 批量

# 问答
python3 query.py "这本书主要讲了什么？"
python3 query.py --book "Python" "函数怎么定义"
python3 query.py --chat          # 交互模式

# 浏览
python3 list.py                  # 列出所有书
python3 list.py --summarize      # AI 摘要
```

架构：PyMuPDF/ebooklib 解析 → 800 字分块 → Bailian text-embedding-v3 嵌入 → ChromaDB 存储 → Agnes-2.0-Flash 问答。
