from PyQt6.QtWidgets import (QGraphicsView, QGraphicsLineItem, QGraphicsRectItem,
                             QGraphicsEllipseItem, QGraphicsTextItem, QGraphicsPathItem,
                             QGraphicsItem)
from PyQt6.QtGui import QPen, QColor, QPainter, QPainterPath, QBrush, QFont
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF


# ═══════════════════════════════════════════════════════════════════════════════
# TEXT ZONE DRAG HANDLE  —  «Ручка» для перетягування текстової зони
# ═══════════════════════════════════════════════════════════════════════════════

class TextZoneHandle(QGraphicsRectItem):
    """
    Інтерактивна рамка текстової зони, прив'язана до ValidationBox.

    Що вміє:
      • Перетягування (move) — зміщує зону, зберігаючи розміри.
      • Зміна розміру (resize) — за правий/нижній кут.
      • При відпусканні — перераховує rx0/ry0/rx1/ry1 відносно union_rect
        ValidationBox і записує назад у found_obj.custom_zones['ghost_zones'].
      • Подвійний клік — скидає зону до початкового (шаблонного) стану.

    Координати зберігаються в абсолютних px сцени; ratios перераховуються
    тільки при mouseReleaseEvent, щоб не смикати модель під час руху.
    """

    HANDLE_SIZE  = 8    # розмір квадратика в правому нижньому куті
    RESIZE_ZONE  = 10   # ширина зони «чуття» для resize
    LABEL_OFFSET = -14  # зсув підпису вгору відносно рамки

    def __init__(self, zone_dict: dict, zone_index: int, parent_vbox: 'ValidationBox'):
        """
        zone_dict  — елемент found_obj.custom_zones['ghost_zones'][zone_index]
        zone_index — індекс у списку (потрібен для запису назад)
        parent_vbox — батьківський ValidationBox (для доступу до rect() та found_obj)
        """
        super().__init__(parent_vbox)   # дочірній елемент ValidationBox

        self._vbox       = parent_vbox
        self._zone_idx   = zone_index
        self._zone_dict  = zone_dict
        self._field_name = zone_dict.get('field', f'zone_{zone_index}')

        # Обчислюємо початковий прямокутник у локальних координатах батька
        self._sync_rect_from_ratios()

        # Стиль: читаємо з state_ref батька якщо є, інакше дефолт
        sr = getattr(parent_vbox, 'state_ref', None)
        if sr:
            zone_c_hex = sr.load_setting("color_contour_zones", "#ff9b59b6")
            zone_f_hex = sr.load_setting("color_fill_zones",    "#4d9b59b6")
            zone_w     = float(sr.load_setting("line_width_zones", 1.5))
            PEN_STYLES = {'solid': Qt.PenStyle.SolidLine, 'dash': Qt.PenStyle.DashLine,
                          'dot': Qt.PenStyle.DotLine, 'dashdot': Qt.PenStyle.DashDotLine}
            zone_s = PEN_STYLES.get(sr.load_setting("line_style_zones", "dash"), Qt.PenStyle.DashLine)
            self._color       = QColor(zone_c_hex)
            self._color_fill  = QColor(zone_f_hex)
        else:
            self._color       = QColor(22, 160, 133)
            self._color_fill  = QColor(22, 160, 133, 30)
            zone_w, zone_s    = 1.5, Qt.PenStyle.DashLine

        self._pen_normal  = QPen(self._color, zone_w, zone_s)
        self._pen_hover   = QPen(self._color.lighter(130), zone_w + 1.0)
        self._pen_drag    = QPen(QColor(243, 156, 18), 2.0)
        self._brush_normal = QBrush(self._color_fill if sr else QColor(22, 160, 133, 30))
        self._brush_hover  = QBrush(self._color.lighter(130) if not sr else QColor(self._color_fill).lighter(120))

        self.setPen(self._pen_normal)
        self.setBrush(self._brush_normal)
        self.setZValue(10)   # поверх ValidationBox

        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

        # Підпис назви поля
        self._label = QGraphicsTextItem(self._field_name, self)
        self._label.setDefaultTextColor(self._color)
        fnt = QFont(); fnt.setPointSize(7); fnt.setBold(True)
        self._label.setFont(fnt)
        self._label.setPos(2, self.LABEL_OFFSET)
        self._label.setZValue(11)

        # Квадратик resize-ручки (правий нижній кут)
        self._resize_knob = QGraphicsRectItem(self)
        self._resize_knob.setBrush(QBrush(self._color))
        self._resize_knob.setPen(QPen(Qt.GlobalColor.white, 1))
        self._resize_knob.setZValue(12)
        self._update_knob_pos()

        # Стан перетягування
        self._drag_mode  = None   # 'move' | 'resize'

    def apply_new_settings(self, state_ref):
        """Оновлює стилі ручки при зміні налаштувань (викликається з refresh_scene_styles)."""
        PEN_STYLES = {'solid': Qt.PenStyle.SolidLine, 'dash': Qt.PenStyle.DashLine,
                      'dot': Qt.PenStyle.DotLine, 'dashdot': Qt.PenStyle.DashDotLine}
        zone_c_hex = state_ref.load_setting("color_contour_zones", "#ff9b59b6")
        zone_f_hex = state_ref.load_setting("color_fill_zones",    "#4d9b59b6")
        zone_w     = float(state_ref.load_setting("line_width_zones", 1.5))
        zone_s     = PEN_STYLES.get(state_ref.load_setting("line_style_zones", "dash"), Qt.PenStyle.DashLine)

        self._color      = QColor(zone_c_hex)
        self._color_fill = QColor(zone_f_hex)
        self._pen_normal = QPen(self._color, zone_w, zone_s)
        self._pen_hover  = QPen(self._color.lighter(130), zone_w + 1.0)
        self._brush_normal = QBrush(self._color_fill)
        self._resize_knob.setBrush(QBrush(self._color))
        self._label.setDefaultTextColor(self._color)

        # Застосовуємо якщо не в режимі перетягування
        if not self._drag_mode:
            self.setPen(self._pen_normal)
            self.setBrush(self._brush_normal)
        self.update()
        self._drag_start_scene = None
        self._drag_start_rect  = None

    # ── Синхронізація ratios ↔ rect ──────────────────────────────────────────

    def _sync_rect_from_ratios(self):
        """Обчислює QRectF у локальних координатах батька з ratios зони."""
        pr = self._vbox.rect()
        z  = self._zone_dict
        x  = pr.x() + z['rx0'] * pr.width()
        y  = pr.y() + z['ry0'] * pr.height()
        w  = (z['rx1'] - z['rx0']) * pr.width()
        h  = (z['ry1'] - z['ry0']) * pr.height()
        self.setRect(QRectF(x, y, max(w, 4.0), max(h, 4.0)))

    def _write_ratios_back(self):
        """Перераховує ratios з поточного rect() і записує в zone_dict."""
        pr = self._vbox.rect()
        if pr.width() == 0 or pr.height() == 0:
            return
        r  = self.rect()
        self._zone_dict['rx0'] = (r.x()      - pr.x()) / pr.width()
        self._zone_dict['ry0'] = (r.y()      - pr.y()) / pr.height()
        self._zone_dict['rx1'] = (r.right()  - pr.x()) / pr.width()
        self._zone_dict['ry1'] = (r.bottom() - pr.y()) / pr.height()

        # Клампуємо — зона може виходити за межі union_rect (це дозволено V5.2)
        # але обмежуємо щоб rx0 < rx1 та ry0 < ry1
        if self._zone_dict['rx0'] >= self._zone_dict['rx1']:
            self._zone_dict['rx1'] = self._zone_dict['rx0'] + 0.01
        if self._zone_dict['ry0'] >= self._zone_dict['ry1']:
            self._zone_dict['ry1'] = self._zone_dict['ry0'] + 0.01

    def _update_knob_pos(self):
        r = self.rect()
        hs = self.HANDLE_SIZE
        self._resize_knob.setRect(r.right() - hs, r.bottom() - hs, hs, hs)

    def refresh(self):
        """Викликається ззовні якщо батьківський rect змінився — оновлює позицію."""
        self._sync_rect_from_ratios()
        self._update_knob_pos()

    # ── Hover ────────────────────────────────────────────────────────────────

    def hoverMoveEvent(self, e):
        if self._is_near_resize(e.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setPen(self._pen_hover)
        self.setBrush(self._brush_hover)
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e):
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setPen(self._pen_normal)
        self.setBrush(self._brush_normal)
        super().hoverLeaveEvent(e)

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def _is_near_resize(self, local_pos: QPointF) -> bool:
        r = self.rect()
        return (r.right()  - local_pos.x() < self.RESIZE_ZONE and
                r.bottom() - local_pos.y() < self.RESIZE_ZONE)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start_scene = e.scenePos()
            self._drag_start_rect  = QRectF(self.rect())
            self._drag_mode = 'resize' if self._is_near_resize(e.pos()) else 'move'
            self.setPen(self._pen_drag)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_mode and self._drag_start_scene:
            parent = self._vbox
            p1 = parent.mapFromScene(self._drag_start_scene)
            p2 = parent.mapFromScene(e.scenePos())
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()

            r = QRectF(self._drag_start_rect)
            if self._drag_mode == 'move':
                r.translate(dx, dy)
            elif self._drag_mode == 'resize':
                r.setWidth(max(8.0, r.width() + dx))
                r.setHeight(max(8.0, r.height() + dy))

            self.setRect(r)
            self._update_knob_pos()
            self._label.setPos(2, self.LABEL_OFFSET)
            e.accept()   # НЕ передаємо батьку
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_mode:
            self._write_ratios_back()
            self._drag_mode = None
            self._drag_start_scene = None
            self.setPen(self._pen_normal)
            self.setBrush(self._brush_normal)
            if hasattr(self._vbox, '_on_zone_moved'):
                self._vbox._on_zone_moved()
            e.accept()   # НЕ передаємо батьку
        else:
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        """Подвійний клік — скидає зону до значень з шаблону (ratios)."""
        self._sync_rect_from_ratios()
        self._update_knob_pos()
        e.accept()


