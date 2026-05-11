import fitz  # PyMuPDF
#python
import json
import os

def get_text_flags(flags):
    """Декодує прапорці шрифту (жирний, курсив тощо)"""
    res = []
    if flags & 2**0: res.append("superscript")
    if flags & 2**1: res.append("italic")
    if flags & 2**2: res.append("serifed")
    if flags & 2**3: res.append("monospace")
    if flags & 2**4: res.append("bold")
    return res

def analyze_pdf_max_detail(file_path):
    if not os.path.exists(file_path):
        print(f"❌ Файл не знайдено: {file_path}")
        return

    doc = fitz.open(file_path)
    full_data = {
        "document_metadata": doc.metadata,
        "total_pages": len(doc),
        "pages": []
    }

    for page_num, page in enumerate(doc):
        page_dict = {
            "page_number": page_num + 1,
            "dimensions": {
                "width": page.rect.width,
                "height": page.rect.height,
                "rotation": page.rotation,
                "cropbox": [page.cropbox.x0, page.cropbox.y0, page.cropbox.x1, page.cropbox.y1],
                "mediabox": [page.mediabox.x0, page.mediabox.y0, page.mediabox.x1, page.mediabox.y1],
            },
            "objects": {
                "text_blocks": [],
                "drawings": [],
                "images": [],
                "annotations": [],
                "links": []
            }
        }

        # --- 1. ГЛИБОКИЙ АНАЛІЗ ТЕКСТУ ---
        blocks = page.get_text("dict")["blocks"]
        for b in blocks:
            if b["type"] == 0:  # Текстовий блок
                for line in b["lines"]:
                    for span in line["spans"]:
                        page_dict["objects"]["text_blocks"].append({
                            "text": span["text"],
                            "bbox": span["bbox"],
                            "origin": span["origin"],
                            "font": span["font"],
                            "size": span["size"],
                            "color_raw": span["color"],
                            "ascender": span["ascender"],
                            "descender": span["descender"],
                            "flags_id": span["flags"],
                            "flags_decoded": get_text_flags(span["flags"])
                        })

        # --- 2. ВЕКТОРНА ГРАФІКА (Всі шляхи) ---
        drawings = page.get_drawings()
        for d in drawings:
            # Витягуємо всі деталі малювання
            page_dict["objects"]["drawings"].append({
                "type": "path",
                "bbox": [d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1],
                "items": [str(item) for item in d["items"]], # координати ліній/кривих
                "fill_color": d.get("fill"),
                "stroke_color": d.get("color"),
                "width": d.get("width"),
                "opacity": d.get("fill_opacity", 1.0),
                "lineCap": d.get("lineCap"),
                "lineJoin": d.get("lineJoin"),
                "dashes": d.get("dashes")
            })

        # --- 3. ЗОБРАЖЕННЯ ТА ЇХ МЕТАДАНІ ---
        for img in page.get_images(full=True):
            xref = img[0]
            base_image = doc.extract_image(xref)
            page_dict["objects"]["images"].append({
                "xref": xref,
                "width": base_image["width"],
                "height": base_image["height"],
                "bits_per_component": base_image["samples"],
                "colorspace": base_image["colorspace"],
                "extension": base_image["ext"],
                "size_bytes": len(base_image["image"])
            })

        # --- 4. АНОТАЦІЇ (Повний дамп словника) ---
        for annot in page.annots():
            # Отримуємо всі доступні ключі з PDF-об'єкта анотації
            annot_keys = {}
            for key in ["content", "title", "subject", "id", "modDate", "creationDate"]:
                val = annot.info.get(key)
                if val: annot_keys[key] = val
            
            page_dict["objects"]["annotations"].append({
                "type": annot.type[1],
                "bbox": [annot.rect.x0, annot.rect.y0, annot.rect.x1, annot.rect.y1],
                "flags": annot.flags,
                "is_interactive": annot.is_interactive,
                "metadata": annot_keys,
                "colors": {
                    "stroke": annot.colors.get("stroke"),
                    "fill": annot.colors.get("fill")
                }
            })

        # --- 5. ПОСИЛАННЯ ---
        for link in page.get_links():
            page_dict["objects"]["links"].append({
                "kind": link["kind"],
                "from_bbox": [link["from"].x0, link["from"].y0, link["from"].x1, link["from"].y1],
                "uri": link.get("uri"),
                "page_target": link.get("page"),
                "to_point": str(link.get("to"))
            })

        full_data["pages"].append(page_dict)

    # Збереження у файл
    output_filename = f"{os.path.splitext(file_path)[0]}_FULL_EXPORT.json"
    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Глибокий аналіз завершено!")
    print(f"📊 Всього сторінок оброблено: {len(doc)}")
    print(f"💾 Всі параметри об'єктів збережено у файл: {output_filename}")
    
    doc.close()

if __name__ == "__main__":
    # Вкажіть ім'я вашого файлу
    FILE_TO_ANALYZE = "Arc_test2.pdf" 
    analyze_pdf_max_detail(FILE_TO_ANALYZE)

