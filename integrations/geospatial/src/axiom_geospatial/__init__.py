"""Optional geospatial adapter. Importing it does not import GIS/native engines."""
from .ngii import NgiiImportOptions, capabilities, inspect_ngii_zip

__all__ = ["NgiiImportOptions", "capabilities", "inspect_ngii_zip"]