class ServiceZoneHandle(TextZoneHandle):
    """Аналог TextZoneHandle, але для service_zones (teal-колір, інший ключ у custom_zones)."""
    
    def __init__(self, zone_dict: dict, zone_index: int, parent_vbox: 'ValidationBox'):
        # Викликаємо __init__ батька, але переоформлюємо стилі під service
        super().__init__(zone_dict, zone_index, parent_vbox)
        
        sr = getattr(parent_vbox, 'state_ref', None)
        if sr:
            c_hex = sr.load_setting("color_contour_service_zones", "#ff16a085")
            f_hex = sr.load_setting("color_fill_service_zones",    "#4d1abc9c")
            w     = float(sr.load_setting("line_width_service_zones", 1.5))
            PEN_STYLES = {'solid': Qt.PenStyle.SolidLine, 'dash': Qt.PenStyle.DashLine,
                          'dot': Qt.PenStyle.DotLine, 'dashdot': Qt.PenStyle.DashDotLine}
            style = PEN_STYLES.get(sr.load_setting("line_style_service_zones", "dash"), Qt.PenStyle.DashLine)
            self._color = QColor(c_hex)
            self._color_fill = QColor(f_hex)
        else:
            self._color = QColor(22, 160, 133)
            self._color_fill = QColor(26, 188, 156, 60)
            w, style = 1.5, Qt.PenStyle.DashLine
        
        self._pen_normal = QPen(self._color, w, style)
        self._pen_hover = QPen(self._color.lighter(130), w + 1.0)
        self._pen_drag = QPen(QColor(243, 156, 18), 2.0)
        self._brush_normal = QBrush(self._color_fill)
        self._brush_hover = QBrush(QColor(self._color_fill).lighter(120))
        
        self.setPen(self._pen_normal)
        self.setBrush(self._brush_normal)
        self._resize_knob.setBrush(QBrush(self._color))
        self._label.setDefaultTextColor(self._color)
    
    def apply_new_settings(self, state_ref):
        """Оновлює стилі при зміні налаштувань."""
        PEN_STYLES = {'solid': Qt.PenStyle.SolidLine, 'dash': Qt.PenStyle.DashLine,
                      'dot': Qt.PenStyle.DotLine, 'dashdot': Qt.PenStyle.DashDotLine}
        c_hex = state_ref.load_setting("color_contour_service_zones", "#ff16a085")
        f_hex = state_ref.load_setting("color_fill_service_zones", "#4d1abc9c")
        w = float(state_ref.load_setting("line_width_service_zones", 1.5))
        style = PEN_STYLES.get(state_ref.load_setting("line_style_service_zones", "dash"), Qt.PenStyle.DashLine)
        
        self._color = QColor(c_hex)
        self._color_fill = QColor(f_hex)
        self._pen_normal = QPen(self._color, w, style)
        self._pen_hover = QPen(self._color.lighter(130), w + 1.0)
        self._brush_normal = QBrush(self._color_fill)
        self._resize_knob.setBrush(QBrush(self._color))
        self._label.setDefaultTextColor(self._color)
        
        if not self._drag_mode:
            self.setPen(self._pen_normal)
            self.setBrush(self._brush_normal)
        self.update()

    def mouseReleaseEvent(self, e):
        """Override щоб писати в service_ghost_zones замість ghost_zones."""
        if self._drag_mode:
            self._write_ratios_back()
            self._drag_mode = None
            self._drag_start_scene = None
            self.setPen(self._pen_normal)
            self.setBrush(self._brush_normal)
            if hasattr(self._vbox, '_on_service_zone_moved'):
                self._vbox._on_service_zone_moved()
            e.accept()
        else:
            super(TextZoneHandle, self).mouseReleaseEvent(e)

def build_bezier_path_from_segments(path_segs: list) -> QPainterPath:
    """
    Будує QPainterPath із PDF-сегментів pdfplumber (поле 'path').
    Формат сегментів:
      ('m', (x, y))               — moveto
      ('c', (cp1x,cp1y), (cp2x,cp2y), (ex,ey)) — кубічний Без'є
      ('l', (x, y))               — лінія
      ('h',)                      — closePath
      ('v', (cp2x,cp2y), (ex,ey)) — Без'є з першою КТ = поточна точка (PDF v-команда)
      ('y', (cp1x,cp1y), (ex,ey)) — Без'є з другою КТ = кінцева (PDF y-команда)
    """
    path = QPainterPath()
    cur = (0.0, 0.0)

    for seg in path_segs:
        cmd = seg[0]
        if cmd == 'm':
            x, y = seg[1]
            path.moveTo(x, y)
            cur = (x, y)
        elif cmd == 'l':
            x, y = seg[1]
            path.lineTo(x, y)
            cur = (x, y)
        elif cmd == 'c':
            cp1x, cp1y = seg[1]
            cp2x, cp2y = seg[2]
            ex, ey     = seg[3]
            path.cubicTo(cp1x, cp1y, cp2x, cp2y, ex, ey)
            cur = (ex, ey)
        elif cmd == 'v':
            # перша КТ = поточна точка
            cp2x, cp2y = seg[1]
            ex, ey     = seg[2]
            path.cubicTo(cur[0], cur[1], cp2x, cp2y, ex, ey)
            cur = (ex, ey)
        elif cmd == 'y':
            # друга КТ = кінцева точка
            cp1x, cp1y = seg[1]
            ex, ey     = seg[2]
            path.cubicTo(cp1x, cp1y, ex, ey, ex, ey)
            cur = (ex, ey)
        elif cmd == 'h':
            path.closeSubpath()

    return path


