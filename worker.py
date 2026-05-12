# worker.py
from PyQt6.QtCore import QThread, pyqtSignal
import pdfplumber
import template_engine as te
from evaluator import ExprEvaluator
from PyQt6.QtGui import QImage
import fitz
import math

def extract_text_by_center(page, x0, y0, x1, y1, multiline=False, join_char='\n'):
    """Універсальна функція: OCR за центрами символів з підтримкою вертикального тексту."""
    x0_c, y0_c = max(0, min(x0, x1)), max(0, min(y0, y1))
    x1_c, y1_c = min(page.width, max(x0, x1)), min(page.height, max(y0, y1))
    if x1_c <= x0_c or y1_c <= y0_c: return ""
        
    try:
        valid_chars = []
        for char in page.chars:
            cx, cy = (char['x0'] + char['x1']) / 2.0, (char['top'] + char['bottom']) / 2.0
            if x0_c <= cx <= x1_c and y0_c <= cy <= y1_c:
                valid_chars.append(char)
                
        if not valid_chars: return ""
        
        
        
        # Визначаємо орієнтацію тексту за більшістю символів
        upright_count = sum(1 for c in valid_chars if c.get('upright', True))
        is_vertical = upright_count < len(valid_chars) / 2
        
        import pdfplumber.utils
        
        if is_vertical:
            # Вертикальний текст — збираємо посимвольно
            # matrix[1] > 0 → текст знизу вгору (90° CCW)
            first_matrix = valid_chars[0].get('matrix', (1, 0, 0, 1, 0, 0))
            bottom_to_top = first_matrix[1] > 0
            
            # Групуємо символи по колонках (однаковий x0)
            from collections import defaultdict
            columns = defaultdict(list)
            for c in valid_chars:
                col_key = round(c['x0'] / 4) * 4
                columns[col_key].append(c)
            
            final_lines = []
            for col_key in sorted(columns.keys()):
                col_chars = columns[col_key]
                # Сортуємо по top: знизу вгору = reverse (більший top = нижче = перший символ)
                col_chars.sort(key=lambda c: c['top'], reverse=bottom_to_top)
                text = ''.join(c['text'] for c in col_chars)
                if text.strip():
                    final_lines.append(text)
            
            if multiline: return join_char.join(final_lines).strip()
            else: return ' '.join(final_lines).strip()
        else:
            # Горизонтальний текст — стандартна логіка
            words = pdfplumber.utils.extract_words(valid_chars, x_tolerance=3, y_tolerance=3, keep_blank_chars=False)
            if not words: return ""
                
            words.sort(key=lambda w: w['top'])
            lines = []
            current_line = [words[0]]
            
            for w in words[1:]:
                if abs(w['top'] - current_line[0]['top']) <= 4:
                    current_line.append(w)
                else:
                    lines.append(current_line)
                    current_line = [w]
            lines.append(current_line)
            
            final_lines = []
            for line in lines:
                line.sort(key=lambda w: w['x0'])
                final_lines.append(' '.join(w['text'] for w in line))
                
            if multiline: return join_char.join(final_lines).strip()
            else: return ' '.join(final_lines).strip()
            
    except Exception as e:
        print(f"[OCR Center Error] {e}")
        return ""


class ThumbnailWorker(QThread):
    thumbnail_ready = pyqtSignal(int, QImage)
    def __init__(self, pdf_path, max_width=150):
        super().__init__()
        self.pdf_path, self.max_width, self.is_running = pdf_path, max_width, True

    def run(self):
        try:
            doc = fitz.open(self.pdf_path)
            for page_num in range(len(doc)):
                if not self.is_running: break
                page = doc.load_page(page_num)
                zoom = self.max_width / page.rect.width
                matrix = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=matrix)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
                self.thumbnail_ready.emit(page_num, img.copy())
            doc.close()
        except Exception as e: print(f"Помилка генерації мініатюр: {e}")
    def stop(self): self.is_running = False

