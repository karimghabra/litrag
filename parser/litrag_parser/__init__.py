"""litrag-parser: Docling into one node tree, spoken to over JSON lines."""

import os

# ONNX Runtime — RapidOCR's engine, in the chart reader and in Docling's OCR — sends usage events to
# Microsoft (mobile.events.data.microsoft.com) unless told not to, and invariant 1 allows no such
# call. Set here, before it is first loaded, so the worker, Docling's child and every test inherit it.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

__version__ = "0.3.2"