def build_bezier_path(pts: list) -> QPainterPath:
    """
    Fallback: будує шлях із плоского списку pts (лише кінцеві точки).
    Використовується тільки якщо 'path' відсутній у сирих даних pdfplumber.
    З'єднуємо точки прямими лініями — краще ніж нічого.
    """
    path = QPainterPath()
    if not pts:
        return path
    path.moveTo(pts[0][0], pts[0][1])
    for pt in pts[1:]:
        path.lineTo(pt[0], pt[1])
    return path


def is_circle_like(w: float, h: float, tol: float = 0.20) -> bool:
    """Повертає True, якщо пропорції bounding box близькі до квадрата (коло / еліпс)."""
    if max(w, h) == 0:
        return False
    return abs(w - h) < max(w, h) * tol

# ═══════════════════════════════════════════════════════════════════════════════
# ГРАФІЧНІ ЕЛЕМЕНТИ ПОЛОТНА (СЦЕНИ)
# ═══════════════════════════════════════════════════════════════════════════════
class InteractiveMixin:
    """
    Домішок (Mixin), що додає будь-якому графічному елементу властивості виділення.
    Автоматично змінює колір ліній при наведенні мишки або кліку.
    """
    def setup_interactive(self, raw_data, on_click_callback):
        self.raw_data = raw_data
        self.on_click = on_click_callback
        self.is_selected = False
        
        self.pen_default = QPen(QColor(0, 150, 255, 120), 2)
        self.setAcceptHoverEvents(True)
        if hasattr(self, 'setPen'):
            self.setPen(self.pen_default)

    def apply_new_settings(self, state_ref):
        """Оновлює колір, товщину та тип лінії для векторів PDF з бази даних"""
        from PyQt6.QtGui import QColor, QPen
        from PyQt6.QtCore import Qt
        
        is_visible = state_ref.load_setting("visible_vectors", True)
        if hasattr(self, 'setVisible'):
            self.setVisible(is_visible)

        c_hex = state_ref.load_setting("color_contour_vectors", "#ff0096ff")
        w = float(state_ref.load_setting("line_width_vectors", 1.0))
        s = state_ref.load_setting("line_style_vectors", "solid")
        
        PEN_STYLES = {
            'solid': Qt.PenStyle.SolidLine, 'dash': Qt.PenStyle.DashLine,
            'dot': Qt.PenStyle.DotLine, 'dashdot': Qt.PenStyle.DashDotLine, 'dashdotdot': Qt.PenStyle.DashDotDotLine
        }

        self.pen_default = QPen(QColor(c_hex), w, PEN_STYLES.get(s, Qt.PenStyle.SolidLine))
        
        if not self.is_selected and hasattr(self, 'setPen'):
            self.setPen(self.pen_default)

    def base_hover_enter(self):
        if not self.is_selected and hasattr(self, 'setPen'):
            hover_pen = QPen(self.pen_default)
            hover_pen.setColor(QColor(255, 0, 0, 200))
            hover_pen.setWidthF(self.pen_default.widthF() + 1.0)
            self.setPen(hover_pen)

    def base_hover_leave(self):
        if not self.is_selected and hasattr(self, 'setPen'): 
            self.setPen(self.pen_default)

    def base_mouse_press(self):
        if self.on_click: self.on_click(self)

    def set_selected(self, state):
        self.is_selected = state
        if hasattr(self, 'setPen'):
            if state:
                sel_pen = QPen(self.pen_default)
                sel_pen.setColor(QColor(0, 255, 0, 255))
                sel_pen.setWidthF(self.pen_default.widthF() + 1.0)
                self.setPen(sel_pen)
            else:
                self.setPen(self.pen_default)

# --- Спеціалізовані класи для векторних елементів сторінки ---
class InteractiveLine(QGraphicsLineItem, InteractiveMixin):
    def __init__(self, x0, y0, x1, y1, raw, cb):
        super().__init__(x0, y0, x1, y1)
        self.setup_interactive(raw, cb)
    def hoverEnterEvent(self, e): self.base_hover_enter(); super().hoverEnterEvent(e)
    def hoverLeaveEvent(self, e): self.base_hover_leave(); super().hoverLeaveEvent(e)
    def mousePressEvent(self, e): 
        if e.button() == Qt.MouseButton.LeftButton: self.base_mouse_press()
        super().mousePressEvent(e)

class InteractivePath(QGraphicsPathItem, InteractiveMixin):
    def __init__(self, path, raw, cb):
        super().__init__(path)
        self.setup_interactive(raw, cb)
    def hoverEnterEvent(self, e): self.base_hover_enter(); super().hoverEnterEvent(e)
    def hoverLeaveEvent(self, e): self.base_hover_leave(); super().hoverLeaveEvent(e)
    def mousePressEvent(self, e): 
        if e.button() == Qt.MouseButton.LeftButton: self.base_mouse_press()
        super().mousePressEvent(e)

     
class InteractiveRect(QGraphicsRectItem, InteractiveMixin):
    def __init__(self, x, y, w, h, raw, cb):
        super().__init__(x, y, w, h)
        self.setup_interactive(raw, cb)
    def hoverEnterEvent(self, e): self.base_hover_enter(); super().hoverEnterEvent(e)
    def hoverLeaveEvent(self, e): self.base_hover_leave(); super().hoverLeaveEvent(e)
    def mousePressEvent(self, e): 
        if e.button() == Qt.MouseButton.LeftButton: self.base_mouse_press()
        super().mousePressEvent(e)

class InteractiveEllipse(QGraphicsEllipseItem, InteractiveMixin):
    def __init__(self, x, y, w, h, raw, cb):
        super().__init__(x, y, w, h)
        self.setup_interactive(raw, cb)
    def hoverEnterEvent(self, e): self.base_hover_enter(); super().hoverEnterEvent(e)
    def hoverLeaveEvent(self, e): self.base_hover_leave(); super().hoverLeaveEvent(e)
    def mousePressEvent(self, e): 
        if e.button() == Qt.MouseButton.LeftButton: self.base_mouse_press()
        super().mousePressEvent(e)



