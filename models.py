from dataclasses import dataclass, field
from typing import Optional
import math

@dataclass
class PageLine:
    """
    Універсальний клас для зберігання координат і властивостей графічних примітивів 
    (ліній, прямокутників, кіл, дуг) зі сторінки PDF.
    """
    lid:     int
    x0:      float
    y0:      float
    x1:      float
    y1:      float
    length:  float
    dir:     str          
    blocked: bool = False
    angle:   float = 0.0   # Кут нахилу для діагоналей
    # --- Поля для дуг та кривих Без'є ---
    pts:      list = field(default_factory=list)   # Всі контрольні точки (для малювання)
    is_closed: bool = False                         # Замкнений контур?
    arc_bbox_w: float = 0.0                         # Ширина bounding box дуги
    arc_bbox_h: float = 0.0                         # Висота bounding box дуги

    @property
    def width(self) -> float:
        return abs(self.x1 - self.x0)

    @property
    def height(self) -> float:
        return abs(self.y1 - self.y0)
    
    @classmethod
    def from_pdfplumber(cls, raw: dict, lid: int, shape_type: str = 'line') -> Optional['PageLine']:
        import math
        
        # === ЛОГІКА ДЛЯ ЗОБРАЖЕНЬ (IMAGE) ===
        if shape_type == 'image':
            x0, top = raw.get('x0'), raw.get('top')
            x1, bottom = raw.get('x1'), raw.get('bottom')
            
            if x0 is None or top is None or x1 is None or bottom is None:
                return None
                
            w = abs(x1 - x0)
            h = abs(bottom - top)
            
            if w < 1 or h < 1: 
                return None
                
            res = cls(lid=lid, x0=x0, y0=top, x1=x1, y1=bottom, length=max(w, h), dir='image')
            res.arc_bbox_w, res.arc_bbox_h = w, h
            res.is_closed = True
            
            # --- НОВЕ: Зберігаємо метадані для жорсткої фільтрації в рушії ---
            res.img_name = raw.get('name', '')
            res.img_w = float(raw.get('width', 0))
            res.img_h = float(raw.get('height', 0))
            src = raw.get('srcsize', (0, 0))
            res.img_src_w = float(src[0]) if src else 0.0
            res.img_src_h = float(src[1]) if src else 0.0
            # ------------------------------------------------------------------
            
            return res
        # ====================================

        if shape_type == 'line':
            # Отримуємо точки
            if 'pts' in raw and len(raw['pts']) >= 2:
                (px0, py0), (px1, py1) = raw['pts'][0], raw['pts'][-1]
            else:
                px0, py0 = raw['x0'], raw.get('top', raw['y0'])
                px1, py1 = raw['x1'], raw.get('bottom', raw['y1'])

            dx, dy = abs(px1 - px0), abs(py1 - py0)
            
            if dy <= 1.0 and dx > 1.0: # Горизонталь
                direction, length, angle = 'H', dx, 0.0
                x0, y0 = min(px0, px1), min(py0, py1)
                x1, y1 = max(px0, px1), min(py0, py1)
            elif dx <= 1.0 and dy > 1.0: # Вертикаль
                direction, length, angle = 'V', dy, 90.0
                x0, y0 = min(px0, px1), min(py0, py1)
                x1, y1 = min(px0, px1), max(py0, py1)
            elif dx > 1.0 and dy > 1.0: # Діагональ
                direction, length = 'D', math.sqrt(dx**2 + dy**2)
                # НОРМАЛІЗАЦІЯ: завжди відраховуємо від верхньої точки (Top-Down)
                if py0 <= py1: x0, y0, x1, y1 = px0, py0, px1, py1
                else: x0, y0, x1, y1 = px1, py1, px0, py0
                angle = math.degrees(math.atan2(y1 - y0, x1 - x0))
            else: return None
            
            res = cls(lid=lid, x0=x0, y0=y0, x1=x1, y1=y1, length=length, dir=direction)
            res.angle = angle
            return res
            
        else:
            # --- СКЛАДНІ ФІГУРИ (arc, ellipse, rect, path) ---
            direction = shape_type
            x0, y0 = raw.get('x0', 0), raw.get('top', 0)
            w, h = raw.get('width', raw.get('w', 0)), raw.get('height', raw.get('h', 0))
            x1, y1 = x0 + w, y0 + h

            # НАДІЙНЕ витягування точок (навіть якщо pdfplumber дав лише 'path')
            raw_pts = list(raw.get('pts', []))
            if not raw_pts and 'path' in raw:
                for cmd_tuple in raw['path']:
                    args = cmd_tuple[1:]
                    # Безпечно витягуємо всі координати з команд малювання
                    for i in range(0, len(args), 2):
                        if i+1 < len(args):
                            raw_pts.append((args[i], args[i+1]))

            start_rel_x, start_rel_y = 0.5, 0.5
            end_rel_x, end_rel_y = 0.5, 0.5

            if raw_pts:
                xs, ys = [p[0] for p in raw_pts], [p[1] for p in raw_pts]
                x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                w, h = x1 - x0, y1 - y0
                
                # Знаходимо, де знаходяться кінці дуги (від 0.0 до 1.0)
                if w > 0:
                    start_rel_x = (raw_pts[0][0] - x0) / w
                    end_rel_x = (raw_pts[-1][0] - x0) / w
                if h > 0:
                    start_rel_y = (raw_pts[0][1] - y0) / h
                    end_rel_y = (raw_pts[-1][1] - y0) / h

            if w < 1 or h < 1: return None

            res = cls(lid=lid, x0=x0, y0=y0, x1=x1, y1=y1, length=max(w, h), dir=direction)
            res.pts = raw.get('pts', [])
            res.path = raw.get('path', [])
            res.is_closed = any(s[0] == 'h' for s in res.path) if res.path else False
            res.arc_bbox_w, res.arc_bbox_h = w, h
            
            # Зберігаємо середню позицію кінців для порівняння з шаблоном!
            res.ends_x = (start_rel_x + end_rel_x) / 2
            res.ends_y = (start_rel_y + end_rel_y) / 2
            
            return res

@dataclass
class FoundObject:
    template_name: str
    variant_name:  str
    anchor:        dict
    variables:     dict
    line_ids:      list
    pins:          dict
    page_num:      int = 0
    page_w:        float = 0.0  
    page_h:        float = 0.0  
    text_fields:   dict = field(default_factory=dict)
    status:        str = "pending"
    custom_zones:  dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "template_name": self.template_name,
            "variant_name": self.variant_name,
            "anchor": self.anchor,
            "variables": self.variables,
            "line_ids": self.line_ids,
            "pins": self.pins,
            "page_num": self.page_num,
            "page_w": self.page_w,
            "page_h": self.page_h,
            "text_fields": self.text_fields,
            "status": self.status,
            "custom_zones": self.custom_zones
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            template_name=data.get("template_name", ""),
            variant_name=data.get("variant_name", ""),
            anchor=data.get("anchor", {}),
            variables=data.get("variables", {}),
            line_ids=data.get("line_ids", []),
            pins=data.get("pins", {}),
            page_num=data.get("page_num", 0),
            page_w=data.get("page_w", 0.0),
            page_h=data.get("page_h", 0.0),
            text_fields=data.get("text_fields", {}),
            status=data.get("status", "pending"),
            custom_zones=data.get("custom_zones", {})
        )