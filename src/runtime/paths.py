"""仓库内的路径基准。

以前每个模块各自数一遍 `Path(__file__).resolve().parents[N]`。这个写法在文件
移动时会静默出错——层级变了但数字没改，算出来的路径依然是个合法路径，
只是指向了别处。

`pantry.py` 就是这么坏的：它用的是 `parents[2]`，而同目录其他文件都是
`parents[3]`。于是它的数据目录变成了 `agents/data/kitchen-assistant`，
一个从来不存在的路径。**读返回空、写创建新目录，全程不报错。**

所以基准只留一份，谁需要就 import。
"""

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT_DIR / "data"
AGENTS_DIR = ROOT_DIR / "agents"
ASSETS_DIR = ROOT_DIR / "assets"
WEB_DIR = ROOT_DIR / "web"