def render_pdf_curve(raw_curve: dict, on_click_cb) -> 'QGraphicsItem':
    """
    Повертає графічний елемент для однієї кривої pdfplumber.

    Стратегія (пріоритет зверху вниз):
      1. Є 'path' сегменти ('m','c','l','h'…) — справжній Без'є через QPainterPath.
         Якщо контур замкнений і bounding box квадратний → InteractiveEllipse (чисте коло).
      2. Є 'pts' >= 4 — fallback Без'є (лише кінцеві точки, пряма апроксимація).
      3. Квадратний bounding box → InteractiveEllipse.
      4. Інакше → пунктирний InteractiveRect (bounding box).
    """
    x0 = raw_curve.get('x0', 0)
    y0 = raw_curve.get('y0', 0)
    w  = raw_curve.get('w', raw_curve.get('width', 0))
    h  = raw_curve.get('h', raw_curve.get('height', 0))
    path_segs = raw_curve.get('path', [])
    pts       = raw_curve.get('pts', [])

    arc_color = QColor(255, 20, 147, 200)

    raw_data = {
        'type': 'arc', 'dir': 'arc',
        'x0': x0, 'y0': y0, 'x1': x0 + w, 'y1': y0 + h,
        'length': max(w, h),
        'pts': pts,
        'path': path_segs  # <--- ВАЖЛИВО: ТЕПЕР МИ ПЕРЕДАЄМО КРИВУ ДАЛІ!
    }

    # Визначаємо замкненість: команда 'h' у path або перша/остання pts збігаються
    has_close = any(s[0] == 'h' for s in path_segs)

    # --- Варіант 1: справжній Без'є з PDF path-команд ---
    if path_segs:
        # Якщо замкнений + квадратний + містить ТІЛЬКИ криві (без 'l') → коло
        has_lines = any(s[0] == 'l' for s in path_segs)
        is_pure_curve = not has_lines and any(s[0] in ('c', 'v', 'y') for s in path_segs)
        if has_close and is_circle_like(w, h) and is_pure_curve:
            item = InteractiveEllipse(x0, y0, w, h, raw_data, on_click_cb)
            item.pen_default = QPen(arc_color, 2)
            item.setPen(item.pen_default)
            item.setBrush(QBrush(Qt.GlobalColor.transparent))
            item.setZValue(0)
            return item

        bezier = build_bezier_path_from_segments(path_segs)
        item = InteractivePath(bezier, raw_data, on_click_cb)
        item.pen_default = QPen(arc_color, 1.5)
        item.setPen(item.pen_default)
        item.setZValue(0)
        return item

    # --- Варіант 2: fallback через pts ---
    if len(pts) >= 2:
        bezier = build_bezier_path(pts)
        item = InteractivePath(bezier, raw_data, on_click_cb)
        item.pen_default = QPen(arc_color, 1.5)
        item.setPen(item.pen_default)
        item.setZValue(0)
        return item

    # --- Варіант 3: квадратний bbox → еліпс ---
    if is_circle_like(w, h):
        item = InteractiveEllipse(x0, y0, w, h, raw_data, on_click_cb)
        item.pen_default = QPen(arc_color, 2)
        item.setPen(item.pen_default)
        item.setBrush(QBrush(Qt.GlobalColor.transparent))
        item.setZValue(0)
        return item

    # --- Варіант 4: пунктирний bounding box ---
    item = InteractiveRect(x0, y0, w, h, raw_data, on_click_cb)
    item.pen_default = QPen(arc_color, 1.5, Qt.PenStyle.DashLine)
    item.setPen(item.pen_default)
    item.setBrush(QBrush(QColor(255, 20, 147, 25)))
    item.setZValue(0)
    return item


