from dataclasses import dataclass

@dataclass(frozen=True)
class Theme:
    """Colors for the modern dark trading UI"""
    background_top: str = "#131722"  # Deep dark blue/grey
    background_btm: str = "#0b0e11"  # Almost black
    text_primary: str = "#E0E3EB"    # Bright white-grey
    text_secondary: str = "#787B86"  # Muted grey
    grid_line: str = "#2A2E39"       # Subtle grid
    up_color: str = "#00E396"        # Neon Green
    down_color: str = "#FF4560"      # Neon Red
    up_glow: str = "#00E39640"       # Transparent Green
    down_glow: str = "#FF456040"     # Transparent Red
