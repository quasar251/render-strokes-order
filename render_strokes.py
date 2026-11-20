"""按类别加载 QuickDraw 笔画数据，并按批次绘制带笔画序号的草图网格。"""

import json
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from multiprocessing import Value

import matplotlib.pyplot as plt
import numpy as np
from tqdm.auto import tqdm

CMAP = plt.get_cmap('tab20')
COLORS = [CMAP(i) for i in range(CMAP.N)]


@dataclass
class RunConfig:
    """一次运行的完整配置，可直接写入断点文件。"""

    categories: list
    total_batches: int
    data_dir: str
    checkpoint_file: str
    use_category_offset: bool
    output_dir: str
    batch_size: int = 100


def _resolve_output_dir(output_dir):
    """相对路径按脚本所在目录解析，保证任意工作目录下结果位置一致。"""
    if os.path.isabs(output_dir):
        return output_dir
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), output_dir)


def get_strokes_group(stroke_lengths):
    """按相邻笔画长度差分组，返回每一笔所属的组号。"""
    if len(stroke_lengths) == 1:
        return [0]

    stroke_groups = []
    group_id = 0
    stroke_count = len(stroke_lengths)
    base_index = 0
    current_index = 1
    while current_index < stroke_count:
        # 相邻笔画长度差超过 5 就换组，让长度接近的相邻笔画颜色一致
        if abs(stroke_lengths[base_index] - stroke_lengths[current_index]) > 5:
            stroke_groups.append(group_id)
            base_index = current_index
            group_id += 1
        else:
            stroke_groups.append(group_id)
        current_index += 1
        if current_index == stroke_count:
            stroke_groups.append(group_id)

    return stroke_groups


def normalize(dataset):
    """把原始笔画序列归一化到 [-1, 1]。"""
    return [sketch_norm(sketch) for sketch in dataset]


def sketch_norm(sketch):
    """累加增量坐标并等比缩放。"""
    # 原始数据存的是相邻点增量，先累加成绝对坐标；第三列是抬笔标记，不参与变换
    normalized_sketch = sketch.astype(np.float32)
    normalized_sketch[:, :2] = np.cumsum(normalized_sketch[:, :2], axis=0)

    width_max = np.max(normalized_sketch[:, 0], axis=0)
    width_min = np.min(normalized_sketch[:, 0], axis=0)
    height_max = np.max(normalized_sketch[:, 1], axis=0)
    height_min = np.min(normalized_sketch[:, 1], axis=0)

    width_scale = width_max - width_min
    height_scale = height_max - height_min
    # 按较长边等比缩放，避免图形拉伸变形
    scale = width_scale if width_scale > height_scale else height_scale

    if scale == 0:
        # 单点或所有点重合时没有可比尺度，直接落在原点，避免 NaN
        normalized_sketch[:, 0] = 0
        normalized_sketch[:, 1] = 0
    else:
        normalized_sketch[:, 0] = (normalized_sketch[:, 0] - width_min) / scale * 2 - 1
        normalized_sketch[:, 1] = (normalized_sketch[:, 1] - height_min) / scale * 2 - 1
    return normalized_sketch


def draw_sketch(ax, strokes, stroke_groups):
    """在子图上绘制单张草图，每隔 3 个笔画点标注一次笔画序号。"""
    limit = 1.05
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-limit, limit)

    for stroke_index, stroke in enumerate(strokes):
        # 同组笔画同色；取模避免分组数超过颜色表长度
        color = COLORS[stroke_groups[stroke_index] % len(COLORS)]
        # 原始数据 y 轴向下，取负后符合 matplotlib 的 y 轴方向
        ax.plot(stroke[:, 0], -stroke[:, 1], color=color)
        for point_index in range(0, len(stroke)):
            # 只在落笔点（标记为 0）且每 3 个点标注一次序号
            if stroke[point_index][2] == 0 and point_index % 3 == 0:
                ax.text(
                    stroke[point_index, 0], -stroke[point_index, 1],
                    str(stroke_index), fontsize=5,
                )

    ax.set_xticks([])
    ax.set_yticks([])


def strokes_split_group(strokes):
    """按抬笔标记切分笔画，并返回每笔长度和分组。"""
    # 第三列为 1 表示抬笔，在该点之后切开得到独立笔画
    split_indices = np.where(strokes[:, 2] == 1)[0] + 1
    split_strokes = np.split(strokes, split_indices[:-1])
    stroke_lengths = [len(stroke) for stroke in split_strokes]
    stroke_groups = get_strokes_group(stroke_lengths)

    return split_strokes, stroke_lengths, stroke_groups


