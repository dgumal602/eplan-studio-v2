import re

class ExprEvaluator:
    """Парсер математичних формул та умов з JSON-шаблонів."""
    def __init__(self, variables):
        self.variables = variables if variables else {}
        self.safe_env = {
            "min": min, "max": max, "abs": abs, "round": round, "len": len,
            "True": 1.0, "true": 1.0, "False": 0.0, "false": 0.0
        }
        self.safe_env.update(self.variables)

    def _prepare(self, expr):
        return re.sub(r'([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)', r"\1['\2']", str(expr).strip())

    def eval(self, expr):
        """Числовий вираз. Повертає float."""
        if not expr: return 0.0
        expr_str = str(expr).strip()
        if expr_str.lower() == 'true': return 1.0
        if expr_str.lower() == 'false': return 0.0
        expr_str = self._prepare(expr_str)
        try:
            return float(eval(expr_str, {"__builtins__": {}}, self.safe_env))
        except Exception as e:
            print(f"Помилка обчислення виразу '{expr}': {e}")
            return 0.0

    def eval_raw(self, expr):
        """Вираз без приведення до float. Підтримує рядки, числа, bool."""
        if not expr: return ""
        expr_str = str(expr).strip()
        if expr_str.lower() == 'true': return True
        if expr_str.lower() == 'false': return False
        expr_str = self._prepare(expr_str)
        try:
            return eval(expr_str, {"__builtins__": {}}, self.safe_env)
        except Exception as e:
            print(f"Помилка обчислення raw виразу '{expr}': {e}")
            return ""

    def eval_condition(self, expr):
        """Умова → bool. Підтримує ==, !=, <, >, <=, >=, in, and, or, not, рядки."""
        if not expr: return True
        expr_str = str(expr).strip()
        if expr_str.lower() == 'true' or expr_str == '': return True
        if expr_str.lower() == 'false': return False
        expr_str = self._prepare(expr_str)
        try:
            return bool(eval(expr_str, {"__builtins__": {}}, self.safe_env))
        except Exception as e:
            print(f"Помилка обчислення умови '{expr}': {e}")
            return False