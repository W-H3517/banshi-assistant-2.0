"""扫描页表格：版面模型找表格区域，按表格线切出网格与合并单元格，OCR 逐字坐标填格。

没有可用表格线（无框线表格）时改用表格结构模型（SLANet+）给出单元格及跨度。
输出与文字页 extract_grid 相同的网格结构，后续的逻辑表合并、表头判断共用一套逻辑。
"""

import math
from statistics import median

import cv2
import numpy as np

MIN_LINE = 25         # 表格线最短像素（300dpi）
CLUSTER = 15          # 同一条表格线的位置容差（像素）：原表线条常错开数像素，表格行高远大于此
COVER = .6            # 两格之间的分隔线覆盖比例达到此值才算真正分开
PAD = 12              # 表格区域外扩像素，避免裁掉外框线
SNAP = 12             # 竖线端点与横线的最大距离（像素）
MAX_CELL = 200        # 单格字数上限：超过多半是整页文字落进一个格


def char_points(line):
    """OCR 行内每个非空白字的中心点；逐字框缺失时按行框等分估算。"""
    text, boxes = line["text"], line["boxes"]
    polygon = np.array(line["polygon"], dtype=float)
    visible = [i for i, char in enumerate(text) if not char.isspace()]
    points = []
    for order, index in enumerate(visible):
        box = boxes[index]
        if box is not None:
            # 一个识别词（如“500”）共用一个框：按字在词内的位次横向等分
            same = [i for i in visible if boxes[i] == box]
            t = (same.index(index) + .5) / len(same)
            points.append((text[index], box[0] + (box[2] - box[0]) * t, (box[1] + box[3]) / 2))
        else:
            # 行框左上→右上方向等分
            t = (order + .5) / len(visible)
            top = polygon[0] + (polygon[1] - polygon[0]) * t
            bottom = polygon[3] + (polygon[2] - polygon[3]) * t
            point = (top + bottom) / 2
            points.append((text[index], point[0], point[1]))
    return points


def skew_angle(lines):
    """按较长 OCR 行上沿的倾角估算页面倾斜（度）。"""
    angles = []
    for line in lines:
        (x0, y0), (x1, y1) = line["polygon"][0], line["polygon"][1]
        if x1 - x0 > 80:
            angles.append(math.degrees(math.atan2(y1 - y0, x1 - x0)))
    return median(angles) if angles else 0.0


def segments(mask, axis):
    """表格线段：axis=0 为横线 (位置y, 起x, 止x)，axis=1 为竖线 (位置x, 起y, 止y)。"""
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    result = []
    for x, y, w, h, _ in stats[1:count]:
        if axis == 0 and w >= MIN_LINE:
            result.append((y + h / 2, x, x + w))
        elif axis == 1 and h >= MIN_LINE:
            result.append((x + w / 2, y, y + h))
    return result


def cluster(values):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= CLUSTER:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(g) / len(g) for g in groups]


def covered(lines, position, low, high):
    """位于 position 的分隔线在 [low, high] 区间上的覆盖比例。"""
    spans = sorted((a, b) for p, a, b in lines if abs(p - position) <= CLUSTER and b > low and a < high)
    total, cursor = 0.0, low
    for a, b in spans:
        a, b = max(a, cursor), min(b, high)
        if b > a:
            total += b - a
            cursor = b
    return total / (high - low) if high > low else 0.0