class ValidationBox(QGraphicsRectItem, InteractiveMixin):
    def __init__(self, found_obj, cb, state_ref=None, on_geometry_changed_cb=None):
        # 1. Пріоритет на ui_rect (Union Bounding Box)
        if 'ui_rect' in found_obj.custom_zones:
            ur = found_obj.custom_zones['ui_rect']
            x, y, w, h = ur['x'], ur['y'], ur['w'], ur['h']
        else:
            x, y = float(found_obj.anchor.get('x', 0)), float(found_obj.anchor.get('y', 0))
            w, h = float(found_obj.anchor.get('width', 100)), float(found_obj.anchor.get('height', 100))
        
        self.aspect_ratio = abs(w / h) if h != 0 else 1.0
        
        if 'manual_rect' in found_obj.custom_zones:
            mr = found_obj.custom_zones['manual_rect']
            x, y, w, h = mr['x'], mr['y'], mr['w'], mr['h']
        else:
            w, h = max(abs(w), 1.0), max(abs(h), 1.0)
            
        super().__init__(x, y, w, h)
        
        self.on_geometry_changed_cb = on_geometry_changed_cb
        self.found_obj = found_obj
        self.status = getattr(found_obj, 'status', 'pending')
        self.setup_interactive(found_obj, cb)
        
        self.setAcceptHoverEvents(True)
        # ВИПРАВЛЕННЯ: Відключаємо ItemIsMovable, щоб уникнути конфліктів!
        self.setFlags(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable)
        self.setCacheMode(QGraphicsRectItem.CacheMode.NoCache) 
        self.state_ref = state_ref

        self.action_mode = None # Може бути 'resize' або 'move'
        self.resize_margin = 10
        self.start_rect = None
        self.start_pos = None
        self.is_isolated = False
        self.active_field = None

        if self.state_ref:
            self.apply_new_settings(self.state_ref)
        else:
            from PyQt6.QtGui import QBrush
            self.pen_pending = QPen(QColor(243, 156, 18), 4)
            self.brush_pending = QBrush(QColor(243, 156, 18, 64))
            self.pen_approved = QPen(QColor(39, 174, 96), 4)
            self.brush_approved = QBrush(QColor(39, 174, 96, 64))
            base_purple = QColor(155, 89, 182)
            self.ghost_pen = QPen(base_purple, 2, Qt.PenStyle.DashLine)
            self.ghost_brush = QBrush(QColor(155, 89, 182, 76))
            self.line_pen = QPen(QColor(231, 76, 60, 200), 2)
            self.set_status(self.status)

        if not hasattr(found_obj, 'text_fields'): found_obj.text_fields = {}
        label_text = found_obj.template_name
        self.label = QGraphicsTextItem(label_text, self)
        self.label.setDefaultTextColor(QColor(0, 0, 0))
        self.label.setPos(0, -22)

        # ── Створюємо TextZoneHandle для кожної ghost_zone ───────────────────
        self._zone_handles: list[TextZoneHandle] = []
        self._handles_visible = True
        self._zones_layer_visible = True
        self._rebuild_zone_handles()

        # Anchor dot — завжди поверх рамки
        self.setZValue(2)
        
    def boundingRect(self):
        """Розширюємо boundingRect щоб anchor та label не обрізалися."""
        r = super().boundingRect()
        margin = 25  # для anchor хрестика та label
        return r.adjusted(-margin, -margin, margin, margin)
    

    def _rebuild_zone_handles(self):
        """Видаляє старі ручки і створює нові за поточними ghost_zones + service_ghost_zones."""
        for h in self._zone_handles:
            if h.scene():
                h.scene().removeItem(h)
        self._zone_handles.clear()

        zones = self.found_obj.custom_zones.get('ghost_zones', [])
        sz_zones = self.found_obj.custom_zones.get('service_ghost_zones', [])
        
        zones_visible = self._handles_visible
        sz_visible = self._handles_visible
        if hasattr(self, 'state_ref') and self.state_ref:
            zones_visible = zones_visible and self.state_ref.load_setting("visible_zones", True)
            sz_visible = sz_visible and self.state_ref.load_setting("visible_service_zones", True)
        
        for idx, zone in enumerate(zones):
            handle = TextZoneHandle(zone, idx, self)
            handle.setVisible(zones_visible)
            handle._is_service = False
            self._zone_handles.append(handle)
        
        for idx, zone in enumerate(sz_zones):
            handle = ServiceZoneHandle(zone, idx, self)
            handle.setVisible(sz_visible)
            handle._is_service = True
            self._zone_handles.append(handle)

    def set_handles_visible(self, visible: bool):
        """Показує або ховає ручки за типом (text vs service) — кожен шар незалежно."""
        self._handles_visible = visible
        self._zones_layer_visible = visible
        
        # Окрема перевірка для кожного типу
        zones_layer_on = True
        sz_layer_on = True
        if hasattr(self, 'state_ref') and self.state_ref:
            zones_layer_on = self.state_ref.load_setting("visible_zones", True)
            sz_layer_on = self.state_ref.load_setting("visible_service_zones", True)
        
        for h in self._zone_handles:
            if getattr(h, '_is_service', False):
                h.setVisible(visible and sz_layer_on)
            else:
                h.setVisible(visible and zones_layer_on)
        
        if hasattr(self, 'label'):
            self.label.setVisible(visible)
        self.update()

    def _on_zone_moved(self):
        """
        Викликається TextZoneHandle після mouseRelease.
        Зберігає оновлені ratios в SQLite через колбек головного вікна.
        """
        if self.on_geometry_changed_cb:
            # Передаємо self і поточний rect без зміни геометрії рамки
            self.on_geometry_changed_cb(self, self.rect(), 'zone_edit')

    def _on_service_zone_moved(self):
        """Викликається ServiceZoneHandle після mouseRelease — оновити OCR для service zones."""
        if self.on_geometry_changed_cb:
            self.on_geometry_changed_cb(self, self.rect(), 'service_zone_edit')

    def paint(self, painter, option, widget):
        from PyQt6.QtCore import QRectF, QPointF
        from PyQt6.QtGui import QPen, QBrush
        from PyQt6.QtWidgets import QStyle
        option.state &= ~QStyle.StateFlag.State_Selected
        

        
        is_frame_visible    = True
        is_zones_visible    = True
        is_skeleton_visible = True
        is_anchor_visible   = True
        active_f = getattr(self, 'active_field', None)

        if hasattr(self, 'state_ref') and self.state_ref:
            if self.status == "pending":
                is_frame_visible = self.state_ref.load_setting("visible_frame_pending", True)
            else:
                is_frame_visible = self.state_ref.load_setting("visible_frame_approved", True)
            is_zones_visible    = self.state_ref.load_setting("visible_zones", True)
            is_skeleton_visible = self.state_ref.load_setting("visible_skeleton", True)
            is_anchor_visible   = self.state_ref.load_setting("visible_anchor", True)

        if is_frame_visible:
            super().paint(painter, option, widget)
        

        rect = self.rect()

        # ── Скелет ─────────────────────────────────────────────────────────────
        if is_skeleton_visible and 'ghost_skeleton' in self.found_obj.custom_zones:
            sk_pen = QPen(self.line_pen)
            if active_f:
                c = sk_pen.color(); c.setAlpha(int(c.alpha() * 0.15))
                sk_pen.setColor(c)

            painter.setPen(sk_pen)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            for seg in self.found_obj.custom_zones['ghost_skeleton']:
                seg_type = seg.get('type', 'line')

                if seg_type == 'path':
                    # Універсальний path — M/L/Q/C/A/Z команди
                    # Читаємо дані з пріоритетом (відносні path_ratios або стандартний path)
                    path_cmds = seg.get('path_ratios') or seg.get('path')
                    if path_cmds:
                        qpath = QPainterPath()
                        for cmd_data in path_cmds:
                            cmd = cmd_data[0]
                            args = cmd_data[1:]
                            
                            # Уніфікуємо будь-які координати (кортежі чи плоскі числа) у єдиний плоский список
                            flat_ratios = []
                            for arg in args:
                                if isinstance(arg, tuple):
                                    flat_ratios.extend([arg[0], arg[1]])
                                else:
                                    flat_ratios.append(arg)

                            if cmd == 'M' and len(flat_ratios) >= 2:
                                px = rect.x() + flat_ratios[0] * rect.width()
                                py = rect.y() + flat_ratios[1] * rect.height()
                                qpath.moveTo(px, py)
                            elif cmd == 'L' and len(flat_ratios) >= 2:
                                px = rect.x() + flat_ratios[0] * rect.width()
                                py = rect.y() + flat_ratios[1] * rect.height()
                                qpath.lineTo(px, py)
                            elif cmd == 'Q' and len(flat_ratios) >= 4:
                                cx = rect.x() + flat_ratios[0] * rect.width()
                                cy = rect.y() + flat_ratios[1] * rect.height()
                                ex = rect.x() + flat_ratios[2] * rect.width()
                                ey = rect.y() + flat_ratios[3] * rect.height()
                                qpath.quadTo(cx, cy, ex, ey)
                            elif cmd == 'C' and len(flat_ratios) >= 6:
                                c1x = rect.x() + flat_ratios[0] * rect.width()
                                c1y = rect.y() + flat_ratios[1] * rect.height()
                                c2x = rect.x() + flat_ratios[2] * rect.width()
                                c2y = rect.y() + flat_ratios[3] * rect.height()
                                ex  = rect.x() + flat_ratios[4] * rect.width()
                                ey  = rect.y() + flat_ratios[5] * rect.height()
                                qpath.cubicTo(c1x, c1y, c2x, c2y, ex, ey)
                            elif cmd == 'A' and len(flat_ratios) >= 2:
                                # SVG Arc → спрощена апроксимація лінією
                                ex = rect.x() + flat_ratios[-2] * rect.width()
                                ey = rect.y() + flat_ratios[-1] * rect.height()
                                qpath.lineTo(ex, ey)
                            elif cmd == 'Z':
                                qpath.closeSubpath()
                        
                        painter.drawPath(qpath)
                elif seg_type == 'image':
                    # Маркер зображення — пунктирна рамка з іконкою 🖼
                    ix = rect.x() + seg['rx0'] * rect.width()
                    iy = rect.y() + seg['ry0'] * rect.height()
                    iw = (seg['rx1'] - seg['rx0']) * rect.width()
                    ih = (seg['ry1'] - seg['ry0']) * rect.height()
                    img_pen = QPen(sk_pen)
                    img_pen.setStyle(Qt.PenStyle.DotLine)
                    painter.setPen(img_pen)
                    painter.setBrush(QBrush(QColor(100, 149, 237, 30)))
                    painter.drawRect(QRectF(ix, iy, iw, ih))
                    # Іконка по центру
                    from PyQt6.QtGui import QFont
                    fnt = QFont()
                    fnt.setPointSize(max(int(min(iw, ih) * 0.4), 6))
                    painter.setFont(fnt)
                    painter.setPen(sk_pen)
                    painter.drawText(QRectF(ix, iy, iw, ih), Qt.AlignmentFlag.AlignCenter, "🖼")

                elif seg_type in ('ellipse', 'arc'):
                    if seg.get('path'):
                        abs_path = []
                        for cmd, *args in seg['path']:
                            abs_path.append([cmd] + [(rect.x() + p[0]*rect.width(), rect.y() + p[1]*rect.height()) for p in args])
                        painter.drawPath(build_bezier_path_from_segments(abs_path))
                    elif seg.get('pts'):
                        abs_pts = [(rect.x() + p[0]*rect.width(), rect.y() + p[1]*rect.height()) for p in seg['pts']]
                        painter.drawPath(build_bezier_path(abs_pts))
                    else:
                        ex = rect.x() + seg['rx0'] * rect.width()
                        ey = rect.y() + seg['ry0'] * rect.height()
                        ew = (seg['rx1'] - seg['rx0']) * rect.width()
                        eh = (seg['ry1'] - seg['ry0']) * rect.height()
                        
                        if seg_type == 'arc':
                            arc_pen = QPen(sk_pen); arc_pen.setStyle(Qt.PenStyle.DashLine)
                            painter.setPen(arc_pen)
                            painter.drawRect(QRectF(ex, ey, ew, eh))
                            painter.setPen(sk_pen)
                        else:
                            painter.drawEllipse(QRectF(ex, ey, ew, eh))
                else:
                    lx0 = rect.x() + seg['rx0'] * rect.width()
                    ly0 = rect.y() + seg['ry0'] * rect.height()
                    lx1 = rect.x() + seg['rx1'] * rect.width()
                    ly1 = rect.y() + seg['ry1'] * rect.height()
                    painter.drawLine(QPointF(lx0, ly0), QPointF(lx1, ly1))

        # ── Текстові зони (якщо ручки сховані) ────────────────────────────────
        zones_layer = getattr(self, '_zones_layer_visible', True)
        if is_zones_visible and zones_layer and 'ghost_zones' in self.found_obj.custom_zones:
            handles_active = getattr(self, '_handles_visible', True)
            if not handles_active:                
                for zone in self.found_obj.custom_zones['ghost_zones']:
                    alpha_mult = 1.0
                    if active_f and zone['field'] != active_f:
                        alpha_mult = 0.15
                    z_pen   = QPen(self.ghost_pen)
                    z_brush = QBrush(self.ghost_brush)
                    cp = z_pen.color();   cp.setAlpha(int(cp.alpha() * alpha_mult))
                    cb = z_brush.color(); cb.setAlpha(int(cb.alpha() * alpha_mult))
                    z_pen.setColor(cp); z_brush.setColor(cb)
                    painter.setPen(z_pen); painter.setBrush(z_brush)
                    zx = rect.x() + zone['rx0'] * rect.width()
                    zy = rect.y() + zone['ry0'] * rect.height()
                    zw = (zone['rx1'] - zone['rx0']) * rect.width()
                    zh = (zone['ry1'] - zone['ry0']) * rect.height()
                    painter.drawRect(QRectF(zx, zy, zw, zh).normalized())
        # ── Service Zones (коли handles сховані) ─────────────────────────────
        is_sz_visible = True
        if hasattr(self, 'state_ref') and self.state_ref:
            is_sz_visible = self.state_ref.load_setting("visible_service_zones", True)
        
        if is_sz_visible and 'service_ghost_zones' in self.found_obj.custom_zones:
            handles_active = getattr(self, '_handles_visible', True)
            if not handles_active:
                sz_pen = getattr(self, 'sz_pen', QPen(QColor(22, 160, 133), 1.5, Qt.PenStyle.DashLine))
                sz_brush = getattr(self, 'sz_brush', QBrush(QColor(26, 188, 156, 60)))
                painter.setPen(sz_pen)
                painter.setBrush(sz_brush)
                for sz in self.found_obj.custom_zones['service_ghost_zones']:
                    zx = rect.x() + sz['rx0'] * rect.width()
                    zy = rect.y() + sz['ry0'] * rect.height()
                    zw = (sz['rx1'] - sz['rx0']) * rect.width()
                    zh = (sz['ry1'] - sz['ry0']) * rect.height()
                    painter.drawRect(QRectF(zx, zy, zw, zh).normalized())
        # ── Хрестик точки захоплення — малюємо ОСТАННІМ, Z поверх усього ──
        if is_anchor_visible:
            anchor_pos = self.found_obj.custom_zones.get('anchor_pos')
            if anchor_pos:
                ui = self.found_obj.custom_zones.get('ui_rect', {})
                ux  = ui.get('x', rect.x())
                uy  = ui.get('y', rect.y())
                uw  = max(ui.get('w', rect.width()),  1)
                uh  = max(ui.get('h', rect.height()), 1)

                cx = rect.x() + ((anchor_pos['x'] - ux) / uw) * rect.width()
                cy = rect.y() + ((anchor_pos['y'] - uy) / uh) * rect.height()

                arm = 8

                # Читаємо налаштування з state_ref
