"""Create example master and check drawings for testing the CAD comparison system."""

from PIL import Image, ImageDraw, ImageFont
import os

# Image dimensions
WIDTH, HEIGHT = 1200, 900
BG_COLOR = (255, 255, 255)
LINE_COLOR = (0, 0, 0)
DIM_COLOR = (0, 0, 200)
TITLE_COLOR = (80, 80, 80)

def get_font(size):
    """Try to get a reasonable font, fall back to default."""
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except:
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
        except:
            return ImageFont.load_default()

def draw_dimension_line(draw, x1, y1, x2, y2, value, unit="mm", font=None, offset=30):
    """Draw a dimension line with arrows and value."""
    # Determine if horizontal or vertical
    if abs(x2 - x1) > abs(y2 - y1):
        # Horizontal dimension
        mid_x = (x1 + x2) / 2
        y_line = y1 - offset

        # Extension lines
        draw.line([(x1, y1), (x1, y_line - 5)], fill=DIM_COLOR, width=1)
        draw.line([(x2, y2), (x2, y_line - 5)], fill=DIM_COLOR, width=1)

        # Dimension line
        draw.line([(x1, y_line), (x2, y_line)], fill=DIM_COLOR, width=1)

        # Arrows
        draw.polygon([(x1, y_line), (x1 + 8, y_line - 4), (x1 + 8, y_line + 4)], fill=DIM_COLOR)
        draw.polygon([(x2, y_line), (x2 - 8, y_line - 4), (x2 - 8, y_line + 4)], fill=DIM_COLOR)

        # Text
        text = f"{value} {unit}"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        draw.text((mid_x - text_width/2, y_line - 20), text, fill=DIM_COLOR, font=font)
    else:
        # Vertical dimension
        mid_y = (y1 + y2) / 2
        x_line = x1 + offset

        # Extension lines
        draw.line([(x1, y1), (x_line + 5, y1)], fill=DIM_COLOR, width=1)
        draw.line([(x2, y2), (x_line + 5, y2)], fill=DIM_COLOR, width=1)

        # Dimension line
        draw.line([(x_line, y1), (x_line, y2)], fill=DIM_COLOR, width=1)

        # Arrows
        draw.polygon([(x_line, y1), (x_line - 4, y1 + 8), (x_line + 4, y1 + 8)], fill=DIM_COLOR)
        draw.polygon([(x_line, y2), (x_line - 4, y2 - 8), (x_line + 4, y2 - 8)], fill=DIM_COLOR)

        # Text
        text = f"{value} {unit}"
        draw.text((x_line + 10, mid_y - 8), text, fill=DIM_COLOR, font=font)


