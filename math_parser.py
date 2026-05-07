import re
from typing import Any, Optional

class ExprEvaluator:
    """
    Мікро-парсер математичних формул, що використовуються у JSON-шаблонах.
    Дозволяє писати динамічні формули виду "box_h + row_ys[0] * 0.15" 
    та безпечно звертатися до властивостей (pins.top.count).
    """
    def __init__(self, context: dict):
        self.ctx = context

    def _resolve_var(self, name: str) -> Any:
        parts = name.split('.')
        val = self.ctx
        for part in parts:
            if isinstance(val, dict) and part in val:
                val = val[part]
            elif isinstance(val, list) and part in ('count', 'length'):
                val = len(val)
            else:
                raise KeyError(f"Не вдалося розкрити '{name}': атрибут '{part}' відсутній")
        return val

    def _resolve_index(self, list_name: str, index_expr: str) -> Any:
        lst = self._resolve_var(list_name)
        if not isinstance(lst, list):
            raise TypeError(f"'{list_name}' не є списком")

        idx = int(self.eval(index_expr))
        if idx < 0 or idx >= len(lst):
            raise IndexError(f"Індекс {idx} поза межами масиву '{list_name}'")
        return lst[idx]

    def eval(self, expr: str) -> float:
        expr = str(expr).strip()

        # Обробка логічних умов True/False
        expr_lower = expr.lower()
        if expr_lower == 'true': return 1.0
        if expr_lower == 'false': return 0.0

        try: return float(expr)
        except ValueError: pass

        func_match = re.match(r'^(min|max)\((.+)\)$', expr)
        if func_match:
            func_name, args_str = func_match.groups()
            args = self._split_args(args_str)
            vals = [self.eval(a.strip()) for a in args]
            return min(vals) if func_name == 'min' else max(vals)

        idx_match = re.match(r'^([\w\.]+)\[(.+)\]$', expr)
        if idx_match:
            return self._resolve_index(idx_match.group(1), idx_match.group(2))

        for ops in [['+', '-'], ['*', '/']]:
            result = self._try_binary_op(expr, ops)
            if result is not None:
                return result

        try:
            return float(self._resolve_var(expr))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"Помилка парсингу '{expr}': {e}")

    def _try_binary_op(self, expr: str, ops: list) -> Optional[float]:
        depth = 0 
        for i in range(len(expr) - 1, 0, -1):
            ch = expr[i]
            if ch == ')': depth += 1
            elif ch == '(': depth -= 1
            elif depth == 0 and ch in ops:
                if ch == '-' and (i == 0 or expr[i-1] in '+-*/('): continue

                left, right = expr[:i].strip(), expr[i+1:].strip()
                if not left or not right: continue

                lval, rval = self.eval(left), self.eval(right)

                if ch == '+': return lval + rval
                elif ch == '-': return lval - rval
                elif ch == '*': return lval * rval
                elif ch == '/': return lval / rval if rval != 0 else 0.0

        return None 

    def _split_args(self, args_str: str) -> list:
        args, depth, current = [], 0, []
        for ch in args_str:
            if ch == '(': depth += 1
            elif ch == ')': depth -= 1
            if ch == ',' and depth == 0:
                args.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current: args.append(''.join(current).strip())
        return args

def eval_expr(expr: str, context: dict) -> float:
    return ExprEvaluator(context).eval(expr)
    