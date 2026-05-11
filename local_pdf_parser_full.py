import fitz  # PyMuPDF
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

def analyze_pdf_ultimate(file_path):
    if not os.path.exists(file_path):
        print(f"❌ Файл не знайдено: {file_path}")
        return

    print(f"⏳ Розпочато глибокий парсинг файлу: {file_path}...")
    doc = fitz.open(file_path)
    
    # Витягуємо сирий словник всього документа (Catalog)
    catalog_xref = doc.pdf_catalog()
    
    full_data = {
        "document_info": {
            "metadata": doc.metadata,
            "total_pages": len(doc),
            "is_encrypted": doc.is_encrypted,
            "is_form": doc.is_form_pdf,
            "raw_catalog_dict": doc.xref_object(catalog_xref) if catalog_xref else None
        },
        "pages": []
    }

    for page_num, page in enumerate(doc):
        # Отримуємо сирий словник самої сторінки
        raw_page_dict = doc.xref_object(page.xref)

        page_dict = {
            "page_number": page_num + 1,
            "xref": page.xref,
            "raw_page_dictionary": raw_page_dict, # <-- СИРІ ДАНІ СТОРІНКИ
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

        # --- 1. ТЕКСТ (включаючи координати, шрифти, розміри) ---
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

        # --- 2. ВЕКТОРНА ГРАФІКА (Лінії, криві) ---
        drawings = page.get_drawings()
        for d in drawings:
            page_dict["objects"]["drawings"].append({
                "type": "path",
                "bbox": [d["rect"].x0, d["rect"].y0, d["rect"].x1, d["rect"].y1],
                "items": [str(item) for item in d["items"]],
                "fill_color": d.get("fill"),
                "stroke_color": d.get("color"),
                "width": d.get("width"),
                "opacity": d.get("fill_opacity", 1.0),
                "lineCap": d.get("lineCap"),
                "lineJoin": d.get("lineJoin"),
                "dashes": d.get("dashes")
            })

        # --- 3. ЗОБРАЖЕННЯ (+ сирі дані) ---
        for img in page.get_images(full=True):
            xref = img[0]
            base_image = doc.extract_image(xref)
            page_dict["objects"]["images"].append({
                "xref": xref,
                "raw_image_dictionary": doc.xref_object(xref), # <-- СИРІ ДАНІ КАРТИНКИ
                "width": base_image["width"],
                "height": base_image["height"],
                "bits_per_component": base_image["samples"],
                "colorspace": base_image["colorspace"],
                "extension": base_image["ext"],
                "size_bytes": len(base_image["image"])
            })

        # --- 4. АНОТАЦІЇ (+ сирі дані) ---
        for annot in page.annots():
            annot_keys = {}
            for key in ["content", "title", "subject", "id", "modDate", "creationDate"]:
                val = annot.info.get(key)
                if val: annot_keys[key] = val
            
            page_dict["objects"]["annotations"].append({
                "xref": annot.xref,
                "raw_annotation_dictionary": doc.xref_object(annot.xref), # <-- СИРІ ДАНІ АНОТАЦІЇ
                "type": annot.type[1] if annot.type else "Unknown",
                "bbox": [annot.rect.x0, annot.rect.y0, annot.rect.x1, annot.rect.y1],
                "flags": annot.flags,
                "is_interactive": annot.is_interactive,
                "metadata": annot_keys,
                "colors": {
                    "stroke": annot.colors.get("stroke"),
                    "fill": annot.colors.get("fill")
                }
            })

        # --- 5. ПОСИЛАННЯ (+ сирі дані, якщо є) ---
        for link in page.get_links():
            link_xref = link.get("xref")
            page_dict["objects"]["links"].append({
                "xref": link_xref,
                "raw_link_dictionary": doc.xref_object(link_xref) if link_xref else None, # <-- СИРІ ДАНІ ПОСИЛАННЯ
                "kind": link.get("kind"),
                "from_bbox": [link["from"].x0, link["from"].y0, link["from"].x1, link["from"].y1],
                "uri": link.get("uri"),
                "page_target": link.get("page"),
                "to_point": str(link.get("to"))
            })

        full_data["pages"].append(page_dict)

    # Збереження у файл
    output_filename = f"{os.path.splitext(file_path)[0]}_ULTIMATE_DUMP.json"
    
    # Використовуємо default=str на випадок, якщо трапляться нетипові об'єкти
    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✅ Успіх! Глибокий аналіз завершено.")
    print(f"📊 Всього сторінок оброблено: {len(doc)}")
    print(f"💾 Всі параметри + СИРІ PDF-словники збережено у файл: {output_filename}")
    
    doc.close()

if __name__ == "__main__":
    # ВКАЖІТЬ ШЛЯХ ДО ВАШОГО PDF ФАЙЛУ ТУТ:
    TARGET_PDF = "Arc_test2.pdf" 
    
    analyze_pdf_ultimate(TARGET_PDF)
