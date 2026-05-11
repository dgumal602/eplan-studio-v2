import re

class ExprEvaluator:
    """
    Клас для безпечного обчислення математичних формул та умов з JSON-шаблонів.
    """
    def __init__(self, variables):
        self.variables = variables if variables else {}
        self.safe_env = {
            "min": min, "max": max, "abs": abs, "round": round, "len": len,
            "True": 1.0, "true": 1.0, "False": 0.0, "false": 0.0
        }
        self.safe_env.update(self.variables)

    def _prepare_expr(self, expr):
        """Перетворює role.field на role['field'] для eval."""
        return re.sub(r'([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)', r"\1['\2']", str(expr).strip())

    def eval(self, expr):
        """Обчислює математичний вираз (повертає float)."""
        if not expr: return 0.0
        expr_str = str(expr).strip()
        if expr_str.lower() == 'true': return 1.0
        if expr_str.lower() == 'false': return 0.0
        expr_str = self._prepare_expr(expr_str)
        try:
            return float(eval(expr_str, {"__builtins__": {}}, self.safe_env))
        except Exception as e:
            print(f"Помилка обчислення виразу '{expr}': {e}")
            return 0.0

    def eval_condition(self, expr):
        """
        Обчислює умову (повертає bool).
        Підтримує: ==, !=, <, >, <=, >=, in, and, or, not, рядкові методи.
        Приклади:
          device_type == 'relay'
          'PLC' in device_type
          device_type != ''
          channel != '' and voltage > 12
        """
        if not expr: return True
        expr_str = str(expr).strip()
        if expr_str.lower() == 'true' or expr_str == '': return True
        if expr_str.lower() == 'false': return False
        expr_str = self._prepare_expr(expr_str)
        try:
            result = eval(expr_str, {"__builtins__": {}}, self.safe_env)
            return bool(result)
        except Exception as e:
            print(f"Помилка обчислення умови '{expr}': {e}")
            return False