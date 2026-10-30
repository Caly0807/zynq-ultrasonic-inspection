from pathlib import Path
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
paths = sorted((ROOT / "build" / "desktop").glob("preview_*.bmp"))
images = [Image.open(path).convert("RGB") for path in paths]
atlas = Image.new("RGB", (800, 480 * len(images)))
for number, image in enumerate(images):
    atlas.paste(image, (0, number * 480))
quantized = atlas.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
palette = np.asarray([(11, 18, 28)] + [tuple(quantized.getpalette()[offset:offset + 3]) for offset in range(0, 255 * 3, 3)], dtype=np.int32)
indices = np.arange(65536, dtype=np.uint32)
colors = np.stack((((indices >> 11) & 31) * 255 // 31, ((indices >> 5) & 63) * 255 // 63, (indices & 31) * 255 // 31), axis=1).astype(np.int32)
lookup = np.empty(65536, dtype=np.uint8)
for start in range(0, 65536, 1024):
    distances = colors[start:start + 1024, None, :] - palette[None, :, :]
    squared = (distances * distances * np.asarray([2, 3, 1])).sum(axis=2)
    lookup[start:start + 1024] = squared.argmin(axis=1)
lines = ['#include "ut_palette.h"', 'const uint32_t ut_palette[256] = {']
lines.extend(f'0x{red:02X}{green:02X}{blue:02X},' for red, green, blue in palette)
lines.append('};\nconst uint8_t ut_rgb565_to_index[65536] = {')
lines.extend(','.join(str(value) for value in lookup[start:start + 32]) + ',' for start in range(0, 65536, 32))
lines.append('};')
(ROOT / 'assets' / 'ut_palette.c').write_text('\n'.join(lines) + '\n', encoding='utf-8')
(ROOT / 'assets' / 'ut_palette.h').write_text('#ifndef UT_PALETTE_H\n#define UT_PALETTE_H\n#include <stdint.h>\nextern const uint32_t ut_palette[256];\nextern const uint8_t ut_rgb565_to_index[65536];\n#endif\n', encoding='utf-8')
(ROOT.parents[1] / 'fpga' / 'lvgl_palette.mem').write_text('\n'.join(f'{red:02X}{green:02X}{blue:02X}' for red, green, blue in palette) + '\n', encoding='ascii')
for path, image in zip(paths, images):
    pixels = np.asarray(image, dtype=np.uint32)
    codes = ((pixels[:, :, 0] >> 3) << 11) | ((pixels[:, :, 1] >> 2) << 5) | (pixels[:, :, 2] >> 3)
    restored = palette[lookup[codes]].astype(np.uint8)
    Image.fromarray(restored).save(path.with_name(path.stem + '_panel.png'))
print('Generated 256-color PL palette and PS conversion table.')