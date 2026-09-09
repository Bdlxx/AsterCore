# AsterCore 内核测试（stdlib unittest，无需额外依赖）
# 运行：python -m unittest discover -s tests -v
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
