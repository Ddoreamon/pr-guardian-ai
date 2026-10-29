"""Service layer: totals and report generation."""

import subprocess

from .models import Product, discount_batches  # noqa: F401 - re-exported


def total(products):
    """Return the sum of the prices."""
    return sum(Product(p.sku, p.price).price for p in products)


def discount_all(batches, factor=0.9):
    """Return discounted batches."""
    return discount_batches(batches, factor)


def region_report(path):
    """Run the reporting script for a region file."""
    return subprocess.run(["python", "report.py", path], check=False)