def create_bracket_drawing(filename, dims, title, notes=""):
    """Create an L-bracket technical drawing with given dimensions."""
    img = Image.new('RGB', (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_small = get_font(14)
    font_med = get_font(18)
    font_large = get_font(24)

    # Title block
    draw.rectangle([(20, 20), (WIDTH-20, 80)], outline=LINE_COLOR, width=2)
    draw.text((40, 35), title, fill=TITLE_COLOR, font=font_large)
    draw.text((40, 60), "PART NO: BRK-001  |  MATERIAL: ALUMINUM 6061-T6  |  SCALE: 1:1", fill=TITLE_COLOR, font=font_small)

    # Draw the L-bracket shape
    # Main shape coordinates (centered)
    cx, cy = 500, 450

    # L-bracket dimensions from input
    width = dims['width']  # Overall width
    height = dims['height']  # Overall height
    thickness = dims['thickness']  # Material thickness
    hole_dia = dims['hole_diameter']
    hole_x = dims['hole_x']  # Hole center from left
    hole_y = dims['hole_y']  # Hole center from bottom

    # Scale for drawing (pixels per mm)
    scale = 3

    # L-bracket outline points
    x0 = cx - (width * scale) / 2
    y0 = cy + (height * scale) / 2

    points = [
        (x0, y0),  # Bottom left
        (x0 + width * scale, y0),  # Bottom right
        (x0 + width * scale, y0 - thickness * scale),  # Step up
        (x0 + thickness * scale, y0 - thickness * scale),  # Step left
        (x0 + thickness * scale, y0 - height * scale),  # Top of vertical
        (x0, y0 - height * scale),  # Top left
    ]

    # Draw the L-bracket
    draw.polygon(points, outline=LINE_COLOR, fill=(240, 240, 245))
    draw.polygon(points, outline=LINE_COLOR, width=3)

    # Draw hole
    hole_cx = x0 + hole_x * scale
    hole_cy = y0 - hole_y * scale
    hole_r = (hole_dia * scale) / 2
    draw.ellipse([(hole_cx - hole_r, hole_cy - hole_r),
                  (hole_cx + hole_r, hole_cy + hole_r)],
                 outline=LINE_COLOR, width=2, fill=BG_COLOR)

    # Center lines for hole
    draw.line([(hole_cx - hole_r - 10, hole_cy), (hole_cx + hole_r + 10, hole_cy)],
              fill=LINE_COLOR, width=1)
    draw.line([(hole_cx, hole_cy - hole_r - 10), (hole_cx, hole_cy + hole_r + 10)],
              fill=LINE_COLOR, width=1)

    # Dimensions
    # Overall width (bottom)
    draw_dimension_line(draw, x0, y0, x0 + width * scale, y0, width, "mm", font_med, offset=-40)

    # Overall height (left side)
    draw_dimension_line(draw, x0, y0, x0, y0 - height * scale, height, "mm", font_med, offset=-50)

    # Thickness horizontal
    draw_dimension_line(draw, x0, y0 - thickness * scale - 60,
                        x0 + thickness * scale, y0 - thickness * scale - 60,
                        thickness, "mm", font_med, offset=20)

    # Hole diameter
    draw.text((hole_cx + hole_r + 15, hole_cy - 25), f"⌀{hole_dia} mm", fill=DIM_COLOR, font=font_med)

    # Hole position from left edge
    draw_dimension_line(draw, x0, y0 + 50, hole_cx, y0 + 50, hole_x, "mm", font_med, offset=-20)

    # Hole position from bottom
    y_bottom = y0
    draw.text((hole_cx + 40, hole_cy + 5), f"↑ {hole_y} mm from base", fill=DIM_COLOR, font=font_small)

    # Tolerances box
    tol_x, tol_y = 850, 150
    draw.rectangle([(tol_x, tol_y), (WIDTH - 40, tol_y + 120)], outline=LINE_COLOR, width=1)
    draw.text((tol_x + 10, tol_y + 10), "GENERAL TOLERANCES:", fill=TITLE_COLOR, font=font_med)
    draw.text((tol_x + 10, tol_y + 35), "Linear: ±0.1 mm", fill=LINE_COLOR, font=font_small)
    draw.text((tol_x + 10, tol_y + 55), "Angular: ±0.5°", fill=LINE_COLOR, font=font_small)
    draw.text((tol_x + 10, tol_y + 75), "Holes: +0.05/-0.00 mm", fill=LINE_COLOR, font=font_small)
    draw.text((tol_x + 10, tol_y + 95), "Surface: Ra 1.6", fill=LINE_COLOR, font=font_small)

    # Notes
    if notes:
        draw.text((40, HEIGHT - 60), f"NOTES: {notes}", fill=(150, 50, 50), font=font_med)

    # Revision block
    draw.rectangle([(WIDTH - 200, HEIGHT - 80), (WIDTH - 20, HEIGHT - 20)], outline=LINE_COLOR, width=1)
    draw.text((WIDTH - 190, HEIGHT - 70), "REV: A", fill=TITLE_COLOR, font=font_small)
    draw.text((WIDTH - 190, HEIGHT - 50), "DATE: 2024-01-15", fill=TITLE_COLOR, font=font_small)

    img.save(filename, 'PNG', dpi=(150, 150))
    print(f"Created: {filename}")


# Master drawing - the specification
master_dims = {
    'width': 100,
    'height': 75,
    'thickness': 15,
    'hole_diameter': 10,
    'hole_x': 50,
    'hole_y': 37.5,
}

# Check drawing - manufactured part with slight deviations
check_dims = {
    'width': 100.2,      # Slightly over
    'height': 74.8,      # Slightly under
    'thickness': 15.1,   # Slightly over
    'hole_diameter': 10.05,  # Within tolerance
    'hole_x': 49.7,      # Slightly off center
    'hole_y': 37.3,      # Slightly low
}

output_dir = "/Users/noelleso/Downloads/cad-main"

create_bracket_drawing(
    os.path.join(output_dir, "master_bracket.png"),
    master_dims,
    "MASTER DRAWING - L-BRACKET ASSEMBLY",
)

create_bracket_drawing(
    os.path.join(output_dir, "check_bracket.png"),
    check_dims,
    "CHECK DRAWING - L-BRACKET (AS-MANUFACTURED)",
    notes="Inspection sample from production batch #2024-0115"
)

print("\n✓ Test drawings created successfully!")
print(f"  Master: {output_dir}/master_bracket.png")
print(f"  Check:  {output_dir}/check_bracket.png")
print("\nDifferences between master and check:")
print("  - Width: 100 mm → 100.2 mm (+0.2 mm deviation)")
print("  - Height: 75 mm → 74.8 mm (-0.2 mm deviation)")
print("  - Thickness: 15 mm → 15.1 mm (+0.1 mm deviation)")
print("  - Hole diameter: 10 mm → 10.05 mm (+0.05 mm, within +0.05 tolerance)")
print("  - Hole X position: 50 mm → 49.7 mm (-0.3 mm deviation)")
print("  - Hole Y position: 37.5 mm → 37.3 mm (-0.2 mm deviation)")
