<div align="center">

# render-strokes-order
**从 QuickDraw 读取草图笔画，为每笔赋予颜色并标注序号。类别数据按批复用，支持断点续跑与多进程绘制。**

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)





<img src="assets/banner.png" width="600" alt="render-strokes-order banner">

<img src="assets/cute_narwhal_stroke_by_stroke.svg" width="600" alt="逐笔绘制示意：每笔带颜色与序号">
</div>

## <img src="assets/characters/requirements.png" width="26" height="26" alt="小熊图标"> 环境要求

Python 3.9+：

```bash
pip install numpy matplotlib tqdm
```

## <img src="assets/characters/data.png" width="26" height="26" alt="兔子图标"> 数据格式

数据目录 `/workspace/data/datasets/quickdraw/`，每个类别一个 `.npz` 文件（如 `bee.npz`）。文件内的 `test` 键为 object 数组，每条草图由若干笔画组成，单笔形状为 `(点数, 3)`，即 `[dx, dy, pen_flag]`，其中 `pen_flag=1` 表示抬笔。

## <img src="assets/characters/run.png" width="26" height="26" alt="狐狸图标"> 运行方式

```bash
python render_strokes.py
```

可调参数集中在 `render_strokes.py` 的 `params` 字典：

```python
BATCH_SIZE = 100
params = {
    'data_dir': '/workspace/data/datasets/quickdraw',
    'categories': sorted(["bee", "bus", "flower", "giraffe", "pig"]),
    'total_batches': 10,
    'batch_size': BATCH_SIZE,
    'use_category_offset': True,
    'output_dir': 'outputs',
}
main(**params)
```

## <img src="assets/characters/params.png" width="26" height="26" alt="章鱼图标"> 参数说明

| 参数 | 含义 |
|---|---|
| `data_dir` | QuickDraw `.npz` 文件目录 |
| `categories` | 绘制类别，运行时按字母排序 |
| `total_batches` | 每个类别的批次数 |
| `batch_size` | 每批图片数，网格 10 列，行数自动 |
| `use_category_offset` | `True` 编号跨类别连续，`False` 每类从 1 开始 |
| `output_dir` | 文件根目录，相对路径按脚本目录解析 |

## <img src="assets/characters/notes.png" width="26" height="26" alt="熊猫图标"> 说明

项目代码采用 MIT 协议；横幅与章节图标使用 Twemoji 素材（CC-BY 4.0，需署名），SVG 示意图为本地原创，详见 `LICENSE`。
