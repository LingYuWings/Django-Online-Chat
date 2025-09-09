import sys, os, json, tempfile, subprocess, threading, textwrap
from typing import Optional, Tuple
import requests

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout,
    QHBoxLayout, QPushButton, QLineEdit, QPlainTextEdit, QLabel, QFileDialog
)
from PySide6.QtCore import Qt, QProcess

# ----------- Ollama 设置 -----------
OLLAMA_ENDPOINT = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL    = "qwen3:30b-a3b"   # 按你本地的实际 tag 改
SYSTEM_PROMPT = """你是一个能调用工具的助手。你可以选择：
1) webget(url): 用来抓取网页纯文本
2) pyrun(code): 在本地Python里运行代码并返回stdout/stderr（非交互）
只在需要时调用工具。工具调用格式必须是**单个JSON**，且独占一行：
{"tool": "webget", "args": {"url": "https://example.com"}}
或
{"tool": "pyrun", "args": {"code": "print(1+1)"}}
如果不需要工具，请直接给出最终回答。
当你得到工具结果后，会收到形如：
[tool_result name=webget] ...文本...
或
[tool_result name=pyrun] ...输出...
然后你再整合答案。注意：只输出一件事，要么工具JSON，要么最终答案。
"""

# ----------- Edge WebDriver 设置 -----------
from selenium import webdriver
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from bs4 import BeautifulSoup

# 把这个改成你的 msedgedriver 路径；如果已放 PATH，可设为 None
EDGE_DRIVER_PATH = None  # 例如 r"C:\\tools\\msedgedriver.exe" 或 "/usr/local/bin/msedgedriver"


def make_edge_driver():
    opts = EdgeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    # 你也可以添加代理、UA 等
    if EDGE_DRIVER_PATH:
        service = EdgeService(executable_path=EDGE_DRIVER_PATH)
        driver = webdriver.Edge(service=service, options=opts)
    else:
        driver = webdriver.Edge(options=opts)
    return driver


def fetch_text_via_edge(url: str, timeout: int = 30) -> str:
    driver = make_edge_driver()
    driver.set_page_load_timeout(timeout)
    driver.get(url)
    html = driver.page_source
    driver.quit()
    soup = BeautifulSoup(html, "html.parser")
    # 简单提取主体文本（示例策略，可自行优化）
    for s in soup(["script", "style", "noscript"]):
        s.extract()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:2000])  # 截断，避免太长


# ----------- 本地代码执行（子进程） -----------
def run_python_code(code: str, use_pdb: bool = False, workdir: Optional[str] = None) -> Tuple[int, str, str]:
    """
    写入临时文件，用子进程执行：
    - 普通运行: python temp.py
    - 调试运行: python -m pdb temp.py （非交互，收集首轮输出）
    返回 (exit_code, stdout, stderr)
    """
    workdir = workdir or tempfile.mkdtemp(prefix="agent_py_")
    temp_path = os.path.join(workdir, "snippet.py")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(code)

    cmd = [sys.executable, "-u"]
    if use_pdb:
        cmd += ["-m", "pdb", temp_path]
    else:
        cmd += [temp_path]

    proc = subprocess.Popen(
        cmd,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True
    )
    try:
        stdout, stderr = proc.communicate(timeout=60)  # 简化：最多等 60s
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = "", "TimeoutExpired: process killed"

    return proc.returncode, stdout, stderr


