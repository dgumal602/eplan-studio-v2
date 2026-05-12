#!/usr/bin/env python3
"""
EPLAN Template Engine — Головний рушій пошуку (Версія 5.2)
=============================================================

КОНЦЕПЦІЯ V5.2:
---------
- Відносна точність (Tolerances) замість жорстких min/max.
- Динамічний розрахунок Ratios для H (від ширини) та V (від висоти).
- Повносторінковий пошук (0-100% аркуша без сліпих зон).
"""

import json
from pathlib import Path
from typing import Optional

from models import PageLine, FoundObject
from math_parser import ExprEvaluator, eval_expr


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ЗАВАНТАЖЕННЯ ШАБЛОНІВ ТА ПІДГОТОВКА СТОРІНКИ
# ═══════════════════════════════════════════════════════════════════════════════

def load_templates(templates_dir: str) -> list[dict]:
    """Завантажує JSON-шаблони та сортує за пріоритетом (0 = найвищий)."""
    templates_path = Path(templates_dir)
    if not templates_path.exists():
        raise FileNotFoundError(f"Папку шаблонів не знайдено: {templates_dir}")

    templates = []
    for json_file in sorted(templates_path.glob("*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            tmpl = json.load(f)

        if not tmpl.get('enabled', True):
            continue

        tmpl['_file'] = json_file.name
        templates.append(tmpl)

    templates.sort(key=lambda t: t.get('priority', 50))
    return templates


def prepare_page_lines(page) -> list[PageLine]:
    """Збирає всі геометричні примітиви зі 100% площі сторінки (Специфікація V5.2)."""
    result = []
    lid = 0

    for raw_line in page.lines:
        line = PageLine.from_pdfplumber(raw_line, lid, 'line')
        if line: 
            result.append(line)
            lid += 1
            
    for raw_rect in page.rects:
        line = PageLine.from_pdfplumber(raw_rect, lid, 'rect')
        if line: 
            result.append(line)
            lid += 1
            
    for raw_curve in page.curves:
        line = PageLine.from_pdfplumber(raw_curve, lid, 'arc')
        if line: 
            result.append(line)
            lid += 1

    for raw_img in page.images:
        line = PageLine.from_pdfplumber(raw_img, lid, 'image')
        if line:
            result.append(line)
            lid += 1

    print(f"  [PAGE] Підготовлено векторів: {lid} "
          f"(H={sum(1 for l in result if l.dir=='H')}, "
          f"V={sum(1 for l in result if l.dir=='V')}, "
          f"D={sum(1 for l in result if l.dir=='D')}, "
          f"rect={sum(1 for l in result if l.dir=='rect')}, "
          f"arc={sum(1 for l in result if l.dir=='arc')}, "
          f"img={sum(1 for l in result if l.dir=='image')})")
          
    return result

def find_single_element(lines: list[PageLine], elem_def: dict, base: PageLine, template: dict, exclude_lids: set) -> Optional[PageLine]:
    """Шукає поодинокі елементи відносно знайденої бази."""
    required_dir = elem_def.get('type', 'H')
    expected_x0  = base.x0 + float(elem_def.get('x0_offset_ratio', 0.0)) * base.length
    expected_y0  = base.y0 + float(elem_def.get('y0_offset_ratio', 0.0)) * base.length
    expected_len = base.length * float(elem_def.get('length_ratio', 1.0))
    
    expected_angle = float(elem_def.get('angle', -1.0))

    xy_tol = max(get_tol(elem_def, template, 'xy_tol') * base.length, 2.5)
    lw_tol = max(get_tol(elem_def, template, 'lw_tol') * expected_len, 2.5) 

    best_match, best_dist = None, float('inf')

    for line in lines:
        if line.blocked or line.lid in exclude_lids or line.dir != required_dir:
            continue

        # === ДОДАНО 'image' до перевірки площинних фігур ===
        if required_dir in ('arc', 'rect', 'ellipse', 'image'):
            exp_w = float(elem_def.get('width_ratio', elem_def.get('length_ratio', 1.0))) * base.length
            exp_h = float(elem_def.get('height_ratio', elem_def.get('length_ratio', 1.0))) * base.length
            
            # Безпечне отримання габаритів (image та arc/rect зберігають їх трохи по-різному)
            line_w = getattr(line, 'arc_bbox_w', getattr(line, 'img_w', line.length))
            line_h = getattr(line, 'arc_bbox_h', getattr(line, 'img_h', line.length))
            
            dw, dh = abs(line_w - exp_w), abs(line_h - exp_h)
            dx, dy = abs(line.x0 - expected_x0), abs(line.y0 - expected_y0)
            lw_tol_val = max(get_tol(elem_def, template, 'lw_tol') * max(exp_w, exp_h), 2.5)
            
            if dx > xy_tol or dy > xy_tol or dw > lw_tol_val or dh > lw_tol_val: continue
            if elem_def.get('require_closed') and not getattr(line, 'is_closed', True): continue

            # === ЖОРСТКА ПЕРЕВІРКА ВЛАСТИВОСТЕЙ ЗОБРАЖЕННЯ (БЛОК MATCH) ===
            if required_dir == 'image':
                match_def = elem_def.get('match', {})
                if match_def:
                    img_tol = float(match_def.get('tolerance', 0.2))
                    
                    if 'name' in match_def and getattr(line, 'img_name', '') != match_def['name']:
                        continue
                        
                    if 'srcsize_w' in match_def:
                        sw = getattr(line, 'img_src_w', 0.0)
                        if abs(sw - float(match_def['srcsize_w'])) > float(match_def['srcsize_w']) * img_tol:
                            continue
                            
                    if 'srcsize_h' in match_def:
                        sh = getattr(line, 'img_src_h', 0.0)
                        if abs(sh - float(match_def['srcsize_h'])) > float(match_def['srcsize_h']) * img_tol:
                            continue
            # ================================================================

            if required_dir == 'arc' and hasattr(line, 'mid_rel_x'):
                all_p = []
                for cmd, *args in elem_def.get('path_ratios', []): all_p.extend(args)
                if not all_p: all_p = elem_def.get('pts_ratios', [])
                if all_p:
                    xs = [p[0] for p in all_p]
                    tw = max(xs) - min(xs)
                    if tw > 0:
                        tm_x = (sum(xs)/len(xs) - (min(xs)+max(xs))/2) / tw
                        if abs(tm_x) > 0.02 and (tm_x * line.mid_rel_x) < 0:
                            continue
                            
            dist = dx + dy + dw + dh
        else:
            dx, dy = abs(line.x0 - expected_x0), abs(line.y0 - expected_y0)
            dl = abs(line.length - expected_len)
            if dx > xy_tol or dy > xy_tol or dl > lw_tol:
                continue
                
            if required_dir == 'D' and expected_angle >= 0:
                if abs(line.angle - expected_angle) > 5.0:
                    continue
                    
            dist = dx + dy + dl

        if dist < best_dist:
            best_dist, best_match = dist, line

    return best_match
# ═══════════════════════════════════════════════════════════════════════════════
# 2. ЛОГІКА ПОШУКУ ВЕКТОРНОЇ ГЕОМЕТРІЇ (МАТЕМАТИКА V5.2)
# ═══════════════════════════════════════════════════════════════════════════════

def get_tol(elem_def: dict, template: dict, tol_key: str) -> float:
    """Отримує значення допуску (xy_tol або lw_tol)."""
    if tol_key in elem_def: return float(elem_def[tol_key])
    if f"global_{tol_key}" in template: return float(template[f"global_{tol_key}"])
    return 0.02 if tol_key == 'xy_tol' else 0.05

def find_base_candidates(lines: list[PageLine], base_def: dict, page_width: float, page_height: float, template: dict) -> list[PageLine]:
    required_dir = base_def.get('type', 'V')
    lw_tol = get_tol(base_def, template, 'lw_tol')
    candidates = []

    for line in lines:
        if line.blocked or line.dir != required_dir: continue

        if required_dir in ('H', 'V'):
            page_dim = page_width if required_dir == 'H' else page_height
            target = float(base_def.get('page_length_ratio', 0.0))
            if target == 0: continue
            if abs((line.length / page_dim) - target) <= (target * lw_tol):
                candidates.append(line)
                
        elif required_dir in ('arc', 'rect', 'ellipse', 'D'):
            target_w = float(base_def.get('page_ratio_W', base_def.get('page_length_ratio', 0.0)))
            target_h = float(base_def.get('page_ratio_H', base_def.get('page_length_ratio', 0.0)))
            if target_w == 0 or target_h == 0: continue

            if abs((line.width / page_width) - target_w) > (target_w * lw_tol) or \
               abs((line.height / page_height) - target_h) > (target_h * lw_tol):
                continue

            if base_def.get('require_closed') and not line.is_closed:
                continue

            if required_dir == 'D':
                target_angle = float(base_def.get('angle', -1.0))
                if target_angle >= 0 and abs(line.angle - target_angle) > 5.0:
                    continue

            if required_dir == 'arc' and hasattr(line, 'ends_x'):
                all_p = []
                for cmd, *args in base_def.get('path_ratios', []): all_p.extend(args)
                if not all_p: all_p = base_def.get('pts_ratios', [])
                
                if all_p:
                    xs, ys = [p[0] for p in all_p], [p[1] for p in all_p]
                    min_tx, max_tx = min(xs), max(xs)
                    min_ty, max_ty = min(ys), max(ys)
                    tw, th = max_tx - min_tx, max_ty - min_ty
                    
                    if tw > 0 and th > 0:
                        tmpl_start_x = (all_p[0][0] - min_tx) / tw
                        tmpl_end_x = (all_p[-1][0] - min_tx) / tw
                        tmpl_ends_x = (tmpl_start_x + tmpl_end_x) / 2
                        
                        tmpl_start_y = (all_p[0][1] - min_ty) / th
                        tmpl_end_y = (all_p[-1][1] - min_ty) / th
                        tmpl_ends_y = (tmpl_start_y + tmpl_end_y) / 2
                        
                        if abs(tmpl_ends_x - line.ends_x) > 0.4 or abs(tmpl_ends_y - line.ends_y) > 0.4:
                            continue

            candidates.append(line)
            
    return candidates

def collect_elements(lines: list[PageLine], elem_def: dict, base: PageLine, template: dict, exclude_lids: set, context: dict) -> list[PageLine]:
    """Збирає масив однотипних елементів у межах певної області."""
    required_dir = elem_def.get('type', 'H')
    ev = ExprEvaluator(context)
    expected_len = base.length * float(elem_def.get('length_ratio', 1.0))

    xy_tol = max(get_tol(elem_def, template, 'xy_tol') * base.length, 2.5)
    lw_tol = max(get_tol(elem_def, template, 'lw_tol') * expected_len, 2.5)

    x_range, y_range = elem_def.get('x0_in_range'), elem_def.get('y0_in_range')
    x_min = ev.eval(x_range[0]) - xy_tol if x_range else -float('inf')
    x_max = ev.eval(x_range[1]) + xy_tol if x_range else float('inf')
    y_min = ev.eval(y_range[0]) + xy_tol if y_range else -float('inf')
    y_max = ev.eval(y_range[1]) - xy_tol if y_range else float('inf')

    found = []
    for line in lines:
        if line.blocked or line.lid in exclude_lids or line.dir != required_dir: continue
        if not (x_min <= line.x0 <= x_max) or not (y_min < line.y0 < y_max): continue
        if abs(line.length - expected_len) > lw_tol: continue
        found.append(line)

    count_def = elem_def.get('count', {})
    if count_def:
        if not (count_def.get('min', 0) <= len(found) <= count_def.get('max', float('inf'))):
            return [] 

    return found


def find_pins(lines: list[PageLine], pins_def: dict, base: PageLine, context: dict) -> dict:
    """Шукає точки підключення (піни). Піни НЕ БЛОКУЮТЬСЯ."""
    if not pins_def: return {}
    ev = ExprEvaluator(context)

    max_tick_len = float(pins_def.get('max_length_ratio', 0.08)) * base.length
    search_margin = max(float(pins_def.get('search_margin_ratio', 0.02)) * base.length, 2.5)

    box_y0, box_y1 = ev.eval('anchor.y'), ev.eval('anchor.y + anchor.height')
    x_range = pins_def.get('x0_in_range')
    x_min = ev.eval(x_range[0]) - search_margin if x_range else -float('inf')
    x_max = ev.eval(x_range[1]) + search_margin if x_range else float('inf')

    pins_top, pins_bottom = [], []
    sides = pins_def.get('sides', ['top', 'bottom'])

    for line in lines:
        if line.dir != 'V' or line.length > max_tick_len or not (x_min <= line.x0 <= x_max): continue
        if 'top' in sides and (abs(line.y1 - box_y0) <= search_margin or abs(line.y0 - box_y0) <= search_margin):
            pins_top.append(line.x0)
        if 'bottom' in sides and (abs(line.y0 - box_y1) <= search_margin or abs(line.y1 - box_y1) <= search_margin):
            pins_bottom.append(line.x0)

    return {'top': sorted(set(round(x, 1) for x in pins_top)), 'bottom': sorted(set(round(x, 1) for x in pins_bottom))}


def check_global_constraints(constraints: dict, found_elements: dict, anchor: dict) -> tuple[bool, str]:
    """Перевіряє глобальні обмеження (наприклад, пропорції об'єкта)"""
    if 'aspect_ratio' in constraints:
        if anchor.get('height', 0) <= 0: return False, "Неможливо розрахувати aspect_ratio (height=0)"
        ar = anchor['width'] / anchor['height']
        ar_def = constraints['aspect_ratio']
        if not (ar_def.get('min', 0) <= ar <= ar_def.get('max', float('inf'))):
            return False, f"Aspect ratio ({ar:.2f}) поза межами."

    return True, ''

def build_full_context(found_elements: dict, anchor: dict, page_width: float, page_height: float) -> dict:
    """Формує повний словник змінних для математичного парсера."""
    ctx = {'anchor': anchor, 'page_w': page_width, 'page_h': page_height}
    
    for role, elem in found_elements.items():
        if isinstance(elem, list):
            ctx[role] = {
                'count': len(elem),
                'y0_sorted': sorted(l.y0 for l in elem),
                'x0_sorted': sorted(l.x0 for l in elem),
                'y0_min': min((l.y0 for l in elem), default=0),
                'y0_max': max((l.y0 for l in elem), default=0),
            }
        elif elem:
            entry = {'x0': elem.x0, 'y0': elem.y0, 'x1': elem.x1, 'y1': elem.y1, 'length': elem.length}
            if hasattr(elem, 'dir') and elem.dir in ('arc', 'rect', 'ellipse'):
                entry['bbox_w'] = getattr(elem, 'arc_bbox_w', elem.length)
                entry['bbox_h'] = getattr(elem, 'arc_bbox_h', elem.length)
                entry['is_closed'] = 1.0 if getattr(elem, 'is_closed', False) else 0.0
                entry['center_x'] = elem.x0 + entry.get('bbox_w', 0) / 2
                entry['center_y'] = elem.y0 + entry.get('bbox_h', 0) / 2
            ctx[role] = entry
            
    return ctx

def build_full_context(found_elements: dict, anchor: dict, page_width: float, page_height: float) -> dict:
    """Формує повний словник змінних для парсера, включаючи геометрію та розміри сторінки."""
    ctx = {'anchor': anchor, 'page_w': page_width, 'page_h': page_height}
    for role, elem in found_elements.items():
        if isinstance(elem, list):
            ctx[role] = {'count': len(elem), 'y0_sorted': sorted(l.y0 for l in elem), 'x0_sorted': sorted(l.x0 for l in elem), 'y0_min': min((l.y0 for l in elem), default=0), 'y0_max': max((l.y0 for l in elem), default=0)}
        elif elem:
            entry = {'x0': elem.x0, 'y0': elem.y0, 'x1': elem.x1, 'y1': elem.y1, 'length': elem.length}
            if hasattr(elem, 'dir') and elem.dir in ('arc', 'rect', 'ellipse'):
                entry['bbox_w'] = getattr(elem, 'arc_bbox_w', elem.length)
                entry['bbox_h'] = getattr(elem, 'arc_bbox_h', elem.length)
                entry['is_closed'] = 1.0 if getattr(elem, 'is_closed', False) else 0.0
                entry['center_x'] = elem.x0 + entry.get('bbox_w', 0) / 2
                entry['center_y'] = elem.y0 + entry.get('bbox_h', 0) / 2
            ctx[role] = entry
    return ctx

def compute_variables(variables_def: dict, full_ctx: dict) -> dict:
    result = {}
    ev = ExprEvaluator(full_ctx)
    for var_name, formula in variables_def.items():
        if var_name.startswith('_'): continue
        try:
            if formula == 'inner_h_lines.y0_sorted': result[var_name] = full_ctx.get('inner_h_lines', {}).get('y0_sorted', [])
            else: result[var_name] = ev.eval(str(formula))
            full_ctx[var_name] = result[var_name]
            ev = ExprEvaluator(full_ctx) 
        except Exception:
            result[var_name] = None
    return result

def select_variant(variants: list, full_ctx: dict) -> Optional[dict]:
    if not variants: return None
    ev = ExprEvaluator(full_ctx)
    for variant in variants:
        condition = variant.get('condition', 'true').strip()
        is_matched = False
        for op in ['>=', '<=', '!=', '>', '<', '==']:
            if op in condition:
                parts = condition.split(op, 1)
                try:
                    lval, rval = ev.eval(parts[0].strip()), ev.eval(parts[1].strip())
                    if op == '>=': is_matched = (lval >= rval)
                    elif op == '<=': is_matched = (lval <= rval)
                    elif op == '>': is_matched = (lval > rval)
                    elif op == '<': is_matched = (lval < rval)
                    elif op == '==': is_matched = (lval == rval)
                    elif op == '!=': is_matched = (lval != rval)
                except Exception: pass
                break
        else:
            try: is_matched = bool(ev.eval(condition))
            except Exception: is_matched = False
        if is_matched: return variant
    return variants[0]

# ═══════════════════════════════════════════════════════════════════════════════
# 3. ГОЛОВНИЙ ОРКЕСТРАТОР ПОШУКУ
# ═══════════════════════════════════════════════════════════════════════════════

def match_template(template: dict, lines: list[PageLine], page_width: float, page_height: float, page_num: int) -> list[FoundObject]:
    """Перевірка сторінки на відповідність шаблону (оновлено для V5.2)."""
    tmpl_name = template.get('name', 'unknown')
    geom_def = template.get('geometry', {})
    lines_def = geom_def.get('lines', [])
    if not lines_def: return []

    base_def = next((l for l in lines_def if l.get('is_base')), lines_def[0])
    # Пропускаємо image/path елементи — вони обробляються в worker._enrich_object_data
    other_defs = [l for l in lines_def if l is not base_def and l.get('type') not in ('path',)]
    
    candidates = find_base_candidates(lines, base_def, page_width, page_height, template)
    found_objects = []

    for base in candidates:
        base_len = base.length
        obj_line_ids = {base.lid}
        base_role = base_def.get('role', 'base_element')
        found_elements = {'_base': base, base_role: base}
        success = True

        for elem_def in other_defs:
            role, mode = elem_def.get('role', 'unknown'), elem_def.get('mode', 'single')
            
            if mode == 'collect':
                partial_ctx = {'base': {'x0': base.x0, 'y0': base.y0, 'length': base_len}}
                for r, e in found_elements.items():
                    if isinstance(e, PageLine): partial_ctx[r] = {'x0': e.x0, 'y0': e.y0, 'x1': e.x1, 'y1': e.y1, 'length': e.length}
                    elif isinstance(e, list) and e: partial_ctx[r] = {'count': len(e), 'y0_sorted': sorted(l.y0 for l in e), 'x0_sorted': sorted(l.x0 for l in e)}
                
                if 'top_line' in partial_ctx and 'bottom_line' in partial_ctx:
                    partial_ctx['box_y0'], partial_ctx['box_y1'] = partial_ctx['top_line']['y0'], partial_ctx['bottom_line']['y0']
                if 'left_wall' in partial_ctx and 'right_wall' in partial_ctx:
                    partial_ctx['box_x0'], partial_ctx['box_x1'] = partial_ctx['left_wall']['x0'], partial_ctx['right_wall']['x0']
                    partial_ctx['box_w'] = partial_ctx['right_wall']['x0'] - partial_ctx['left_wall']['x0']

                group = collect_elements(lines, elem_def, base, template, obj_line_ids, partial_ctx)
                if not group and elem_def.get('required', True): success = False; break
                
                found_elements[role] = group
                obj_line_ids.update(l.lid for l in group)
            else:
                elem = find_single_element(lines, elem_def, base, template, obj_line_ids)
                if elem is None and elem_def.get('required', True): success = False; break
                if elem: found_elements[role] = elem; obj_line_ids.add(elem.lid)

        if not success: continue

        anchor = {}
        for key, formula in template.get('anchor', {}).items():
            if key == 'export' or key.startswith('_'): continue
            partial_ctx = {'base': {'x0': base.x0, 'y0': base.y0, 'length': base_len}}
            for r, e in found_elements.items():
                if isinstance(e, PageLine): partial_ctx[r] = {'x0': e.x0, 'y0': e.y0, 'x1': e.x1, 'y1': e.y1, 'length': e.length}
                elif isinstance(e, list) and e: partial_ctx[r] = {'y0_sorted': sorted(l.y0 for l in e), 'count': len(e)}
            try: anchor[key] = eval_expr(formula, partial_ctx)
            except Exception: anchor[key] = 0.0

        # (Цей блок іде одразу ПІСЛЯ check_global_constraints)
        ok, _ = check_global_constraints(geom_def.get('constraints', {}), found_elements, anchor)
        if not ok: continue

        # === ФОРМУВАННЯ ПОВНОГО КОНТЕКСТУ ТА ЗБЕРЕЖЕННЯ БАЗИ ===
        full_ctx = build_full_context(found_elements, anchor, page_width, page_height)
        variables = compute_variables(template.get('variables', {}), full_ctx)
        
        # КРИТИЧНО ВАЖЛИВО: Зберігаємо знайдені координати ліній (вкл. base_element) 
        # у змінні об'єкта, щоб візуалізатор завжди будував від них, а не від якоря!
        for k, v in full_ctx.items():
            if k not in ['anchor', 'page_w', 'page_h'] and k not in variables:
                variables[k] = v

        full_ctx.update(variables)
        pins = find_pins(lines, template.get('pins', {}), base, full_ctx) if template.get('pins') else {}
        full_ctx['pins'] = pins
        
        # Вибір варіанту зі знанням про координати та page_w
        variant = select_variant(template.get('variants', []), full_ctx)

        found_objects.append(FoundObject(
            template_name = tmpl_name,
            variant_name  = variant.get('name', 'default') if variant else 'default',
            anchor        = anchor,
            variables     = variables,
            line_ids      = list(obj_line_ids),
            pins          = pins,
            page_num      = page_num,
            page_w        = page_width,
            page_h        = page_height
        ))

    return found_objects


def is_overlapping(new_obj: FoundObject, existing_objects: list[FoundObject]) -> bool:
    """Перевіряє перекриття за реальними фізичними габаритами об'єкта."""
    def get_real_bbox(obj):
        xs, ys = [], []
        # Збираємо координати з усіх розрахованих геометричних ролей
        for v in obj.variables.values():
            if isinstance(v, dict) and 'x0' in v and 'y0' in v:
                xs.extend([v['x0'], v.get('x1', v['x0'])])
                ys.extend([v['y0'], v.get('y1', v['y0'])])
        if not xs: # Fallback до якоря
            return (float(obj.anchor.get('x', 0)), float(obj.anchor.get('y', 0)), 
                    max(float(obj.anchor.get('width', 1)), 5.0), max(float(obj.anchor.get('height', 1)), 5.0))
        return min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)

    nx, ny, nw, nh = get_real_bbox(new_obj)
    for ex_obj in existing_objects:
        ex, ey, ew, eh = get_real_bbox(ex_obj)
        # Розрахунок перетину (Intersection over Union / Area)
        ix = max(nx, ex); iy = max(ny, ey)
        iw = min(nx+nw, ex+ew) - ix; ih = min(ny+nh, ey+eh) - iy
        if iw > 0 and ih > 0:
            area_i = iw * ih
            if area_i / (nw * nh) > 0.6 or area_i / (ew * eh) > 0.6:
                return True
    return False

def process_page(page, templates: list[dict], page_num: int) -> list[FoundObject]:
    """Точка входу. Лінії БЛОКУЮТЬСЯ після перевірки required service_zones."""
    lines = prepare_page_lines(page)
    all_found = []
    
    for template in templates:
        found_for_template = match_template(template, lines, page.width, page.height, page_num)
        
        for obj in found_for_template:
            # ПЕРЕВІРКА: required service_zones (до блокування)
            if check_service_zones_required(template, obj, page):
                print(f"  [SKIP] Об'єкт {obj.template_name} відкинуто (required service_zone порожнє)")
                continue
            
            if is_overlapping(obj, all_found):
                print(f"  [NMS] Відкинуто дублікат об'єкта {obj.template_name}")
                continue
            
            all_found.append(obj)
            # Блокуємо лінії підтвердженого об'єкта
            line_ids_to_block = set(getattr(obj, 'line_ids', []))
            for line in lines:
                if line.lid in line_ids_to_block:
                    line.blocked = True
    
    return all_found

def check_service_zones_required(template: dict, obj: FoundObject, page) -> bool:
    """
    Перевіряє чи всі required service_zones мають текст.
    Повертає True якщо об'єкт треба ВІДКИНУТИ (required порожнє).
    """
    service_zones = template.get('service_zones', [])
    if not service_zones:
        return False
    
    # Перевіряємо чи є required зони
    required_szs = [sz for sz in service_zones if sz.get('required', False)]
    if not required_szs:
        return False
    
    # Лінива ініціалізація - тільки якщо є required
    from evaluator import ExprEvaluator
    from worker import extract_text_by_center
    
    ctx = dict(obj.variables)
    ctx['anchor'] = obj.anchor
    ev = ExprEvaluator(ctx)
    
    for sz in required_szs:
        try:
            x0 = ev.eval(sz['x0'])
            y0 = ev.eval(sz['y0'])
            x1 = ev.eval(sz['x1'])
            y1 = ev.eval(sz['y1'])
            text = extract_text_by_center(page, x0, y0, x1, y1)
            if not text.strip():
                return True  # Відкинути
        except Exception:
            return True
    
    return False