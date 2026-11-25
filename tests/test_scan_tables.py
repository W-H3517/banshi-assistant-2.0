import cv2
import numpy as np

from backend.parsing.scan_tables import fill, ruled_cells, to_grid


def drawn_table():
    """白底黑线：3 行 3 列，首列第 2、3 行合并；格内画几笔模拟文字竖笔画。"""
    image = np.full((400, 600), 255, np.uint8)
    xs, ys = [20, 200, 400, 580], [20, 120, 250, 380]
    for y in ys:
        cv2.line(image, (xs[0], y), (xs[-1], y), 0, 2)
    for x in xs:
        cv2.line(image, (x, ys[0]), (x, ys[-1]), 0, 2)
    cv2.line(image, (xs[0] + 1, ys[2]), (xs[1] - 1, ys[2]), 255, 4)  # 擦掉首列中间横线 → 合并
    for x in (260, 290, 450):  # 文字笔画：不顶到上下横线
        cv2.line(image, (x, 140), (x, 230), 0, 2)
    return image


def test_ruled_cells_follow_lines_and_merged_cells_not_text_strokes():
    cells = ruled_cells(drawn_table())
    shape = (max(c[1] for c in cells) + 1, max(c[3] for c in cells) + 1)
    assert shape == (3, 3) and len(cells) == 8
    merged, = [c for c in cells if c[1] > c[0]]
    assert merged[:4] == (1, 2, 0, 0)


def test_fill_orders_lines_top_down_and_grid_shares_merged_box():
    cells = [(0, 0, 0, 0, (0, 0, 100, 50)), (0, 0, 1, 1, (100, 0, 200, 50)),
             (1, 1, 0, 1, (0, 50, 200, 100))]
    chars = [(10, "序", 50, 10), (30, "号", 50, 30), (10, "5", 120, 20), (10, "0", 140, 20),
             (70, "合", 60, 70), (70, "并", 160, 70)]
    values = fill(cells, chars)
    assert values == ["序号", "50", "合并"]
    grid = to_grid(cells, values, lambda box: box)
    assert grid[1][0]["bbox"] == grid[1][1]["bbox"] and grid[1][1]["value"] == "合并"
    assert grid[1][1]["source_column"] == 1
