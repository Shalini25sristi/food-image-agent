"""Processing report generation (Excel + CSV)."""

import logging
import os
from dataclasses import dataclass, field

import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["Food Item", "Image Found", "Image Processed", "Uploaded",
           "Source", "Status"]


@dataclass
class ReportRow:
    food_item: str
    image_found: str = "No"
    image_processed: str = "No"
    uploaded: str = "No"
    source: str = ""
    status: str = "Pending"

    def as_dict(self) -> dict:
        return {
            "Food Item": self.food_item,
            "Image Found": self.image_found,
            "Image Processed": self.image_processed,
            "Uploaded": self.uploaded,
            "Source": self.source,
            "Status": self.status,
        }


class Reporter:
    def __init__(self):
        self.rows: list[ReportRow] = []

    def add(self, row: ReportRow):
        self.rows.append(row)

    def save(self, path: str):
        df = pd.DataFrame([r.as_dict() for r in self.rows], columns=COLUMNS)
        df.to_excel(path, index=False)
        csv_path = os.path.splitext(path)[0] + ".csv"
        df.to_csv(csv_path, index=False)
        log.info("Report written to %s and %s", path, csv_path)

    def summary(self) -> str:
        total = len(self.rows)
        ok = sum(1 for r in self.rows if r.status.startswith("SUCCESS"))
        return f"{ok}/{total} item(s) completed successfully"
