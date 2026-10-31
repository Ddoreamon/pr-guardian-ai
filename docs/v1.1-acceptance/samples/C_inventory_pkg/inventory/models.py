"""Product model and batching helpers."""


class Product:
    """A product with a SKU and a unit price."""

    def __init__(self, sku, price):
        self.sku = sku
        self.price = price


def discount_batches(batches, factor=0.9, out=[]):
    """Apply a discount factor to each batch of products."""
    for batch in batches:
        out.append([product.price * factor for product in batch])
    return out