class SketchDrawer:
    """每个类别只加载一次数据，并按批次绘制。"""

    def __init__(self, number_offset, category, data_dir,
                 use_category_offset=True, output_dir='outputs'):
        # number_offset 是类别编号偏移，用于让图片编号跨类别连续
        self.number_offset = number_offset
        # 关闭后每种编号都从 1 开始，不再叠加类别偏移
        self.use_category_offset = use_category_offset
        self.category = category
        self.output_dir = _resolve_output_dir(output_dir)
        # pickle 保存的 object 数组需要 latin1 解码；加载一次后反复复用
        test_data = np.load(
            f'{data_dir}/{category}.npz', allow_pickle=True, encoding='latin1'
        )['test']
        self.test_data = test_data
        self.fig = None
        self.axes = None
        self._grid_key = None

    def draw_batch(self, batch_index, batch_size=100, dpi=260,
                   show_progress=True, progress=None):
        """绘制第 batch_index 批并保存为 PNG，progress 用于跨进程上报进度。"""
        start_index = batch_index * batch_size
        normalized_sketches = normalize(
            self.test_data[start_index:start_index + batch_size]
        )

        fig, axes = self._prepare_axes(len(normalized_sketches))
        if show_progress:
            print(f'drawing {self.category}-{batch_index}')
        self._draw_sketches(
            axes, normalized_sketches, start_index, show_progress, progress
        )
        self._save_batch(fig, batch_index, dpi)

    def _prepare_axes(self, sketch_count):
        """准备网格画布：必要时创建，清空并隐藏多余子图。"""
        column_count = 10
        row_count = max(1, (sketch_count + column_count - 1) // column_count)
        grid_key = (row_count, column_count)
        # 画布只创建一次，后续批次清空后复用，省去反复创建多个子图
        if self.fig is None or self._grid_key != grid_key:
            self.fig, axes = plt.subplots(
                row_count, column_count,
                figsize=(2 * column_count, 2 * row_count),
            )
            self.fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.98)
            self.axes = np.atleast_1d(axes).ravel()
            self._grid_key = grid_key

        for ax in self.axes:
            ax.cla()
            ax.set_visible(True)
        # 最后一批不足一轮时，隐藏多余子图
        for ax in self.axes[sketch_count:]:
            ax.set_visible(False)
        return self.fig, self.axes

    def _draw_sketches(self, axes, normalized_sketches, start_index,
                       show_progress, progress):
        """逐张绘制草图并写子图标题。"""
        sketch_count = len(normalized_sketches)
        sketch_indices = (
            tqdm(range(sketch_count)) if show_progress else range(sketch_count)
        )
        offset = self.number_offset if self.use_category_offset else 0
        for sketch_index in sketch_indices:
            split_strokes, _, stroke_groups = strokes_split_group(
                normalized_sketches[sketch_index]
            )
            draw_sketch(axes[sketch_index], split_strokes, stroke_groups)
            # 每张子图的序号 = 批偏移 + 批内序号 + （可选）类别偏移
            axes[sketch_index].set_title(
                str(start_index + sketch_index + 1 + offset),
                fontsize=9, pad=1,
            )
            if progress is not None:
                # 子进程每画完一张就累加共享计数，父进程统一刷新进度条
                with progress.get_lock():
                    progress.value += 1

    def _save_batch(self, fig, batch_index, dpi):
        """把当前画布保存为该类别的第 batch_index 张 PNG。"""
        output_path = os.path.join(
            self.output_dir, self.category, f'{self.category}-{batch_index+1}.png'
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        # 低压缩级别可加快 PNG 写入，画面内容不变
        fig.savefig(output_path, dpi=dpi, pil_kwargs={'compress_level': 1})

    def close(self):
        """释放复用的画布。"""
        if self.fig is not None:
            plt.close(self.fig)
            self.fig = None
            self.axes = None
            self._grid_key = None


_CONFIG = None
_PROGRESS = None
_WORKER_STATE = {'key': None, 'drawer': None}


def _init_worker(config, progress):
    """初始化工作进程：保存配置和共享进度计数器。"""
    global _CONFIG, _PROGRESS
    _CONFIG = config
    _PROGRESS = progress


def _draw_batch_worker(task):
    """子进程绘制入口；按类别切换绘制器，避免每个类别重复加载数据。"""
    number_offset, category, batch_index = task
    key = (number_offset, category)
    if _WORKER_STATE['key'] != key:
        _WORKER_STATE['drawer'] = SketchDrawer(
            number_offset, category, _CONFIG.data_dir,
            _CONFIG.use_category_offset, _CONFIG.output_dir,
        )
        _WORKER_STATE['key'] = key
    _WORKER_STATE['drawer'].draw_batch(
        batch_index, batch_size=_CONFIG.batch_size,
        show_progress=False, progress=_PROGRESS,
    )


def _load_checkpoint(checkpoint_file):
    """读取断点文件；文件缺失、损坏或结构异常时返回空记录。"""
    if not os.path.exists(checkpoint_file):
        return {'categories': {}}
    try:
        with open(checkpoint_file, 'r', encoding='utf-8') as checkpoint:
            data = json.load(checkpoint)
    except (OSError, json.JSONDecodeError):
        return {'categories': {}}

    if not isinstance(data, dict) or not isinstance(data.get('categories'), dict):
        return {'categories': {}}
    for category, state in data['categories'].items():
        completed = state.get('completed_batches') if isinstance(state, dict) else None
        if (not isinstance(completed, list)
                or not all(isinstance(index, int) and not isinstance(index, bool)
                           for index in completed)
                or not isinstance(state.get('done'), bool)):
            return {'categories': {}}
    if 'params' in data and not isinstance(data['params'], dict):
        data.pop('params')
    return data


def _save_checkpoint(checkpoint_file, checkpoint):
    """先写临时文件再替换，避免中断时写坏断点文件。"""
    temp_file = f'{checkpoint_file}.tmp'
    with open(temp_file, 'w', encoding='utf-8') as output:
        json.dump(checkpoint, output, ensure_ascii=False, indent=2)
    os.replace(temp_file, checkpoint_file)


def _mark_batch_done(checkpoint, checkpoint_file, category, batch_index):
    """记录已完成批次并立即落盘，只有成功生成的批次才会记录。"""
    category_state = checkpoint['categories'].setdefault(
        category, {'completed_batches': [], 'done': False}
    )
    if batch_index not in category_state['completed_batches']:
        category_state['completed_batches'].append(batch_index)
        category_state['completed_batches'].sort()
    _save_checkpoint(checkpoint_file, checkpoint)


def _count_sketches(drawer, batch_indices, batch_size=100):
    """统计给定批次的实际图片数，最后一批可能不足 batch_size 张。"""
    return sum(
        min(batch_size, len(drawer.test_data[
            batch_index * batch_size:(batch_index + 1) * batch_size
        ]))
        for batch_index in batch_indices
    )


def _compute_number_offsets(config):
    """按每个类别的实际样本数累加出编号偏移。"""
    if not config.use_category_offset:
        return {category: 0 for category in config.categories}

    category_sizes = {}
    for category in config.categories:
        drawer = SketchDrawer(
            0, category, config.data_dir, config.use_category_offset,
            config.output_dir,
        )
        test_data = drawer.test_data
        category_sizes[category] = len(test_data)
        drawer.close()

    offsets = {}
    cumulative = 0
    for category in config.categories:
        offsets[category] = cumulative
        cumulative += category_sizes[category]
    return offsets


def _watch_progress(progress, progress_bar, stop_event):
    """后台线程把子进程累加的计数同步到唯一的进度条。"""
    last_reported = 0
    while not stop_event.is_set():
        with progress.get_lock():
            completed = progress.value
        if completed > last_reported:
            progress_bar.update(completed - last_reported)
            last_reported = completed
        time.sleep(0.05)
    # 退出前做最后一次同步，避免漏掉末尾的计数
    with progress.get_lock():
        completed = progress.value
    if completed > last_reported:
        progress_bar.update(completed - last_reported)


def _draw_batches_parallel(config, drawer, pool, number_offset, category,
                           pending_batches, checkpoint, progress):
    """复用同一个进程池绘制指定批次，成功一批就记一批断点。"""
    total = _count_sketches(drawer, pending_batches, config.batch_size)
    progress_bar = tqdm(total=total, desc=category)
    stop_event = threading.Event()
    with progress.get_lock():
        progress.value = 0
    watcher = threading.Thread(
        target=_watch_progress,
        args=(progress, progress_bar, stop_event),
        daemon=True,
    )
    watcher.start()

    try:
        batch_futures = {
            pool.submit(_draw_batch_worker, (number_offset, category, batch_index)):
                batch_index
            for batch_index in pending_batches
        }
        for batch_future in as_completed(batch_futures):
            batch_index = batch_futures[batch_future]
            batch_future.result()
            # 只有成功生成才记录，失败的批次下次会重试
            _mark_batch_done(checkpoint, config.checkpoint_file, category, batch_index)
    finally:
        stop_event.set()
        watcher.join()
        progress_bar.close()


def main(categories, total_batches, data_dir,
         checkpoint_file=None, use_category_offset=True, output_dir='outputs',
         batch_size=100):
    """按类别绘制 total_batches 批，支持中断后从断点继续。"""
    output_dir = _resolve_output_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    # 断点文件默认放在输出目录里，便于和图片一起提交
    checkpoint_file = checkpoint_file or os.path.join(output_dir, 'checkpoint.json')
    config = RunConfig(
        categories=sorted(categories),
        total_batches=total_batches,
        data_dir=data_dir,
        checkpoint_file=checkpoint_file,
        use_category_offset=use_category_offset,
        output_dir=output_dir,
        batch_size=batch_size,
    )

    checkpoint = _load_checkpoint(checkpoint_file)
    current_params = asdict(config)
    saved_params = checkpoint.get('params')
    if saved_params is None:
        checkpoint['params'] = current_params
    elif saved_params != current_params:
        # 保留生成已有图片时的参数，避免断点记录与实际图片不符
        print(f'警告：当前参数与断点记录不一致，断点仍按 {saved_params} 解释')
        print(f'如需按新参数重新生成，请删除 {checkpoint_file}')
    _save_checkpoint(checkpoint_file, checkpoint)

    number_offsets = _compute_number_offsets(config)
    pending_by_category = {}
    for category in config.categories:
        category_state = checkpoint['categories'].setdefault(
            category, {'completed_batches': [], 'done': False}
        )
        pending_by_category[category] = [
            batch_index for batch_index in range(config.total_batches)
            if batch_index not in category_state['completed_batches']
        ]

    # 所有类别共用一个进程池，减少反复启动进程的开销
    need_pool = any(len(batches) > 1 for batches in pending_by_category.values())
    progress = Value('i', 0)
    pool = None
    if need_pool:
        pool = ProcessPoolExecutor(
            max_workers=min(config.total_batches, os.cpu_count() or 1),
            initializer=_init_worker,
            initargs=(config, progress),
        )

    try:
        for category in config.categories:
            category_state = checkpoint['categories'][category]
            pending_batches = pending_by_category[category]
            if not pending_batches:
                # 该类别已全部生成，直接跳过
                if not category_state['done']:
                    category_state['done'] = True
                    _save_checkpoint(checkpoint_file, checkpoint)
                print(f'{category} 已完成，跳过')
                continue

            drawer = SketchDrawer(
                number_offsets[category], category, config.data_dir,
                config.use_category_offset, config.output_dir,
            )
            try:
                if len(pending_batches) == 1:
                    batch_index = pending_batches[0]
                    drawer.draw_batch(batch_index, batch_size=config.batch_size)
                    _mark_batch_done(
                        checkpoint, config.checkpoint_file, category, batch_index
                    )
                else:
                    _draw_batches_parallel(
                        config, drawer, pool, number_offsets[category], category,
                        pending_batches, checkpoint, progress,
                    )
                if all(index in category_state['completed_batches']
                       for index in range(config.total_batches)):
                    # 类别全部完成后记录一次完成标记
                    category_state['done'] = True
                    _save_checkpoint(checkpoint_file, checkpoint)
            finally:
                drawer.close()
    finally:
        if pool is not None:
            pool.shutdown(wait=True)


if __name__ == '__main__':
    # 备选类别：
    # ["airplane", "angel", "alarm_clock", "apple", "butterfly", "belt", "bus",
    #  "cake", "cat", "clock", "eye", "fish", "pig", "sheep", "spider",
    #  "great_wall", "umbrella"]
    # 所有超参数集中在这里，键名与 main 的形参一致，通过 main(**params) 传入
    params = {
        # 数据集目录：每个类别对应一个 <类别>.npz 文件
        'data_dir': '/workspace/data/datasets/quickdraw',

        # 要绘制的类别；会按字母排序，类别编号偏移也按排序后的下标计算
        'categories': sorted(["bee", "bus", "flower", "giraffe", "pig"]),

        # 每个类别绘制的批次数；断点由 checkpoint.json 续跑
        'total_batches': 10,

        # 每批图片数，默认100，即10*10的方格图
        'batch_size': 100,

        # 子图编号是否跨类别连续：
        #   True  -> 每个类别从上一个类别的样本总数之后继续编号
        #   False -> 每个类别都从 1 开始
        'use_category_offset': True,

        # 生成文件根目录，里面放 checkpoint.json 和 <类别>/<类别>-<批次>.png
        'output_dir': 'outputs',
    }
    main(**params)
