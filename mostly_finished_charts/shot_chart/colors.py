"""Color math for shot charts.

Pure functions for hex/RGB conversion, contrast distance, and brand-preserving
color shifts (lighten colors that blend with the pitch or the dark background
instead of wholesale replacement with a fallback).
"""
from shared.styles import BG_COLOR


# Pitch color — the dark green shown behind shots. Used for contrast checks.
PITCH_COLOR = '#1E5631'
FALLBACK_COLOR = '#FFFFFF'  # White — last-resort fallback for low-contrast colors


def hex_to_rgb(hex_color):
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def color_distance(color1, color2):
    """Calculate Euclidean distance between two hex colors."""
    r1, g1, b1 = hex_to_rgb(color1)
    r2, g2, b2 = hex_to_rgb(color2)
    return ((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2) ** 0.5


def _lighten_hex(color, amount=70):
    """Shift each RGB channel up by `amount` (clamped to 255). Returns hex.

    Used to preserve brand identity for colors that blend with the pitch — shift
    them toward brightness instead of wholesale swapping to white.
    """
    r, g, b = hex_to_rgb(color)
    def clamp(v):
        return max(0, min(255, v + amount))
    return f"#{clamp(r):02X}{clamp(g):02X}{clamp(b):02X}"


def check_bg_contrast(color, min_ratio=3.5):
    """True if `color` is perceptually visible on the dark BG_COLOR.

    WCAG luminance, not RGB distance. The RGB version passed Everton's navy
    #00009E at distance 116 while it sat at ~1.1:1 luminance against #1A2332 -
    an invisible accent bar and an unreadable team name. Same defect class as
    the guard fixed in shared/colors.py (64ebf21): RGB distance does not track
    perceptual contrast, and this module carried its own local copy.
    Threshold 3.5 matches that fix, kept by prior user decision.
    """
    from shared.colors import wcag_contrast
    return wcag_contrast(color, BG_COLOR) >= min_ratio


def ensure_bg_readable(color):
    """Return a variant of `color` that reads clearly on the dark BG_COLOR.

    Preserves brand identity by lightening in HSL (hue and saturation held,
    lightness raised) until WCAG 3.5:1 - delegates to the shared guard so this
    module cannot drift from it again. Used for text that needs to sit on the
    dark navy background (e.g. team-name stat headers).
    """
    from shared.colors import ensure_line_contrast
    return ensure_line_contrast(color, BG_COLOR, min_ratio=3.5)


def ensure_pitch_contrast(color):
    """Return a variant of `color` that reads clearly on the dark-green pitch.

    Only shifts green-dominant colors close to pitch green (e.g. Sassuolo,
    Celtic, Werder). Dark non-green colors — black, navy, deep red, near-black
    grays — pass through unchanged: their hue difference from dark-green is
    enough to read clearly on the pitch.
    """
    r, g, b = hex_to_rgb(color)
    is_greenish = g > r and g > b * 0.8
    if is_greenish and color_distance(color, PITCH_COLOR) < 120:
        return _lighten_hex(color)
    return color