# Читаємо налаштування з state_ref
                if hasattr(self, 'state_ref') and self.state_ref:
                    anchor_color = QColor(self.state_ref.load_setting("color_contour_anchor", "#ffdc1414"))
                    anchor_w     = float(self.state_ref.load_setting("line_width_anchor", 2.0))
                    anchor_s     = self.state_ref.load_setting("line_style_anchor", "solid")
                else:
                    anchor_color = QColor(220, 20, 20)
                    anchor_w     = 2.0
                    anchor_s     = "solid"

                PEN_STYLES = {
                    'solid': Qt.PenStyle.SolidLine, 'dash': Qt.PenStyle.DashLine,
                    'dot': Qt.PenStyle.DotLine, 'dashdot': Qt.PenStyle.DashDotLine, 'dashdotdot': Qt.PenStyle.DashDotDotLine
                }

                cross_pen = QPen(anchor_color, anchor_w, PEN_STYLES.get(anchor_s, Qt.PenStyle.SolidLine))
                cross_pen.setCosmetic(True)
                painter.setPen(cross_pen)

                painter.setBrush(QBrush(Qt.GlobalColor.transparent))
                painter.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
                painter.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))

                circle_pen = QPen(anchor_color, max(anchor_w - 0.5, 1.0), PEN_STYLES.get(anchor_s, Qt.PenStyle.SolidLine))
                circle_pen.setCosmetic(True)
                painter.setPen(circle_pen)
                painter.drawEllipse(QPointF(cx, cy), 5.0, 5.0)
    
    
    def set_status(self, new_status):
        self.status = new_status
        if self.status == "approved":
            self.setPen(self.pen_approved); self.setBrush(self.brush_approved)
        else:
            self.setPen(self.pen_pending); self.setBrush(self.brush_pending)

    def hoverMoveEvent(self, e):
        pos = e.pos()
        rect = self.rect()
        near_right = abs(pos.x() - rect.right()) < self.resize_margin
        near_bottom = abs(pos.y() - rect.bottom()) < self.resize_margin
        
        if near_right and near_bottom: self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif near_right: self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif near_bottom: self.setCursor(Qt.CursorShape.SizeVerCursor)
        else: self.setCursor(Qt.CursorShape.OpenHandCursor) # Рука для переміщення
            
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e):
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverLeaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            
            self.base_mouse_press()
            self.start_rect = self.rect()
            self.start_pos = e.scenePos()
            
            # ВИПРАВЛЕННЯ: Ручне керування режимами
            if self.cursor().shape() != Qt.CursorShape.OpenHandCursor:
                self.action_mode = 'resize'
            else:
                self.action_mode = 'move'
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.action_mode:
            dx = e.scenePos().x() - self.start_pos.x()
            dy = e.scenePos().y() - self.start_pos.y()
            
            if self.action_mode == 'resize':
                shape = self.cursor().shape()
                new_width = self.start_rect.width()
                new_height = self.start_rect.height()
                
                if shape == Qt.CursorShape.SizeHorCursor or shape == Qt.CursorShape.SizeFDiagCursor:
                    new_width = max(10, self.start_rect.width() + dx)
                    new_height = new_width / self.aspect_ratio
                elif shape == Qt.CursorShape.SizeVerCursor:
                    new_height = max(10, self.start_rect.height() + dy)
                    new_width = new_height * self.aspect_ratio
                self.setRect(self.start_rect.x(), self.start_rect.y(), new_width, new_height)
                
            elif self.action_mode == 'move':
                # Ручно змінюємо координати прямокутника (без ItemIsMovable)
                self.setRect(self.start_rect.x() + dx, self.start_rect.y() + dy, self.start_rect.width(), self.start_rect.height())
                
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self.action_mode:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.action_mode == 'move' else Qt.CursorShape.ArrowCursor)
            
            final_rect = self.rect()
            
            # Не recreate якщо позиція не змінилась (простий клік)
            moved = (abs(final_rect.x() - self.start_rect.x()) > 1 or
                     abs(final_rect.y() - self.start_rect.y()) > 1 or
                     abs(final_rect.width() - self.start_rect.width()) > 1 or
                     abs(final_rect.height() - self.start_rect.height()) > 1)
            
            if moved and self.on_geometry_changed_cb:
                self.on_geometry_changed_cb(self, final_rect, self.action_mode)
                
        self.action_mode = None

    def apply_new_settings(self, state_ref):
        from PyQt6.QtGui import QBrush

        PEN_STYLES = {
            'solid':      Qt.PenStyle.SolidLine,
            'dash':       Qt.PenStyle.DashLine,
            'dot':        Qt.PenStyle.DotLine,
            'dashdot':    Qt.PenStyle.DashDotLine,
            'dashdotdot': Qt.PenStyle.DashDotDotLine,
        }

        def _pen(color_key, def_hex, width_key, def_w, style_key, def_s):
            c = QColor(state_ref.load_setting(color_key, def_hex))
            w = float(state_ref.load_setting(width_key, def_w))
            s = PEN_STYLES.get(state_ref.load_setting(style_key, def_s), Qt.PenStyle.SolidLine)
            return QPen(c, w, s)

        def _brush(color_key, def_hex):
            return QBrush(QColor(state_ref.load_setting(color_key, def_hex)))

        self.state_ref = state_ref

        # Рамки — pending / approved (використовують frame_pending/frame_approved ключі)
        self.pen_pending    = _pen("color_contour_frame_pending",  "#fff39c12",
                                   "line_width_frame_pending",   2.0,
                                   "line_style_frame_pending",   "solid")
        self.brush_pending  = _brush("color_fill_frame_pending",  "#40f39c12")

        self.pen_approved   = _pen("color_contour_frame_approved", "#ff27ae60",
                                   "line_width_frame_approved",  2.0,
                                   "line_style_frame_approved",  "solid")
        self.brush_approved = _brush("color_fill_frame_approved", "#4027ae60")

        # Скелет
        self.line_pen = _pen("color_contour_skeleton", "#ffe74c3c",
                             "line_width_skeleton",   1.5,
                             "line_style_skeleton",   "solid")

        # Текстові зони
        self.ghost_pen   = _pen("color_contour_zones", "#ff9b59b6",
                                "line_width_zones",   1.5,
                                "line_style_zones",   "dash")
        self.ghost_brush = _brush("color_fill_zones", "#4d9b59b6")

        # Сервісні зони
        self.sz_pen   = _pen("color_contour_service_zones", "#ff16a085",
                             "line_width_service_zones",   1.5,
                             "line_style_service_zones",   "dash")
        
        self.sz_brush = _brush("color_fill_service_zones", "#4d1abc9c")
        # Визначаємо чи рамка видима
        if state_ref:
            if self.status == "pending":
                frame_on = state_ref.load_setting("visible_frame_pending", True)
            else:
                frame_on = state_ref.load_setting("visible_frame_approved", True)
            zones_on = state_ref.load_setting("visible_zones", True)
            self._zones_layer_visible = zones_on
        else:
            frame_on = True

        if frame_on:
            self.set_status(self.status)
        else:
            # Невидимий пен з мінімальною шириною щоб boundingRect не обрізав anchor
            invisible_pen = QPen(QColor(0, 0, 0, 0), 20)
            self.setPen(invisible_pen)
            self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.update()



