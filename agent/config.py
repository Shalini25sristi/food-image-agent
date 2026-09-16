"""Central configuration for the food image agent."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Input
    excel_path: str = "sample_input.xlsx"
    column: str = "Food Item"

    # Output
    output_dir: str = "output"
    report_path: str = "processing_report.xlsx"

    # Image specifications (per assignment)
    target_width: int = 1800
    target_height: int = 1200
    max_file_size_mb: float = 10.0

    # Search / validation behaviour
    max_candidates: int = 6          # how many candidate images to try per food item
    min_width: int = 800             # reject images smaller than this
    min_height: int = 533
    blur_threshold: float = 80.0     # Laplacian variance below this => blurry
    download_timeout: int = 25
    max_download_mb: int = 30
    best_effort: bool = True         # if no image passes validation, use the best one anyway

    # API keys (optional - DuckDuckGo search works without any key)
    serpapi_key: str = field(default_factory=lambda: os.getenv("SERPAPI_KEY", ""))
    google_cse_key: str = field(default_factory=lambda: os.getenv("GOOGLE_CSE_KEY", ""))
    google_cse_cx: str = field(default_factory=lambda: os.getenv("GOOGLE_CSE_CX", ""))

    # Google Drive
    drive_folder_id: str = field(default_factory=lambda: os.getenv("DRIVE_FOLDER_ID", ""))
    drive_credentials: str = field(
        default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
    )
    dry_run: bool = False            # True => never upload to Drive, save locally only
