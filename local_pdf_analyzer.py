import fitz  # PyMuPDF
import json
import os

def rgb_to_hex(color_int):
    if color_int is None: return "—"
    b = color_int & 255
    g = (color_int >> 8) & 255
    r = (color_int >> 16) & 255
    return f"#{r:02x}{g:02x}{b:02x}"

def analyze_pdf_local(file_path):
    if not os.path.exists(file_path):
        print(f"❌ Помилка: Файл '{file_path}' не знайдено.")
        return

    print(f"⏳ Відкриття та аналіз файлу: {file_path}...")
    doc = fitz.open(file_path)
    
    result = {
        "pageInfo": [],
        "summary": {"totalObjects": 0},
        "textObjects": [],
        "lineObjects": [],
        "curveObjects": [],
        "rectObjects": [],
        "imageObjects": [],
        "annotations": [], # Коментарі, форми, маркери
        "links": [],       # Гіперпосилання
        "invisibleObjects": [],
        "allTexts": set()
    }

    obj_id = 1

    for page_num, page in enumerate(doc):
        # 1. Інформація про сторінку
        rect = page.rect
        result["pageInfo"].append({
            "page": page_num + 1,
            "width": round(rect.width, 2),
            "height": round(rect.height, 2),
            "unit": "pt"
        })

        # 2. Текст
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            if "lines" in block:
                for line in block["lines"]:
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if not text: continue
                        
                        result["allTexts"].add(text)
                        
                        size = round(span.get("size", 0), 2)
                        color = rgb_to_hex(span.get("color"))
                        
                        is_invisible = (size < 2.0) or (color == "#ffffff")
                        
                        obj = {
                            "id": obj_id,
                            "page": page_num + 1,
                            "text": text,
                            "x": round(span["bbox"][0], 2),
                            "y": round(span["bbox"][1], 2),
                            "font": span.get("font", "Unknown"),
                            "size": size,
                            "color": color,
                            "visible": not is_invisible
                        }
                        result["textObjects"].append(obj)
                        
                        if is_invisible:
                            result["invisibleObjects"].append({
                                "id": obj_id,
                                "type": "Text",
                                "reason": f"Дрібний розмір ({size}pt) або колір ({color})",
                                "data": text
                            })
                        obj_id += 1

        # 3. Векторна графіка
        drawings = page.get_drawings()
        for draw in drawings:
            fill_color = rgb_to_hex(draw.get("fill")) if draw.get("fill") else "—"
            stroke_color = rgb_to_hex(draw.get("color")) if draw.get("color") else "—"
            
            for item in draw.get("items", []):
                geom_type = item[0]
                if geom_type == "l":
                    result["lineObjects"].append({
                        "id": obj_id, "page": page_num + 1,
                        "x0": round(item[1].x, 2), "y0": round(item[1].y, 2),
                        "x1": round(item[2].x, 2), "y1": round(item[2].y, 2),
                        "linewidth": round(draw.get("width", 1), 2), "color": stroke_color
                    })
                elif geom_type == "c":
                    result["curveObjects"].append({
                        "id": obj_id, "page": page_num + 1,
                        "bbox": f"[{round(draw.get('rect').x0, 2)}, {round(draw.get('rect').y0, 2)}, {round(draw.get('rect').x1, 2)}, {round(draw.get('rect').y1, 2)}]",
                        "fillColor": fill_color, "strokeColor": stroke_color
                    })
                elif geom_type == "re":
                    result["rectObjects"].append({
                        "id": obj_id, "page": page_num + 1,
                        "bbox": f"[{round(item[1].x0, 2)}, {round(item[1].y0, 2)}, {round(item[1].x1, 2)}, {round(item[1].y1, 2)}]",
                        "fillColor": fill_color, "strokeColor": stroke_color
                    })
                obj_id += 1

        # 4. Зображення
        images = page.get_images(full=True)
        for img in images:
            xref = img[0]
            base_image = doc.extract_image(xref)
            result["imageObjects"].append({
                "id": obj_id, "page": page_num + 1,
                "width": base_image.get("width"), "height": base_image.get("height"),
                "colorspace": base_image.get("colorspace", "—"), "extension": base_image.get("ext")
            })
            obj_id += 1

        # 5. АНОТАЦІЇ (Коментарі, виділення, поля форм)
        for annot in page.annots():
            subtype = annot.type[1] if annot.type else "Unknown"
            info = annot.info
            
            # Витягуємо вміст і прибираємо перенесення рядків для красивого виводу
            content = info.get("content", "").strip()
            author = info.get("title", "—")
            
            ann_data = {
                "id": obj_id,
                "page": page_num + 1,
                "subtype": subtype,
                "bbox": f"[{round(annot.rect.x0, 2)}, {round(annot.rect.y0, 2)}, {round(annot.rect.x1, 2)}, {round(annot.rect.y1, 2)}]",
                "author": author,
                "content": content,
                "subject": info.get("subject", "—"),
                "flags": annot.flags,
                "is_interactive": annot.is_interactive # True для полів вводу / кнопок
            }
            
            result["annotations"].append(ann_data)
            
            # Якщо анотація має текст, додаємо його до загального списку слів
            if content:
                result["allTexts"].add(content)
                
            obj_id += 1

        # 6. ПОСИЛАННЯ (Links)
        for link in page.get_links():
            result["links"].append({
                "id": obj_id,
                "page": page_num + 1,
                "kind": link.get("kind"),
                "uri": link.get("uri", "—"),
                "dest": str(link.get("to", "—")), # Внутрішнє посилання на сторінку
                "bbox": f"[{round(link.get('from').x0, 2)}, {round(link.get('from').y0, 2)}, {round(link.get('from').x1, 2)}, {round(link.get('from').y1, 2)}]"
            })
            obj_id += 1

    # Підсумки
    result["allTexts"] = list(result["allTexts"])
    result["summary"]["totalObjects"] = obj_id - 1

    doc.close()
    
    print_detailed_results(result, file_path)
    return result