# ═══════════════════════════════════════════════════════════════════════════════
# ЖИВИЙ ПРИМАРНИЙ КОНТУР (Live Ghost Preview) — режим конфігурації шаблону
# ═══════════════════════════════════════════════════════════════════════════════

class GhostPreviewItem(QGraphicsPathItem):
    """
    Елемент на Canvas для live-preview шаблону під час його створення.
    Динамічно зчитує кольори та стилі з Налаштувань Відображення (SQLite).
    """
    def __init__(self, scene, state_ref=None):
        super().__init__()
        self._scene = scene
        self.state_ref = state_ref
        self.setZValue(1000)
        scene.addItem(self)

    def _update_styles(self):
        """Отримує актуальні кольори з БД перед кожним малюванням."""
        PEN_STYLES = {
            'solid': Qt.PenStyle.SolidLine, 'dash': Qt.PenStyle.DashLine,
            'dot': Qt.PenStyle.DotLine, 'dashdot': Qt.PenStyle.DashDotLine, 'dashdotdot': Qt.PenStyle.DashDotDotLine
        }
        
        if not self.state_ref: return
        
        c_sk = QColor(self.state_ref.load_setting("color_contour_skeleton", "#ffe74c3c"))
        w_sk = float(self.state_ref.load_setting("line_width_skeleton", 1.5))
        s_sk = PEN_STYLES.get(self.state_ref.load_setting("line_style_skeleton", "solid"), Qt.PenStyle.SolidLine)
        self._pen_skeleton = QPen(c_sk, w_sk, s_sk)

        c_z = QColor(self.state_ref.load_setting("color_contour_zones", "#ff9b59b6"))
        f_z = QColor(self.state_ref.load_setting("color_fill_zones", "#4d9b59b6"))
        w_z = float(self.state_ref.load_setting("line_width_zones", 1.5))
        s_z = PEN_STYLES.get(self.state_ref.load_setting("line_style_zones", "dash"), Qt.PenStyle.DashLine)
        self._pen_zone = QPen(c_z, w_z, s_z)
        self._brush_zone = QBrush(f_z)
        self._text_color = c_z

        c_a = QColor(self.state_ref.load_setting("color_contour_anchor", "#ffdc1414"))
        w_a = float(self.state_ref.load_setting("line_width_anchor", 2.0))
        s_a = PEN_STYLES.get(self.state_ref.load_setting("line_style_anchor", "solid"), Qt.PenStyle.SolidLine)
        self._pen_anchor = QPen(c_a, w_a, s_a)

        c_bb = QColor(self.state_ref.load_setting("color_contour_frame_pending", "#fff39c12"))
        self._pen_bbox = QPen(c_bb, 1.5, Qt.PenStyle.DotLine)
        c_bb_fill = QColor(c_bb); c_bb_fill.setAlpha(20)
        self._brush_bbox = QBrush(c_bb_fill)

    def update_preview(self, abs_lines: list, ghost_zones: list, anchor_pos: dict | None, ui_rect: dict | None, service_zones: list = None):
        self._update_styles() 
        
        # ЧИТАЄМО СТАН МЕНЕДЖЕРА ШАРІВ
        v_sk = self.state_ref.load_setting("visible_skeleton", True) if self.state_ref else True
        v_bb = self.state_ref.load_setting("visible_frame_pending", True) if self.state_ref else True
        v_z  = self.state_ref.load_setting("visible_zones", True) if self.state_ref else True
        v_a  = self.state_ref.load_setting("visible_anchor", True) if self.state_ref else True
        
        path = QPainterPath()

        # 1. Малюємо Скелет (лінії та КРИВІ)
        if v_sk:
            for seg in abs_lines:
                t = seg.get('type', 'line')
                x0, y0, x1, y1 = seg['x0'], seg['y0'], seg['x1'], seg['y1']
                
                if t in ('ellipse', 'arc'):
                    if seg.get('path'):
                        path.addPath(build_bezier_path_from_segments(seg['path']))
                    elif seg.get('pts'):
                        path.addPath(build_bezier_path(seg['pts']))
                    else:
                        if t == 'arc':
                            # Якщо дуга не має Без'є - малюємо лише рамку габаритів
                            path.addRect(QRectF(x0, y0, x1 - x0, y1 - y0))
                        else:
                            # Для справжніх еліпсів - малюємо овал
                            path.addEllipse(QRectF(x0, y0, x1 - x0, y1 - y0))
                else:
                    path.moveTo(x0, y0)
                    path.lineTo(x1, y1)

        self.setPath(path)
        if hasattr(self, '_pen_skeleton'): self.setPen(self._pen_skeleton)

        for child in list(self.childItems()):
            self._scene.removeItem(child)

        # 2. Малюємо Габаритну рамку
        if v_bb and ui_rect and hasattr(self, '_pen_bbox'):
            bbox = QGraphicsRectItem(ui_rect['x'], ui_rect['y'], ui_rect['w'], ui_rect['h'], self)
            bbox.setPen(self._pen_bbox); bbox.setBrush(self._brush_bbox); bbox.setZValue(1001)

        # 3. Малюємо Текстові зони
        if v_z:
            for zone in ghost_zones:
                zx0, zy0 = min(zone['x0'], zone['x1']), min(zone['y0'], zone['y1'])
                zw, zh   = abs(zone['x1'] - zone['x0']), abs(zone['y1'] - zone['y0'])
                rect_item = QGraphicsRectItem(zx0, zy0, zw, zh, self)
                if hasattr(self, '_pen_zone'):
                    rect_item.setPen(self._pen_zone); rect_item.setBrush(self._brush_zone)
                rect_item.setZValue(1002)
                lbl = QGraphicsTextItem(zone.get('field', 'unknown'), rect_item)
                if hasattr(self, '_text_color'): lbl.setDefaultTextColor(self._text_color)
                font = lbl.font(); font.setPointSize(7); font.setBold(True)
                lbl.setFont(font); lbl.setPos(0, -18)
        # 3.5. Service Zones (teal)
        v_sz = self.state_ref.load_setting("visible_service_zones", True) if self.state_ref else True
        if v_sz and service_zones:
            # Стилі service_zones з БД
            sz_color = QColor(self.state_ref.load_setting("color_contour_service_zones", "#ff16a085")) if self.state_ref else QColor(22, 160, 133)
            sz_fill = QColor(self.state_ref.load_setting("color_fill_service_zones", "#4d1abc9c")) if self.state_ref else QColor(26, 188, 156, 60)
            sz_w = float(self.state_ref.load_setting("line_width_service_zones", 1.5)) if self.state_ref else 1.5
            PEN_STYLES = {'solid': Qt.PenStyle.SolidLine, 'dash': Qt.PenStyle.DashLine,
                          'dot': Qt.PenStyle.DotLine, 'dashdot': Qt.PenStyle.DashDotLine}
            sz_style_key = self.state_ref.load_setting("line_style_service_zones", "dash") if self.state_ref else "dash"
            sz_style = PEN_STYLES.get(sz_style_key, Qt.PenStyle.DashLine)
            sz_pen = QPen(sz_color, sz_w, sz_style)
            sz_brush = QBrush(sz_fill)
            
            for zone in service_zones:
                zx0, zy0 = min(zone['x0'], zone['x1']), min(zone['y0'], zone['y1'])
                zw, zh = abs(zone['x1'] - zone['x0']), abs(zone['y1'] - zone['y0'])
                rect_item = QGraphicsRectItem(zx0, zy0, zw, zh, self)
                rect_item.setPen(sz_pen)
                rect_item.setBrush(sz_brush)
                rect_item.setZValue(1002)
                
        # 4. Точка Захоплення
        if v_a and anchor_pos and hasattr(self, '_pen_anchor'):
            ax, ay = anchor_pos['x'], anchor_pos['y']
            arm = 10
            for dx1, dy1, dx2, dy2 in [(-arm, 0, arm, 0), (0, -arm, 0, arm)]:
                line = QGraphicsLineItem(ax+dx1, ay+dy1, ax+dx2, ay+dy2, self)
                line.setPen(self._pen_anchor); line.setZValue(1003)
            circ = QGraphicsEllipseItem(ax-5, ay-5, 10, 10, self)
            circ.setPen(self._pen_anchor); circ.setZValue(1003)

    def hide_preview(self):
        self.setPath(QPainterPath())
        for child in list(self.childItems()):
            self._scene.removeItem(child)

