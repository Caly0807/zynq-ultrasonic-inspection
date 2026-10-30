from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
font_path = ROOT / "vendor" / "noto" / "NotoSansSC-Regular.otf"
text = (ROOT / "src" / "ut_ui.c").read_text(encoding="utf-8")
codepoints = sorted(set(range(32, 127)) | {ord(character) for character in text if ord(character) >= 0x80})
output = ['#include "lvgl.h"']
for size in (14, 16, 20):
    font = ImageFont.truetype(str(font_path), size)
    bitmap = bytearray()
    descriptors = [(0, 0, 0, 0, 0, 0)]
    for codepoint in codepoints:
        character = chr(codepoint)
        left, top, right, bottom = font.getbbox(character, anchor="ls")
        width, height = right - left, bottom - top
        offset = len(bitmap)
        if width and height:
            image = Image.new("L", (width, height))
            ImageDraw.Draw(image).text((-left, -top), character, font=font, fill=255, anchor="ls")
            pixels = [int(value * 15 / 255 + 0.5) for value in image.tobytes()]
            if len(pixels) % 2:
                pixels.append(0)
            bitmap.extend((pixels[index] << 4) | pixels[index + 1] for index in range(0, len(pixels), 2))
        descriptors.append((offset, round(font.getlength(character) * 16), width, height, left, -bottom))
    prefix = f"ut_font_{size}"
    output.append(f"static const uint8_t {prefix}_bitmap[] = {{")
    output.extend("    " + ",".join(f"0x{value:02X}" for value in bitmap[index:index + 24]) + "," for index in range(0, len(bitmap), 24))
    output.append("};")
    output.append(f"static const lv_font_fmt_txt_glyph_dsc_t {prefix}_glyphs[] = {{")
    for offset, advance, width, height, left, bottom in descriptors:
        output.append(f"    {{.bitmap_index={offset},.adv_w={advance},.box_w={width},.box_h={height},.ofs_x={left},.ofs_y={bottom}}},")
    output.append("};")
    output.append(f"static const uint16_t {prefix}_unicode[] = {{" + ",".join(str(codepoint - 32) for codepoint in codepoints) + "};")
    output.append(f"static const lv_font_fmt_txt_cmap_t {prefix}_cmap[] = {{{{.range_start=32,.range_length={codepoints[-1] - 31},.glyph_id_start=1,.unicode_list={prefix}_unicode,.list_length={len(codepoints)},.type=LV_FONT_FMT_TXT_CMAP_SPARSE_TINY}}}};")
    output.append(f"static const lv_font_fmt_txt_dsc_t {prefix}_descriptor = {{.glyph_bitmap={prefix}_bitmap,.glyph_dsc={prefix}_glyphs,.cmaps={prefix}_cmap,.cmap_num=1,.bpp=4,.bitmap_format=0}};")
    output.append(f"const lv_font_t {prefix} = {{.get_glyph_dsc=lv_font_get_glyph_dsc_fmt_txt,.get_glyph_bitmap=lv_font_get_bitmap_fmt_txt,.line_height={size + 4},.base_line=4,.subpx=LV_FONT_SUBPX_NONE,.underline_position=-2,.underline_thickness=1,.dsc=&{prefix}_descriptor,.fallback=&lv_font_montserrat_14}};")
(ROOT / "assets" / "ut_font.c").write_text("\n".join(output) + "\n", encoding="utf-8")
(ROOT / "assets" / "ut_font.h").write_text('#ifndef UT_FONT_H\n#define UT_FONT_H\n#include "lvgl.h"\nLV_FONT_DECLARE(ut_font_14);\nLV_FONT_DECLARE(ut_font_16);\nLV_FONT_DECLARE(ut_font_20);\n#endif\n', encoding="utf-8")
print(f"Generated 14/16/20px antialiased CJK fonts, {len(codepoints)} characters each")
