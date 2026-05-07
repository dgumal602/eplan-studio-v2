import re

# ═══════════════════════════════════════════════════════════════════════════════
# МАТЕМАТИЧНИЙ ПАРСЕР
# ═══════════════════════════════════════════════════════════════════════════════
class ExprEvaluator:
    """
    Клас для безпечного обчислення математичних формул, заданих у JSON-шаблонах.
    Використовується інтерфейсом для розрахунку координат Anchor та Text Zones на льоту.
    """
    def __init__(self, variables):
        # Ініціалізуємо словник змінних, додаючи підтримку булевих значень (як 1.0 та 0.0)
        self.variables = variables if variables else {}
        self.safe_env = {
            "min": min, "max": max, "abs": abs, "round": round,
            "True": 1.0, "true": 1.0, "False": 0.0, "false": 0.0
        }
        self.safe_env.update(self.variables)

    def eval(self, expr):
        """
        Рекурсивно обчислює вираз.
        Автоматично перетворює точкову нотацію (anchor.x) на словникову (anchor['x'])
        для сумісності з вбудованою функцією Python eval().
        """
        if not expr: return 0.0
        expr_str = str(expr).strip()
        
        # Швидка обробка чистих булевих значень
        if expr_str.lower() == 'true': return 1.0
        if expr_str.lower() == 'false': return 0.0

        # Регулярний вираз: шукає "слово.слово" і замінює на "слово['слово']"
        expr_str = re.sub(r'([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)', r"\1['\2']", expr_str)
        try:
            # Виконуємо обчислення в безпечному середовищі (без доступу до системних функцій)
            return float(eval(expr_str, {"__builtins__": {}}, self.safe_env))
        except Exception as e:
            print(f"Помилка обчислення виразу '{expr}': {e}")
            return 0.0