def ruled_cells(gray):
    """按表格线切网格：返回 [(r0, r1, c0, c1, (x0, y0, x1, y1))]，线不足时返回 None。"""
    binary = cv2.adaptiveThreshold(255 - gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY, 15, -2)
    w = binary.shape[1]
    # 横线比任何一行文字都长；同一高度附近的横线段合起来定行线，竖线须两端顶到横线。
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                  cv2.getStructuringElement(cv2.MORPH_RECT, (max(MIN_LINE * 4, w // 15), 1)))
    horizontal = cv2.dilate(horizontal, np.ones((3, 15), np.uint8))  # 补齐扫描造成的细小断口
    hlines = segments(horizontal, 0)
    # 同一高度附近的横线段合起来算（原表横线常在列交界处错开几个像素）；
    # 至少三条贯穿大半表宽的行线才按表格线切分。合并单元格内部的短横线也是行边界。
    ys = cluster([p for p, a, b in hlines])
    if sum(covered(hlines, y, 0, w) >= .4 for y in ys) < 3:
        return None
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (1, MIN_LINE)))
    vertical = cv2.dilate(vertical, np.ones((5, 3), np.uint8))

    def touch(x, y, outward):
        """竖线端点 (x, y) 碰到的横线：按该横坐标上实际经过的横线段判断，兼容阶梯状错开的行线。

        端点可越过横线向外多伸（outward=-1 向上、+1 向下）；向格内只容许线宽误差。
        文字笔画若要越过横线就得穿线而过，因此不会误收。"""
        near = [p for p, a, b in hlines if a - SNAP <= x <= b + SNAP
                and -SNAP <= (y - p) * outward <= SNAP * 2]
        return min(near, key=lambda p: abs(p - y)) if near else None

    def ruling(line):
        # 真正的竖线两端顶到横线，且几乎占满所跨的行高；文字竖笔画在格内上下留白。
        x, a, b = line
        top, bottom = touch(x, a, -1), touch(x, b, 1)
        return top is not None and bottom is not None and bottom > top and b - a >= (bottom - top) * .85

    vlines = [line for line in segments(vertical, 1) if ruling(line)]
    xs = cluster([p for p, a, b in vlines])
    if len(xs) < 3:
        return None
    rows, cols = len(ys) - 1, len(xs) - 1
    parent = list(range(rows * cols))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols and covered(vlines, xs[c + 1], ys[r], ys[r + 1]) < COVER:
                parent[find(r * cols + c + 1)] = find(r * cols + c)
            if r + 1 < rows and covered(hlines, ys[r + 1], xs[c], xs[c + 1]) < COVER:
                parent[find((r + 1) * cols + c)] = find(r * cols + c)
    groups = {}
    for i in range(rows * cols):
        groups.setdefault(find(i), []).append(divmod(i, cols))
    cells = []
    for members in groups.values():
        r0, r1 = min(r for r, _ in members), max(r for r, _ in members)
        c0, c1 = min(c for _, c in members), max(c for _, c in members)
        cells.append((r0, r1, c0, c1, (xs[c0], ys[r0], xs[c1 + 1], ys[r1 + 1])))
    return cells


def model_cells(engine, image):
    """无框线表格：表格结构模型给出各单元格的逻辑行列与像素框。"""
    output = engine(image)
    if not output.cell_bboxes or output.cell_bboxes[0] is None or not len(output.cell_bboxes[0]):
        return None
    cells = []
    for box, (r0, r1, c0, c1) in zip(output.cell_bboxes[0], output.logic_points[0]):
        xs, ys = box[0::2], box[1::2]
        cells.append((int(r0), int(r1), int(c0), int(c1),
                      (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()))))
    return cells


def fill(cells, chars):
    """把字按中心点归入单元格，按 OCR 行序和横向位置连成文字。"""
    texts = {index: [] for index in range(len(cells))}
    for line_y, char, x, y in chars:
        for index, (*_, (x0, y0, x1, y1)) in enumerate(cells):
            if x0 <= x <= x1 and y0 <= y <= y1:
                texts[index].append((line_y, x, char))
                break
    values = []
    for index in range(len(cells)):
        ordered = sorted(texts[index])
        text = ""
        for i, (line_index, _, char) in enumerate(ordered):
            if i and line_index != ordered[i - 1][0] and text[-1:].isascii() and char.isascii():
                text += " "
            text += char
        values.append(text)
    return values


def to_grid(cells, values, to_pdf):
    """单元格列表 → 规则网格；合并单元格覆盖的每一格指向同一来源框。"""
    rows = max(cell[1] for cell in cells) + 1
    cols = max(cell[3] for cell in cells) + 1
    grid = [[{"value": "", "source_row": None, "source_column": None, "bbox": None}
             for _ in range(cols)] for _ in range(rows)]
    for (r0, r1, c0, c1, box), value in zip(cells, values):
        bbox = [round(v, 2) for v in to_pdf(box)]
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r][c] = {"value": value, "source_row": r0 + 1, "source_column": c0 + 1,
                              "bbox": bbox}
    return grid


def page_box(box, inverse, origin):
    """纠偏后裁剪图上的框 → 原页像素框（取旋转回去后四角的外接矩形）。"""
    a, b, c, d = box
    corners = np.array([[a, b, 1], [c, b, 1], [c, d, 1], [a, d, 1]], dtype=float)
    points = corners @ inverse.T + origin
    return points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()