def print_detailed_results(res, file_path):
    print("\n" + "═"*90)
    print(f"🔬 ДЕТАЛЬНИЙ ЛОКАЛЬНИЙ АНАЛІЗ PDF: {file_path}")
    print("═"*90)

    def print_section(title, items, formatter, limit=15):
        total = len(items)
        if total == 0: return
        print(f"\n{title} (Всього: {total})")
        print("-" * 90)
        for item in items[:limit]:
            print(formatter(item))
        if total > limit:
            print(f"  ... та ще {total - limit} об'єктів (див. JSON файл).")

    print(f"\n📋 ІНФОРМАЦІЯ ПРО ДОКУМЕНТ")
    for p in res["pageInfo"]:
        print(f"  Сторінка {p['page']}: {p['width']} × {p['height']} pt")
    print(f"  Всього знайдено об'єктів: {res['summary']['totalObjects']}")

    # Вивід Анотацій
    print_section(
        "💬 АНОТАЦІЇ ТА ФОРМИ (Коментарі, Виділення, Widget)", 
        res["annotations"], 
        lambda o: f"  [#{o['id']:<4}] Стор:{o['page']} | Тип: {o['subtype']:<10} | Автор: {o['author'][:12]:<12} | BBox: {o['bbox']:<30} | Текст: «{o['content'].replace(chr(10), ' ')[:40]}{'...' if len(o['content'])>40 else ''}»"
    )

    # Вивід Посилань
    print_section(
        "🔗 ПОСИЛАННЯ (Links)", 
        res["links"], 
        lambda o: f"  [#{o['id']:<4}] Стор:{o['page']} | Тип: {o['kind']:<5} | BBox: {o['bbox']:<30} | Куди: {o['uri'] if o['uri'] != '—' else 'Стор. ' + o['dest']}"
    )

    print_section(
        "📝 ТЕКСТОВІ ОБ'ЄКТИ", 
        res["textObjects"], 
        lambda o: f"  [#{o['id']:<4}] Стор:{o['page']} | Розмір: {o['size']:<4}pt | Колір: {o['color']:<7} | Текст: «{o['text'][:40]}{'...' if len(o['text'])>40 else ''}»",
        limit=10
    )

    print_section(
        "🖼️ ЗОБРАЖЕННЯ", 
        res["imageObjects"], 
        lambda o: f"  [#{o['id']:<4}] Стор:{o['page']} | Розмір: {o['width']}x{o['height']} px | Формат: {o['extension']}",
        limit=5
    )

    # Збереження у файл
    out_file = f"{os.path.splitext(file_path)[0]}_local_analysis.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    
    print("\n" + "═"*90)
    print(f"💾 Увесь дамп збережено у файл: {out_file}")

if __name__ == "__main__":
    # ВКАЖІТЬ ШЛЯХ ДО ВАШОГО PDF ФАЙЛУ ТУТ:
    TARGET_PDF = "test.pdf" 
    
    analyze_pdf_local(TARGET_PDF)


if __name__ == "__main__":
    # ВКАЖІТЬ ШЛЯХ ДО ВАШОГО PDF ФАЙЛУ ТУТ:
    TARGET_PDF = "Arc_test2.pdf" 
    
    analyze_pdf_local(TARGET_PDF)