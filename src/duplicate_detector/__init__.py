"""Local Duplicate Detector: find duplicate files and choose which copies to delete."""

from .scanner import FileRecord, Progress, ScanCancelled, ScanResult, SimilarPair, scan

__version__ = "0.2.0"

__all__ = [
    "FileRecord",
    "Progress",
    "ScanCancelled",
    "ScanResult",
    "SimilarPair",
    "__version__",
    "scan",
]