class ZoomableView(QGraphicsView):
    """
    Покращений компонент перегляду сцени (Полотно). 
    Підтримує масштабування коліщатком мишки та режим малювання текстової зони (Rubber Band).
    """
    # Сигнал, що передає координати намальованої зони в головне вікно
    rect_drawn = pyqtSignal(float, float, float, float)
    point_snapped = pyqtSignal(float, float) # НОВИЙ СИГНАЛ ДЛЯ ТОЧКИ ЗАХОПЛЕННЯ

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        
        self.drawing_mode = False
        self.start_pos = None
        self.temp_rect = None

        # --- Змінні для режиму прилипання (OSNAP) ---
        self.snapping_mode = False
        self.snap_marker = None
        self.current_snap_pos = None

    def start_snapping(self):
        """Запускає режим вказування точки з прилипанням (V5.2 Fix)"""
        self.snapping_mode = True
        
        # Перевірка, чи маркер ще існує в пам'яті C++
        marker_ok = False
        try:
            if self.snap_marker and self.snap_marker.scene():
                marker_ok = True
        except RuntimeError:
            marker_ok = False

        if not marker_ok:
            self.snap_marker = QGraphicsEllipseItem(-4, -4, 8, 8)
            self.snap_marker.setBrush(QColor(231, 76, 60, 200)) # Червоний маркер
            self.snap_marker.setPen(QPen(QColor(192, 57, 43), 2))
            self.snap_marker.setZValue(9999)
            self.scene().addItem(self.snap_marker)
            
        self.snap_marker.show()
        self.setCursor(Qt.CursorShape.CrossCursor)

    def start_drawing(self):
        """Вмикає режим 'хрестика' для виділення текстової зони."""
        self.drawing_mode = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def stop_drawing(self):
        """Повертає стандартний режим перетягування сцени."""
        self.drawing_mode = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if self.temp_rect:
            self.scene().removeItem(self.temp_rect)
            self.temp_rect = None

    def mousePressEvent(self, event):
        if self.snapping_mode and event.button() == Qt.MouseButton.LeftButton:
            # Користувач клікнув, щоб зафіксувати точку
            pos = self.current_snap_pos if self.current_snap_pos else self.mapToScene(event.pos())
            self.snapping_mode = False
            if self.snap_marker: self.snap_marker.hide()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.point_snapped.emit(pos.x(), pos.y()) # Передаємо координати у головне вікно
            
        elif self.drawing_mode and event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = self.mapToScene(event.pos())
            self.temp_rect = QGraphicsRectItem()
            self.temp_rect.setPen(QPen(Qt.GlobalColor.magenta, 2, Qt.PenStyle.DashLine))
            self.temp_rect.setBrush(QColor(255, 0, 255, 50))
            self.scene().addItem(self.temp_rect)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.snapping_mode:
            cur_pos = self.mapToScene(event.pos())
            best_dist = float('inf')
            best_pt = None
            
            # Шукаємо найближчу ключову точку (кінці та середини ліній)
            for item in self.scene().items():
                if hasattr(item, 'raw_data') and item.raw_data:
                    raw = item.raw_data
                    pts = [
                        QPointF(raw.get('x0', 0), raw.get('y0', 0)), # Початок
                        QPointF(raw.get('x1', 0), raw.get('y1', 0)), # Кінець
                        QPointF((raw.get('x0', 0) + raw.get('x1', 0))/2, (raw.get('y0', 0) + raw.get('y1', 0))/2) # Середина
                    ]
                    for pt in pts:
                        dist = ((cur_pos.x() - pt.x())**2 + (cur_pos.y() - pt.y())**2)**0.5
                        if dist < best_dist:
                            best_dist, best_pt = dist, pt
                            
            # Динамічний поріг прилипання залежно від зуму (15 пікселів екрану)
            snap_thresh = 15.0 / self.transform().m11() if self.transform().m11() > 0 else 15.0
            
            if best_dist < snap_thresh and best_pt:
                self.current_snap_pos = best_pt
                self.snap_marker.setPos(best_pt)
            else:
                self.current_snap_pos = cur_pos
                self.snap_marker.setPos(cur_pos)
            
            if self.snap_marker: self.snap_marker.show()
            
        elif self.drawing_mode and self.start_pos and self.temp_rect:
            cur_pos = self.mapToScene(event.pos())
            x = min(self.start_pos.x(), cur_pos.x())
            y = min(self.start_pos.y(), cur_pos.y())
            w = abs(self.start_pos.x() - cur_pos.x())
            h = abs(self.start_pos.y() - cur_pos.y())
            self.temp_rect.setRect(x, y, w, h)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Завершення малювання та відправка координат для автоматичного розрахунку формул."""

        if self.drawing_mode and event.button() == Qt.MouseButton.LeftButton and self.temp_rect:
            r = self.temp_rect.rect()
            self.rect_drawn.emit(r.x(), r.y(), r.x() + r.width(), r.y() + r.height())
            self.scene().removeItem(self.temp_rect)
            self.temp_rect = None
            self.drawing_mode = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        """Логіка масштабування (зуму) навколо курсору мишки."""
        z_in = 1.25; z_out = 1 / z_in
        if event.angleDelta().y() > 0: self.scale(z_in, z_in)
        else: self.scale(z_out, z_out)