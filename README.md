# 中国现代文学馆 · 与茅盾对话

观众打开网页，用文字或麦克风提问。程序在本机资料里查找，需要组织回答时调用 DeepSeek。同一段检查过的文字既显示在页面上，也送去朗读。

这是数字化模拟，不是茅盾本人。

完整的功能位置、修改方法和故障排查见 [docs/交接说明.md](docs/交接说明.md)。

## 准备

在项目文件夹中执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

密钥只填项目根目录的 `.env`。示例文件 `.env.example` 不会被程序读取。不要把密钥写进网页文件，也不要提交到版本记录。

## 资料怎样放

| 内容 | 位置 | 说明 |
| --- | --- | --- |
| 原始表格 / Word | `data/sources/files/`，登记在 `catalog.json` | 馆方资料原文；改表后可重新“生成” |
| 知识包正文 | `data/knowledge-pack/`（`nodes.jsonl` 等） | 从原始资料整理出的可检索正文；别人克隆后可直接“加载”，不必先有 Excel 工具链以外的步骤 |
| 复刻音色 | `data/voices/`，参考录音还有根目录 `md.wma` | 阿里云复刻用的参考音与 `maodun.json` 里的 voice_id |
| 会话库 | `data/maodun.db` | 运行后自动生成：本机聊天记录和已加载的知识索引。空库即可，不随仓库分发 |

克隆本仓库后，优先：

```bash
.venv/bin/python scripts/使用.py 加载
```

若只有原始表格、没有知识包，再从表格生成：

```bash
.venv/bin/python scripts/使用.py 生成
```

增加或更换某一份已登记资料时，用“增加”或“更新”，并写上登记里的标识，例如 `memoir` 或 `museum`。聊天记录不会被删掉。网站如果已经开着，下一次提问会改用新资料。

环境是否齐备：

```bash
.venv/bin/python scripts/使用.py 检查
```

## 启动和停止

```bash
.venv/bin/python scripts/使用.py 启动
```

也可以直接：

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。资料核对页是 <http://127.0.0.1:8000/admin>。从资料页返回对话，会回到刚才那段聊天。

本机聊天记录写在 `data/maodun.db`（首次运行自动创建）。完整问答按轮次保存；同一会话变长后，程序会保留近期原文、整理较早摘要，并在需要时找回更早原文。详细见 [docs/交接说明.md](docs/交接说明.md)。

停止时，在运行上面这条命令的终端按 Ctrl+C。

改 `.env` 或 Python 程序后需要重新启动。改网页文字和样式后，刷新浏览器。改 `data/persona/` 里的人物规则和风格样本，保存后下一次提问即生效。

## 检查

```bash
.venv/bin/python -m pytest -q
```

自动检查只说明程序没有改坏。回答的质量要真实提问后人工核对：

```bash
.venv/bin/python scripts/对话检查.py --file docs/验收问题.txt
.venv/bin/python scripts/使用.py 诊断
```

第一条不经过网页连续提问，会真实调用 DeepSeek，不写聊天记录。第二条查看网页上最近几轮问答的处理经过。两者都只给维护者用。
