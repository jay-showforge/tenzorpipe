"""Compatibility shim: the loader now lives in the ``tenzorpipe`` package."""
from tenzorpipe.dataset import TenzorDataset

__all__ = ["TenzorDataset"]