class BaseWorker(QThread):
    """Базовий клас із єдиною логікою збагачення об'єктів графікою та OCR."""
    def _enrich_object_data(self, obj, tmpl, page):
        obj.page_w, obj.page_h = float(page.width), float(page.height)
        ax, ay = float(obj.anchor.get('x', 0)), float(obj.anchor.get('y', 0))
        aw = max(float(obj.anchor.get('width', 1)), 1.0)
        
        ctx = dict(obj.variables)
        ctx['anchor'] = obj.anchor


        # === ОБЧИСЛЕННЯ VARIABLES ===
        # variables можуть бути:
        #   1) число чи рядок ("5", "'PLC_001'")
        #   2) формула ("right_wall.x0 - base_element.x0")
        #   3) старий формат {"name": "expr"} → новий {"name": {"expr": "...", "export": true}}
        from evaluator import ExprEvaluator as ExprEvalString
        computed_vars = {}
        for var_name, var_def in tmpl.get('variables', {}).items():
            # Backward compatibility
            if isinstance(var_def, str):
                expr = var_def
            elif isinstance(var_def, dict):
                expr = var_def.get('expr', '')
            else:
                continue
            
            if not expr:
                computed_vars[var_name] = ""
                ctx[var_name] = ""
                continue
            
            try:
                # Пробуємо обчислити (formula або константа)
                ev = ExprEvalString(ctx)
                val = ev.eval(expr)
                computed_vars[var_name] = val
                ctx[var_name] = val
            except Exception as e:
                # Якщо це рядок без лапок — використовуємо як literal
                expr_clean = expr.strip()
                if expr_clean and not any(ch in expr_clean for ch in '+-*/()'):
                    computed_vars[var_name] = expr_clean
                    ctx[var_name] = expr_clean
                else:
                    print(f"[Worker] Variable '{var_name}' помилка: {e}")
                    computed_vars[var_name] = ""
                    ctx[var_name] = ""
        
        # Зберігаємо variables в obj.variables (для повторного використання)
        obj.variables.update(computed_vars)

        ctx['pins'] = getattr(obj, 'pins', {})
        if 'base_element' not in ctx: ctx['base_element'] = {'x0': ax, 'y0': ay, 'length': aw}
        if 'shape' not in ctx: ctx['shape'] = {'x0': ax, 'y0': ay, 'length': aw}
        
        bx, by = float(ctx['base_element'].get('x0', ax)), float(ctx['base_element'].get('y0', ay))
        blen = max(float(ctx['base_element'].get('length', aw)), 1.0)

        abs_lines = []
        for line_def in tmpl.get('geometry', {}).get('lines', []):
            l_type, role = line_def.get('type', 'H'), line_def.get('role', 'unknown')
            try:
                lx0, ly0 = bx + float(line_def.get('x0_offset_ratio', 0)) * blen, by + float(line_def.get('y0_offset_ratio', 0)) * blen
                if l_type == 'path':
                    # Універсальний path — M/L/Q/C/A/Z
                    abs_cmds = []
                    all_pts_x, all_pts_y = [], []
                    for cmd_data in line_def.get('commands_ratio', []):
                        cmd = cmd_data[0]
                        coords = cmd_data[1:]
                        abs_cmd = [cmd]
                        for i in range(0, len(coords), 2):
                            if i + 1 < len(coords):
                                px = bx + float(coords[i]) * blen
                                py = by + float(coords[i+1]) * blen
                                abs_cmd.extend([px, py])
                                all_pts_x.append(px)
                                all_pts_y.append(py)
                        abs_cmds.append(abs_cmd)
                    if all_pts_x:
                        p_min_x, p_min_y = min(all_pts_x), min(all_pts_y)
                        p_max_x, p_max_y = max(all_pts_x), max(all_pts_y)
                        ctx[role] = {'x0': p_min_x, 'y0': p_min_y, 'x1': p_max_x, 'y1': p_max_y,
                                     'length': max(p_max_x - p_min_x, p_max_y - p_min_y, 1.0),
                                     'bbox_w': max(p_max_x - p_min_x, 1.0), 'bbox_h': max(p_max_y - p_min_y, 1.0)}
                        abs_lines.append({'x0': p_min_x, 'y0': p_min_y, 'x1': p_max_x, 'y1': p_max_y,
                                          'type': 'path', 'path_cmds': abs_cmds})
                
                elif l_type in ('arc', 'rect', 'ellipse'):
                    lw = float(line_def.get('width_ratio', line_def.get('length_ratio', 1.0))) * blen
                    lh = float(line_def.get('height_ratio', line_def.get('length_ratio', 1.0))) * blen
                    w_safe, h_safe = max(abs(lw), 1.0), max(abs(lh), 1.0)
                    
                    ctx[role] = {
                        'x0': lx0, 'y0': ly0, 'x1': lx0 + lw, 'y1': ly0 + lh, 
                        'length': max(w_safe, h_safe), 'bbox_w': w_safe, 'bbox_h': h_safe
                    }
                    
                    seg_type = l_type if l_type == 'image' else ('ellipse' if line_def.get('require_closed', l_type != 'arc') else 'arc')
                    rec_path, rec_pts = [], []
                    
                    if 'path_ratios' in line_def:
                        for cmd, *args in line_def['path_ratios']: 
                            rec_path.append([cmd] + [(bx + p[0]*blen, by + p[1]*blen) for p in args])
                    elif 'pts_ratios' in line_def:
                        rec_pts = [(bx + p[0]*blen, by + p[1]*blen) for p in line_def['pts_ratios']]
                    
                    abs_lines.append({
                        'x0': lx0, 'y0': ly0, 'x1': lx0 + w_safe, 'y1': ly0 + h_safe, 
                        'type': seg_type, 'path': rec_path, 'pts': rec_pts
                    })
                # ==========================================================
                else:
                    ll = float(line_def.get('length_ratio', 1.0)) * blen
                    if l_type == 'H': lx1, ly1 = lx0 + ll, ly0
                    elif l_type == 'V': lx1, ly1 = lx0, ly0 + ll
                    elif l_type == 'D': 
                        lx1 = bx + float(line_def.get('x1_offset_ratio', line_def.get('x0_offset_ratio', 0) + line_def.get('length_ratio', 1.0))) * blen
                        ly1 = by + float(line_def.get('y1_offset_ratio', line_def.get('y0_offset_ratio', 0) + line_def.get('length_ratio', 1.0))) * blen
                        ll = math.hypot(lx1 - lx0, ly1 - ly0)
                    ctx[role] = {'x0': lx0, 'y0': ly0, 'x1': lx1, 'y1': ly1, 'length': ll}
                    abs_lines.append({'x0': lx0, 'y0': ly0, 'x1': lx1, 'y1': ly1, 'type': 'line'})
            except Exception: pass

        if not abs_lines: abs_lines.append({'x0': bx, 'y0': by, 'x1': bx + blen, 'y1': by, 'type': 'line'})
        xs, ys = [l['x0'] for l in abs_lines] + [l['x1'] for l in abs_lines], [l['y0'] for l in abs_lines] + [l['y1'] for l in abs_lines]
        min_x, min_y, union_w, union_h = min(xs), min(ys), max(max(xs) - min(xs), 1.0), max(max(ys) - min(ys), 1.0)

        obj.custom_zones['ui_rect'] = {'x': min_x, 'y': min_y, 'w': union_w, 'h': union_h}
        obj.custom_zones['manual_rect'] = obj.custom_zones['ui_rect']
        obj.custom_zones['anchor_pos'] = {'x': ax, 'y': ay}

        obj.custom_zones['ghost_skeleton'] = []
        for seg in abs_lines:
            skel_data = {'rx0': (seg['x0'] - min_x) / union_w, 'ry0': (seg['y0'] - min_y) / union_h, 'rx1': (seg['x1'] - min_x) / union_w, 'ry1': (seg['y1'] - min_y) / union_h, 'type': seg.get('type', 'line')}
            if seg.get('type') == 'path' and seg.get('path_cmds'):
                # Конвертуємо абсолютні координати path в ratios відносно union_rect
                ratios = []
                for cmd_data in seg['path_cmds']:
                    cmd = cmd_data[0]
                    ratio_cmd = [cmd]
                    for i in range(1, len(cmd_data), 2):
                        if i + 1 <= len(cmd_data):
                            ratio_cmd.append((cmd_data[i] - min_x) / union_w)
                            ratio_cmd.append((cmd_data[i+1] - min_y) / union_h)
                    ratios.append(ratio_cmd)
                skel_data['path_ratios'] = ratios
            elif seg.get('path'): skel_data['path'] = [[cmd] + [((p[0]-min_x)/union_w, (p[1]-min_y)/union_h) for p in args] for cmd, *args in seg['path']]
            elif seg.get('pts'): skel_data['pts'] = [((p[0]-min_x)/union_w, (p[1]-min_y)/union_h) for p in seg['pts']]
            obj.custom_zones['ghost_skeleton'].append(skel_data)

        # === SERVICE ZONES — спільні для всіх variants, обчислюються першими ===
        obj.custom_zones['service_ghost_zones'] = []
        ev_service = ExprEvaluator(ctx)
        service_text_fields = {}
        service_required_failed = False
        
        for sz in tmpl.get('service_zones', []):
            field_name = sz.get('field', 'unknown')
            try:
                x0 = ev_service.eval(sz['x0'])
                y0 = ev_service.eval(sz['y0'])
                x1 = ev_service.eval(sz['x1'])
                y1 = ev_service.eval(sz['y1'])
                
                obj.custom_zones['service_ghost_zones'].append({
                    'field': field_name,
                    'rx0': (x0 - min_x) / union_w,
                    'ry0': (y0 - min_y) / union_h,
                    'rx1': (x1 - min_x) / union_w,
                    'ry1': (y1 - min_y) / union_h,
                    'required': sz.get('required', False),
                    'export': sz.get('export', False)
                })
                
                text = extract_text_by_center(page, x0, y0, x1, y1)
                service_text_fields[field_name] = text
                ctx[field_name] = text  # додаємо в context для conditions
                
                if sz.get('required', False) and not text.strip():
                    service_required_failed = True
                    print(f"[Worker] SKIP: '{field_name}' required, але text='{repr(text)}'")
                    print(f"[Worker] Service zone '{field_name}' required але порожнє — об'єкт відкинуто")
                    
            except Exception as e:
                print(f"[Worker] Помилка service zone {field_name}: {e}")
        
        # Якщо required service zone порожня — позначаємо об'єкт для відкидання
        if service_required_failed:
            obj.custom_zones['_skip'] = True
            print(f"[Worker] Об'єкт відкинуто: _skip=True")
        
        # === ВИБІР VARIANT ПО SERVICE ZONES + ВАРІАБЛЕС ===
        selected_variant = None
        ev_cond = ExprEvaluator(ctx)
        for v in tmpl.get('variants', []):
            cond = v.get('condition', 'True')
            if ev_cond.eval_condition(cond):
                selected_variant = v
                obj.variant_name = v.get('name', 'default')
                break
        
        if selected_variant is None and tmpl.get('variants'):
            selected_variant = tmpl['variants'][0]
            obj.variant_name = selected_variant.get('name', 'default')
        
        # === 1. ОБЧИСЛЕННЯ VARIABLES (формули та статичні значення) ===
        ev_vars = ExprEvaluator(ctx)
        computed_vars = {}
        for var_name, var_def in tmpl.get('variables', {}).items():
            if isinstance(var_def, str):
                expr = var_def; export = True
            elif isinstance(var_def, dict):
                expr = var_def.get('expr', ''); export = var_def.get('export', True)
            else:
                continue
            if not expr:
                computed_vars[var_name] = ""; ctx[var_name] = ""
                continue
            try:
                val = ev_vars.eval_raw(expr) if hasattr(ev_vars, 'eval_raw') else ev_vars.eval(expr)
                computed_vars[var_name] = val
                ctx[var_name] = val
            except Exception as e:
                print(f"[Worker] Variable '{var_name}' помилка: {e}")
                computed_vars[var_name] = ""; ctx[var_name] = ""

        # === 2. SERVICE ZONES — OCR за центром (як text_zones) ===
        obj.custom_zones['service_ghost_zones'] = []
        ev_service = ExprEvaluator(ctx)
        service_text_fields = {}
        service_required_failed = False
        
        for sz in tmpl.get('service_zones', []):
            field_name = sz.get('field', 'unknown')
            try:
                x0 = ev_service.eval(sz['x0'])
                y0 = ev_service.eval(sz['y0'])
                x1 = ev_service.eval(sz['x1'])
                y1 = ev_service.eval(sz['y1'])
                
                obj.custom_zones['service_ghost_zones'].append({
                    'field': field_name,
                    'rx0': (x0 - min_x) / union_w, 'ry0': (y0 - min_y) / union_h,
                    'rx1': (x1 - min_x) / union_w, 'ry1': (y1 - min_y) / union_h,
                    'required': sz.get('required', False),
                    'export': sz.get('export', False)
                })
                text = extract_text_by_center(page, x0, y0, x1, y1)
                service_text_fields[field_name] = text
                ctx[field_name] = text
                if sz.get('required', False) and not text.strip():
                    service_required_failed = True
                    print(f"[Worker] SKIP: '{field_name}' required, але text=''")
            except Exception as e:
                print(f"[Worker] Помилка service zone {field_name}: {e}")
        
        if service_required_failed:
            obj.custom_zones['_skip'] = True

        # === 3. VARIANT SELECTION з conditions ===
        selected_variant = None
        ev_cond = ExprEvaluator(ctx)
        for v in tmpl.get('variants', []):
            cond = v.get('condition', 'True')
            try:
                if hasattr(ev_cond, 'eval_condition'):
                    if ev_cond.eval_condition(cond):
                        selected_variant = v
                        obj.variant_name = v.get('name', 'default')
                        break
                else:
                    if ev_cond.eval(cond):
                        selected_variant = v
                        obj.variant_name = v.get('name', 'default')
                        break
            except Exception:
                continue
        if selected_variant is None and tmpl.get('variants'):
            selected_variant = next((v for v in tmpl.get('variants', []) 
                                     if v.get('name') == obj.variant_name), tmpl['variants'][0])

        # === 4. TEXT ZONES вибраного варіанту ===
        obj.custom_zones['ghost_zones'] = []
        variant = selected_variant if selected_variant else {}
        new_text_fields = {**computed_vars, **service_text_fields}
        
        ev = ExprEvaluator(ctx)
        for tz in variant.get('text_zones', []):
            field_name = tz.get('field', 'unknown')
            repeat_expr = tz.get('repeat_over', "").strip()
            try:
                count = 1
                if repeat_expr:
                    try: count = int(ev.eval(repeat_expr))
                    except: count = 1
                extracted_texts = []
                for i in range(count):
                    ev.safe_env['i'] = i
                    x0, y0 = ev.eval(tz['x0']), ev.eval(tz['y0'])
                    x1, y1 = ev.eval(tz['x1']), ev.eval(tz['y1'])
                    if i == 0 or i == count - 1:
                        obj.custom_zones['ghost_zones'].append({
                            'field': f"{field_name}[{i}]" if count > 1 else field_name, 
                            'rx0': (x0 - min_x) / union_w, 'ry0': (y0 - min_y) / union_h, 
                            'rx1': (x1 - min_x) / union_w, 'ry1': (y1 - min_y) / union_h
                        })
                    text = extract_text_by_center(page, x0, y0, x1, y1, tz.get('multiline'), tz.get('join', '\n'))
                    if text: extracted_texts.append(text)
                    else:
                        if count > 1: extracted_texts.append("")
                if count > 1: new_text_fields[field_name] = tz.get('separator', ', ').join(extracted_texts)
                else: new_text_fields[field_name] = extracted_texts[0] if extracted_texts else ""
            except Exception as e: print(f"Помилка OCR зони {field_name}: {e}")
        obj.text_fields = new_text_fields