# ----------- 一个极简“Agent”回路 -----------
def chat_ollama(messages):
    """
    messages: [{"role":"system"/"user"/"assistant","content":"..."}]
    调 Ollama Chat API（Ollama 0.1+ 兼容）
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2}
    }
    r = requests.post(OLLAMA_ENDPOINT, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    # 兼容不同版本：有的在 "message":{"content":...}
    if "message" in data and "content" in data["message"]:
        return data["message"]["content"]
    # 有的直接 "content"
    return data.get("content", "")


def detect_tool_call(text: str) -> Optional[dict]:
    """
    只解析**独占一行**的JSON（见 system 提示）。
    找到就返回 dict，否则 None
    """
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "tool" in obj and "args" in obj:
                    return obj
            except Exception:
                pass
    return None


# ----------- UI -----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Local LLM 能力增强（Edge抓取 + 本地Python运行/调试 + Ollama Qwen3）")
        self.resize(1100, 800)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # --- Tab 1: LLM（带工具） ---
        self.tab_llm = QWidget()
        self.tabs.addTab(self.tab_llm, "LLM（自动用工具）")
        self._init_tab_llm()

        # --- Tab 2: 网页抓取 ---
        self.tab_web = QWidget()
        self.tabs.addTab(self.tab_web, "网页抓取（Edge）")
        self._init_tab_web()

        # --- Tab 3: 本地代码 ---
        self.tab_code = QWidget()
        self.tabs.addTab(self.tab_code, "本地代码运行/调试")
        self._init_tab_code()

        # 会话消息
        self.conv = [{"role": "system", "content": SYSTEM_PROMPT}]

    # ------ Tab: LLM ------
    def _init_tab_llm(self):
        lay = QVBoxLayout(self.tab_llm)

        h = QHBoxLayout()
        self.llm_input = QLineEdit()
        self.llm_input.setPlaceholderText("问本地 LLM（它会在需要时自动调用 webget/pyrun）")
        btn_send = QPushButton("发送")
        btn_send.clicked.connect(self.on_llm_send)
        h.addWidget(QLabel("问题："))
        h.addWidget(self.llm_input, 1)
        h.addWidget(btn_send)
        lay.addLayout(h)

        self.llm_log = QPlainTextEdit()
        self.llm_log.setReadOnly(True)
        lay.addWidget(self.llm_log, 1)

    def log_llm(self, s: str):
        self.llm_log.appendPlainText(s)

    def on_llm_send(self):
        user_msg = self.llm_input.text().strip()
        if not user_msg:
            return
        self.llm_input.clear()
        self.log_llm(f"[user] {user_msg}")
        self.conv.append({"role": "user", "content": user_msg})

        def worker():
            # 第一次询问
            reply = chat_ollama(self.conv)
            self.conv.append({"role": "assistant", "content": reply})
            self.log_llm(f"[assistant raw]\n{reply}\n")

            # 看看是否工具调用
            tool = detect_tool_call(reply)
            if not tool:
                self.log_llm("[assistant]（最终回答，无需工具）\n" + reply)
                return

            name = tool.get("tool")
            args = tool.get("args", {})
            if name == "webget":
                url = args.get("url", "")
                try:
                    text = fetch_text_via_edge(url)
                    tool_res = f"[tool_result name=webget] URL={url}\n{text[:4000]}"
                except Exception as e:
                    tool_res = f"[tool_result name=webget] ERROR: {e}"
            elif name == "pyrun":
                code = args.get("code", "")
                rc, out, err = run_python_code(code, use_pdb=False)
                tool_res = f"[tool_result name=pyrun] exit={rc}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
            else:
                tool_res = f"[tool_result name=unknown] {tool}"

            # 把工具结果发给模型，请它给最终答案
            self.conv.append({"role": "user", "content": tool_res})
            self.log_llm(tool_res)
            final = chat_ollama(self.conv)
            self.conv.append({"role": "assistant", "content": final})
            self.log_llm("\n[assistant 最终回答]\n" + final + "\n")

        threading.Thread(target=worker, daemon=True).start()

    # ------ Tab: Web ------
    def _init_tab_web(self):
        lay = QVBoxLayout(self.tab_web)

        h = QHBoxLayout()
        self.url_edit = QLineEdit("https://example.com")
        btn_fetch = QPushButton("抓取")
        btn_fetch.clicked.connect(self.on_fetch_url)
        h.addWidget(QLabel("URL："))
        h.addWidget(self.url_edit, 1)
        h.addWidget(btn_fetch)
        lay.addLayout(h)

        self.web_output = QPlainTextEdit()
        lay.addWidget(self.web_output, 1)

    def on_fetch_url(self):
        url = self.url_edit.text().strip()
        self.web_output.setPlainText("抓取中…")
        def worker():
            try:
                text = fetch_text_via_edge(url)
                self.web_output.setPlainText(text)
            except Exception as e:
                self.web_output.setPlainText(f"ERROR: {e}")
        threading.Thread(target=worker, daemon=True).start()

    # ------ Tab: Code ------
    def _init_tab_code(self):
        lay = QVBoxLayout(self.tab_code)

        # 代码编辑器
        self.code_edit = QPlainTextEdit()
        self.code_edit.setPlainText(textwrap.dedent("""\
            print("hello from local python!")
            x = 1 + 2
            print("x =", x)
            # 想要调试：可以加 breakpoint()，并在“调试运行”启动
        """))
        lay.addWidget(self.code_edit, 1)

        # 控制区
        h = QHBoxLayout()
        btn_run = QPushButton("运行")
        btn_dbg = QPushButton("调试运行（pdb，非交互简版）")
        btn_run.clicked.connect(self.on_run_code)
        btn_dbg.clicked.connect(lambda: self.on_run_code(debug=True))
        h.addWidget(btn_run)
        h.addWidget(btn_dbg)
        lay.addLayout(h)

        # 输出
        self.code_output = QPlainTextEdit()
        self.code_output.setReadOnly(True)
        lay.addWidget(self.code_output, 1)

    def on_run_code(self, debug: bool = False):
        code = self.code_edit.toPlainText()
        self.code_output.setPlainText("运行中…")
        def worker():
            rc, out, err = run_python_code(code, use_pdb=debug)
            self.code_output.setPlainText(
                f"exit={rc}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
            )
        threading.Thread(target=worker, daemon=True).start()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
