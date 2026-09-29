#!/usr/bin/env python3
"""
scripts/generate_app_icon.py
Generates a high-resolution, industrial-grade macOS App Icon for Vision AI Studio
conforming to Apple Human Interface Guidelines and Cognex/Keyence aesthetic.
Generates build/icon.icns and build/icon.png.
"""

import math
import os
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

def create_icon_1024():
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. macOS Squircle Bounds (Apple 1024x1024 icon grid standard: 824x824 inner area, r=185)
    pad = 100
    box = [pad, pad, size - pad, size - pad]
    radius = 185

    # Base chassis shadow
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)
    s_draw.rounded_rectangle([pad, pad + 15, size - pad, size - pad + 15], radius=radius, fill=(0, 0, 0, 160))
    shadow = shadow.filter(ImageFilter.GaussianBlur(25))
    img.paste(shadow, (0, 0), shadow)

    # Base chassis plate (Dark steel / Cognex charcoal #0B0E14 ~ #131822)
    chassis = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(chassis)
    
    # Layered gradient simulation
    for i in range(pad, size - pad):
        factor = (i - pad) / (size - 2 * pad)
        r = int(11 + factor * 12)
        g = int(14 + factor * 14)
        b = int(20 + factor * 18)
        c_draw.line([(pad, i), (size - pad, i)], fill=(r, g, b, 255))

    # Mask to rounded rectangle
    mask = Image.new("L", (size, size), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle(box, radius=radius, fill=255)
    img.paste(chassis, (0, 0), mask)

    # Hairline Chamfer Bezel (1px precision border #2B3547 & top highlight #3B82F6)
    draw.rounded_rectangle(box, radius=radius, outline=(43, 53, 71, 255), width=4)
    draw.rounded_rectangle([pad + 3, pad + 3, size - pad - 3, size - pad - 3], radius=radius - 3, outline=(20, 26, 38, 255), width=2)

    # 2. Industrial Optical Lens Barrel (Keyence / Cognex telecentric lens motif)
    center_x, center_y = size // 2, size // 2

    # Outer metallic knurled ring
    outer_r = 310
    draw.ellipse([center_x - outer_r, center_y - outer_r, center_x + outer_r, center_y + outer_r],
                 fill=(22, 28, 40, 255), outline=(59, 74, 100, 255), width=6)

    # Knurling notches around circumference
    num_notches = 48
    for i in range(num_notches):
        angle = (2 * math.pi / num_notches) * i
        nx1 = center_x + (outer_r - 12) * math.cos(angle)
        ny1 = center_y + (outer_r - 12) * math.sin(angle)
        nx2 = center_x + outer_r * math.cos(angle)
        ny2 = center_y + outer_r * math.sin(angle)
        draw.line([(nx1, ny1), (nx2, ny2)], fill=(75, 95, 125, 200), width=3)

    # Mid ring (dark anodized aluminum #0F172A)
    mid_r = 275
    draw.ellipse([center_x - mid_r, center_y - mid_r, center_x + mid_r, center_y + mid_r],
                 fill=(15, 23, 42, 255), outline=(30, 41, 59, 255), width=4)

    # Inner lens element (deep optic dark navy #020617)
    lens_r = 230
    draw.ellipse([center_x - lens_r, center_y - lens_r, center_x + lens_r, center_y + lens_r],
                 fill=(2, 6, 23, 255), outline=(16, 185, 129, 120), width=3)

    # Optical coating glass reflection (subtle cyan/emerald arc)
    refl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    r_draw = ImageDraw.Draw(refl)
    r_draw.arc([center_x - lens_r + 15, center_y - lens_r + 15, center_x + lens_r - 15, center_y + lens_r - 15],
               start=195, end=275, fill=(16, 185, 129, 140), width=6)
    r_draw.arc([center_x - lens_r + 28, center_y - lens_r + 28, center_x + lens_r - 28, center_y + lens_r - 28],
               start=205, end=265, fill=(56, 189, 248, 100), width=3)
    refl = refl.filter(ImageFilter.GaussianBlur(2))
    img.paste(refl, (0, 0), refl)

    # 3. Machine Vision Inspection Reticle & Crosshair Grid
    # 4 corner bounding brackets (Cognex ROI bracket style)
    b_len = 50
    b_r = 160
    # Top-Left
    draw.line([(center_x - b_r, center_y - b_r), (center_x - b_r + b_len, center_y - b_r)], fill=(16, 185, 129, 230), width=4)
    draw.line([(center_x - b_r, center_y - b_r), (center_x - b_r, center_y - b_r + b_len)], fill=(16, 185, 129, 230), width=4)
    # Top-Right
    draw.line([(center_x + b_r, center_y - b_r), (center_x + b_r - b_len, center_y - b_r)], fill=(16, 185, 129, 230), width=4)
    draw.line([(center_x + b_r, center_y - b_r), (center_x + b_r, center_y - b_r + b_len)], fill=(16, 185, 129, 230), width=4)
    # Bottom-Left
    draw.line([(center_x - b_r, center_y + b_r), (center_x - b_r + b_len, center_y + b_r)], fill=(16, 185, 129, 230), width=4)
    draw.line([(center_x - b_r, center_y + b_r), (center_x - b_r, center_y + b_r - b_len)], fill=(16, 185, 129, 230), width=4)
    # Bottom-Right
    draw.line([(center_x + b_r, center_y + b_r), (center_x + b_r - b_len, center_y + b_r)], fill=(16, 185, 129, 230), width=4)
    draw.line([(center_x + b_r, center_y + b_r), (center_x + b_r, center_y + b_r - b_len)], fill=(16, 185, 129, 230), width=4)

    # Subpixel crosshair reticle (with gap in center for the icon symbol)
    c_gap = 90
    draw.line([(center_x - lens_r + 20, center_y), (center_x - c_gap, center_y)], fill=(51, 65, 85, 200), width=2)
    draw.line([(center_x + c_gap, center_y), (center_x + lens_r - 20, center_y)], fill=(51, 65, 85, 200), width=2)
    draw.line([(center_x, center_y - lens_r + 20), (center_x, center_y - c_gap)], fill=(51, 65, 85, 200), width=2)
    draw.line([(center_x, center_y + c_gap), (center_x, center_y + lens_r - 20)], fill=(51, 65, 85, 200), width=2)

    # 4. Center Geometric Emblem: Bold Industrial "V" (Vision + AI Nodes)
    # Polygon coordinates for crisp faceted V
    v_top_y = center_y - 85
    v_bottom_y = center_y + 85
    v_left_x = center_x - 90
    v_right_x = center_x + 90
    v_thick = 36

    # Shadow for V
    v_shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    vs_draw = ImageDraw.Draw(v_shadow)
    vs_draw.polygon([
        (v_left_x, v_top_y + 8),
        (v_left_x + v_thick, v_top_y + 8),
        (center_x, v_bottom_y - 20 + 8),
        (v_right_x - v_thick, v_top_y + 8),
        (v_right_x, v_top_y + 8),
        (center_x + 18, v_bottom_y + 8),
        (center_x - 18, v_bottom_y + 8),
    ], fill=(0, 0, 0, 180))
    v_shadow = v_shadow.filter(ImageFilter.GaussianBlur(10))
    img.paste(v_shadow, (0, 0), v_shadow)

    # Primary "V" in crisp Emerald / Cyan Gradient simulation
    # Left stem (Emerald #10B981)
    draw.polygon([
        (v_left_x, v_top_y),
        (v_left_x + v_thick, v_top_y),
        (center_x, v_bottom_y - 22),
        (center_x - 18, v_bottom_y),
    ], fill=(16, 185, 129, 255))

    # Right stem (Electric Cyan/Sapphire #0284C7)
    draw.polygon([
        (v_right_x, v_top_y),
        (v_right_x - v_thick, v_top_y),
        (center_x, v_bottom_y - 22),
        (center_x + 18, v_bottom_y),
    ], fill=(14, 165, 233, 255))

    # Center Apex Node (High precision gold/white focal point)
    draw.ellipse([center_x - 16, v_bottom_y - 28, center_x + 16, v_bottom_y + 4],
                 fill=(248, 250, 252, 255), outline=(16, 185, 129, 255), width=3)

    # 5. Top Status Annunciator LED (Keyence hardware style: Emerald Green = ONLINE/PASS)
    led_x, led_y = center_x, pad + 45
    led_r = 14
    # Bezel
    draw.ellipse([led_x - led_r - 4, led_y - led_r - 4, led_x + led_r + 4, led_y + led_r + 4],
                 fill=(20, 25, 35, 255), outline=(71, 85, 105, 255), width=2)
    # Glow
    led_glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    lg_draw = ImageDraw.Draw(led_glow)
    lg_draw.ellipse([led_x - led_r * 2, led_y - led_r * 2, led_x + led_r * 2, led_y + led_r * 2],
                    fill=(16, 185, 129, 100))
    led_glow = led_glow.filter(ImageFilter.GaussianBlur(8))
    img.paste(led_glow, (0, 0), led_glow)
    # Core
    draw.ellipse([led_x - led_r, led_y - led_r, led_x + led_r, led_y + led_r],
                 fill=(16, 185, 129, 255), outline=(167, 243, 208, 255), width=2)

    return img

def main():
    root = Path(__file__).resolve().parent.parent
    build_dir = root / "build"
    build_dir.mkdir(exist_ok=True)

    icon_img = create_icon_1024()
    png_path = build_dir / "icon.png"
    icon_img.save(png_path, "PNG")
    print(f"[1/3] Generated 1024x1024 master icon: {png_path}")

    # Generate macOS iconset
    iconset_dir = build_dir / "icon.iconset"
    iconset_dir.mkdir(exist_ok=True)

    sizes = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]

    for px, filename in sizes:
        resized = icon_img.resize((px, px), Image.Resampling.LANCZOS)
        resized.save(iconset_dir / filename, "PNG")

    print(f"[2/3] Generated all 10 iconset resolutions in {iconset_dir}")

    # Convert to icns using macOS native iconutil
    icns_path = build_dir / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)], check=True)
    print(f"[3/3] Successfully generated native Apple ICNS: {icns_path}")

if __name__ == "__main__":
    main()
