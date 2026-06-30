import re
import json

input_file = "модуль ввод нового заказа.txt"
output_file = "product_designation_patterns.json"

with open(input_file, "r", encoding="utf-8") as f:
    lines = f.readlines()

# Mapping from designation variable to XML characteristic parameter
variable_to_xml_param = {
    "D_": "D0", "d1_": "D1", "d2_": "D2", "d3_": "D3", "d4_": "D4",
    "D1_": "D1", "D2_": "D2",
    "A_": "A0", "a1_": "A1", "a2_": "A2", "a3_": "A3", "a4_": "A4", "a5_": "A5",
    "B_": "B0", "b1_": "B1", "b2_": "B2", "b3_": "B3", "b4_": "B4", "b5_": "B5",
    "L_": "L0", "l1_": "L1", "l2_": "L2", "l3_": "L3", "l4_": "L4", "l5_": "L5", "l6_": "L6",
    "R_": "R0", "R_1": "R1", "R_2": "R2", "R_3": "R3",
    "u": "U0", "u1": "U1", "u2": "U2",
    "p": "P0", "p1": "P1", "p2": "P2", "p3": "P3",
    "t1": "t1", "t": "t",
    "z_": "z", "z": "z"
}

products = []
current_product = None

for i, line in enumerate(lines):
    line = line.strip()
    
    # Match product start: Если/ИначеЕсли ПродуктШапка = Справочники.асПродукция.XXX Тогда //comment
    m = re.match(r'(?:Если|ИначеЕсли)\s+ПродуктШапка\s*=\s*Справочники\.асПродукция\.(\w+)\s*Тогда(?:\s*//+\s*(.*))?', line)
    if m:
        current_product = {
            "product_ref": m.group(1),
            "comment": (m.group(2) or "").strip(),
            "designation_expression": None,
            "article": None,
            "group_code": None
        }
        continue
    
    if current_product is None:
        continue
    
    # Match designation line
    m = re.match(r'Обозначение\s*=\s*(.+);', line)
    if m:
        current_product["designation_expression"] = m.group(1).strip()
        continue
    
    # Match article group
    m = re.match(r'АртикулГруппы\s*=\s*"([^"]+)";', line)
    if m:
        current_product["group_code"] = m.group(1)
        continue
    
    # Match article product
    m = re.match(r'АртикулПродукции\s*=\s*"([^"]+)";', line)
    if m:
        current_product["article"] = m.group(1)
        # Derive XML parameters from designation expression
        expr = current_product.get("designation_expression", "")
        xml_params = set()
        for var, xml_param in variable_to_xml_param.items():
            # Use regex to match whole variable names
            if re.search(r'\b' + re.escape(var) + r'\b', expr):
                xml_params.add(xml_param)
        current_product["xml_parameters"] = sorted(xml_params)
        
        if current_product["designation_expression"]:
            products.append(current_product)
        current_product = None
        continue
    
    # If we hit a КонецЕсли without finding article, discard
    if line.startswith("КонецЕсли"):
        current_product = None

with open(output_file, "w", encoding="utf-8") as f:
    json.dump({
        "source": input_file,
        "function": "ОбозначениеПродукции",
        "count": len(products),
        "products": products
    }, f, ensure_ascii=False, indent=2)

print(f"Saved {len(products)} products to {output_file}")