def ruled_regions(gray):
    """按表格线找有框线表格的区域：横竖线连通后外接框够宽、且含至少三条横线。"""
    w = gray.shape[1]
    binary = cv2.adaptiveThreshold(255 - gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                   cv2.THRESH_BINARY, 15, -2)
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                  cv2.getStructuringElement(cv2.MORPH_RECT, (w // 20, 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (1, MIN_LINE * 2)))
    frame = cv2.dilate(horizontal | vertical, np.ones((5, 5), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(frame)
    regions = []
    for x, y, bw, bh, _ in stats[1:count]:
        if bw < w * .25 or bh < MIN_LINE * 3:
            continue
        lines = segments(horizontal[y:y + bh, x:x + bw], 0)
        if sum(covered(lines, row, 0, bw) >= .4 for row in cluster([p for p, a, b in lines])) >= 3:
            regions.append((x, y, x + bw, y + bh))
    return regions


def overlap(a, b):
    """两框交集占较小框的比例。"""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height / min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


def plausible(grid):
    """排除表单照片、二维码、粘贴模板等被当成表格的区域：

    不同单元格内容至少 4 个、单格不超过 200 字，且至少两行各有两个以上不同的值。
    """
    cells = {tuple(cell["bbox"]): cell["value"] for row in grid for cell in row if cell["bbox"]}
    values = [v for v in cells.values() if v]
    rows = sum(len({cell["value"] for cell in row if cell["value"]}) >= 2 for row in grid)
    return len(set(values)) >= 4 and max(map(len, values)) <= MAX_CELL and rows >= 2


def recognize_tables(image, lines, layout, structure, to_pdf):
    """一页扫描图上的全部表格：返回 [{"bbox", "grid", "column_positions", "line_ids"}]。

    image 为 RGB 页面图，lines 为同一坐标系下的 OCR 行（polygon、boxes 为像素坐标），
    to_pdf 把像素框 (x0, y0, x1, y1) 换成 PDF 坐标。line_ids 为落入表格、不再作正文的行。
    """
    array = np.asarray(image)
    found = layout(array)
    angle = skew_angle(lines)
    # 有框线的表格以表格线为准（版面模型偶尔把表格判为图片）；其余表格区域来自版面模型。
    boxes = ruled_regions(cv2.cvtColor(array, cv2.COLOR_RGB2GRAY))
    boxes += [tuple(box) for box, label in zip(found.boxes, found.class_names)
              if label == "table" and all(overlap(box, other) < .5 for other in boxes)]
    tables = []
    for box in sorted(boxes, key=lambda b: b[1]):
        x0, y0 = max(0, int(box[0]) - PAD), max(0, int(box[1]) - PAD)
        x1, y1 = min(array.shape[1], int(box[2]) + PAD), min(array.shape[0], int(box[3]) + PAD)
        inside = [i for i, line in enumerate(lines)
                  if x0 <= np.mean([p[0] for p in line["polygon"]]) <= x1
                  and y0 <= np.mean([p[1] for p in line["polygon"]]) <= y1]
        if not inside:
            continue
        crop = cv2.cvtColor(array[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
        # 纠偏：裁剪图与字坐标按同一矩阵旋转
        center = (crop.shape[1] / 2, crop.shape[0] / 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        crop = cv2.warpAffine(crop, matrix, (crop.shape[1], crop.shape[0]),
                              flags=cv2.INTER_LINEAR, borderValue=255)
        inverse = cv2.invertAffineTransform(matrix)
        chars = []
        for line_index in inside:
            points = []
            for char, cx, cy in char_points(lines[line_index]):
                px, py = matrix @ np.array([cx - x0, cy - y0, 1.0])
                points.append((char, px, py))
            # 同一 OCR 行的字共用行高位置，格内按行自上而下、行内自左向右连写
            line_y = round(float(np.mean([y for *_, y in points])))
            chars.extend((line_y, char, px, py) for char, px, py in points)
        cells = ruled_cells(crop)
        if cells is None:
            cells = model_cells(structure, cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR))
        if not cells:
            continue
        values = fill(cells, chars)

        grid = to_grid(cells, values, lambda box, inverse=inverse, origin=(x0, y0):
                       to_pdf(page_box(box, inverse, origin)))
        if len(grid) < 2 or len(grid[0]) < 2 or not plausible(grid):
            continue
        xs = sorted({cell["bbox"][0] for row in grid for cell in row if cell["bbox"]}
                    | {cell["bbox"][2] for row in grid for cell in row if cell["bbox"]})
        tables.append({"bbox": list(to_pdf((x0, y0, x1, y1))), "grid": grid,
                       "column_positions": xs, "line_ids": inside})
    return tables