class SearchWorker(BaseWorker):
    object_found = pyqtSignal(dict)
    scan_finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, pdf_path, page_num, templates, exclusion_zones=None):
        super().__init__()
        self.pdf_path, self.page_num, self.templates = pdf_path, page_num, templates
        self.exclusion_zones = exclusion_zones or []
        self.all_found_dicts = []

    def _is_excluded(self, obj):
        """Перевіряє чи об'єкт перекривається з exclusion_zones (approved)."""
        if not self.exclusion_zones:
            return False
        a = obj.anchor
        ox, oy = float(a.get('x', 0)), float(a.get('y', 0))
        for ez in self.exclusion_zones:
            ex, ey = ez['x'], ez['y']
            ew, eh = ez['w'], ez['h']
            if ex <= ox <= ex + ew and ey <= oy <= ey + eh:
                return True
        return False

    def run(self):
        try:
            self.templates.sort(key=lambda t: (not t.get('Page_Data', False), t.get('priority', 50)))
            base_global_data = {}
            for t in self.templates:
                if t.get("Page_Data", False):
                    for var in t.get("variants", []):
                        for tz in var.get("text_zones", []):
                            base_global_data[f"{t['name']}_{tz['field']}"] = ""

            with pdfplumber.open(self.pdf_path) as pdf_doc:
                page = pdf_doc.pages[self.page_num]
                found_objects = te.process_page(page, self.templates, self.page_num)
                page_global_data = dict(base_global_data)

                # ПРОХІД 1: Збираємо глобальні дані
                for obj in found_objects:
                    tmpl = next((t for t in self.templates if t['name'] == obj.template_name), None)
                    if tmpl and tmpl.get("Page_Data", False):
                        self._enrich_object_data(obj, tmpl, page)
                        for k, v in obj.text_fields.items():
                            page_global_data[f"{tmpl['name']}_{k}"] = v

                # ПРОХІД 2: Фільтруємо excluded, обробляємо OCR, потім перевіряємо _skip та відправляємо в UI
               # ПРОХІД 2: Фільтруємо, обробляємо OCR, потім перевіряємо _skip
                for obj in found_objects:
                    if self._is_excluded(obj):
                        continue
                    tmpl = next((t for t in self.templates if t['name'] == obj.template_name), None)
                    if tmpl:
                        if not tmpl.get("Page_Data", False):
                            self._enrich_object_data(obj, tmpl, page)
                        # _skip встановлюється всередині _enrich_object_data
                        if obj.custom_zones.get('_skip'):
                            continue
                        obj_dict = obj.to_dict()
                        self.all_found_dicts.append(obj_dict)
                        self.object_found.emit(obj_dict)

        except Exception as e: 
            self.error.emit(str(e))
        finally: 
            self.scan_finished.emit(self.all_found_dicts)

class BatchWorker(BaseWorker):
    page_started = pyqtSignal(int); page_finished = pyqtSignal(int, list)
    page_error = pyqtSignal(int, str); batch_finished = pyqtSignal(int)
    progress_changed = pyqtSignal(int, int)

    def __init__(self, pdf_path: str, page_range: range, templates: list,
                 skip_approved: bool = False, approved_cache: dict = None):
        super().__init__()
        self.pdf_path, self.page_range, self.templates = pdf_path, page_range, templates
        self.skip_approved = skip_approved
        self.approved_cache = approved_cache or {}
        self._stop, self._pause = False, False

    def stop(self): self._stop = True
    def pause(self): self._pause = True
    def resume(self): self._pause = False

    def _get_exclusion_zones(self, page_num):
        if not self.skip_approved:
            return []
        cached = self.approved_cache.get(str(page_num), [])
        zones = []
        for obj in cached:
            if obj.get('status') == 'approved':
                ui = obj.get('custom_zones', {}).get('ui_rect', {})
                if ui:
                    zones.append({
                        'x': ui.get('x', 0), 'y': ui.get('y', 0),
                        'w': ui.get('w', 0), 'h': ui.get('h', 0)
                    })
        return zones

    def _is_excluded(self, obj, exclusion_zones):
        if not exclusion_zones:
            return False
        a = obj.anchor
        ox, oy = float(a.get('x', 0)), float(a.get('y', 0))
        for ez in exclusion_zones:
            ex, ey = ez['x'], ez['y']
            ew, eh = ez['w'], ez['h']
            if ex <= ox <= ex + ew and ey <= oy <= ey + eh:
                return True
        return False

    def run(self):
        self.templates.sort(key=lambda t: (not t.get('Page_Data', False), t.get('priority', 50)))
        base_global_data = {}
        for t in self.templates:
            if t.get("Page_Data", False):
                for var in t.get("variants", []):
                    for tz in var.get("text_zones", []):
                        base_global_data[f"{t['name']}_{tz['field']}"] = ""

        total, processed = len(self.page_range), 0
        try:
            with pdfplumber.open(self.pdf_path) as pdf_doc:
                for step, page_num in enumerate(self.page_range):
                    if self._stop: break
                    while self._pause and not self._stop: self.msleep(100)
                    
                    self.progress_changed.emit(step, total)
                    self.page_started.emit(page_num)

                    try:
                        page = pdf_doc.pages[page_num]
                        exclusion_zones = self._get_exclusion_zones(page_num)
                        found_objects = te.process_page(page, self.templates, page_num)
                        page_global_data = dict(base_global_data)

                        for obj in found_objects:
                            tmpl = next((t for t in self.templates if t['name'] == obj.template_name), None)
                            if tmpl and tmpl.get("Page_Data", False):
                                self._enrich_object_data(obj, tmpl, page)
                                for k, v in obj.text_fields.items():
                                    page_global_data[f"{tmpl['name']}_{k}"] = v

                        result_dicts = []
                        # Спочатку approved об'єкти
                        if self.skip_approved:
                            for obj in self.approved_cache.get(str(page_num), []):
                                if obj.get('status') == 'approved':
                                    result_dicts.append(dict(obj))

                        # Потім нові (виключаючи зони approved)
                        for obj in found_objects:
                            if self._is_excluded(obj, exclusion_zones):
                                continue
                            tmpl = next((t for t in self.templates if t['name'] == obj.template_name), None)
                            if tmpl:
                                if not tmpl.get("Page_Data", False):
                                    self._enrich_object_data(obj, tmpl, page)
                                if obj.custom_zones.get('_skip'):
                                    continue
                                result_dicts.append(obj.to_dict())

                        self.page_finished.emit(page_num, result_dicts)
                        processed += 1
                    except Exception as e:
                        self.page_error.emit(page_num, str(e))
        finally:
            self.batch_finished.emit(processed)

